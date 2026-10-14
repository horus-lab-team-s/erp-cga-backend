"""La lecture automatique d'une facture par Claude : la réalisation de `ServiceExtraction`.

─────────────────────────────────────────────────────────────────────────────────
DÉCISION DU CABINET, 14 OCTOBRE 2026 : Claude (vision) lit les factures chargées,
et un collaborateur confirme toujours les montants et le NIU avant l'écriture.

CE QUE CET ADAPTATEUR FAIT, ET RIEN DE PLUS

Il envoie le document (PDF, JPEG ou PNG) et rend une `ExtractionOCR` : chaque
champ avec la valeur lue et un indice de confiance. Il ne retient rien, n'écrit
rien, ne décide rien : `ValeurExtraite` impose déjà qu'un champ sensible soit
validé par un humain quel que soit son score.

⚠️ IL NE LÈVE PAS. Clé absente, réseau coupé, limite de débit, refus, réponse
illisible : l'extraction revient avec un `incident` et sans champ, et l'écran
propose la saisie à la main. Une lecture automatique en panne ne doit jamais
empêcher de traiter une pièce.

⚠️ LE DOCUMENT SORT DU SERVEUR. C'est la raison pour laquelle la clé est un
réglage propre (`CGA_ANTHROPIC_CLE_API`), et non la variable par défaut du SDK :
voir `infrastructure/config.py`.

CHOIX D'APPEL

* `claude-opus-5-5`, effort `medium` : une lecture fausse coûte une écriture
  fausse, mais la tâche est une extraction, pas un raisonnement long ;
* sortie structurée par schéma JSON strict : la réponse se valide, elle ne se
  devine pas dans un texte libre ;
* `fallbacks: "default"` : si la lecture est déclinée par un filtre de sécurité,
  l'API la relance sur le modèle de repli recommandé, dans le même appel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.contextes.collecte.domaine.extraction import ExtractionOCR, ValeurExtraite
from app.contextes.collecte.domaine.pieces import TypePiece
from app.partage.horloge import maintenant
from app.partage.magasin_de_fichiers import detecter_le_type

__all__ = ["ExtractionClaude", "SCHEMA_DE_LECTURE"]

_journal = logging.getLogger(__name__)

#: Ce que le navigateur de l'API sait lire. Le TIFF n'y est pas : il se saisit à la main.
_IMAGES = {"image/jpeg", "image/png"}

_CONSIGNE = """\
Tu lis des factures fournisseurs reçues par un cabinet d'expertise comptable au Cameroun, \
pour préparer leur comptabilisation. Le document joint est une photographie ou un PDF.

Relève les valeurs TELLES QU'IMPRIMÉES sur le document :
- emetteur : le nom du fournisseur qui émet la facture ;
- niu_emetteur : son Numéro d'Identifiant Unique (NIU), en général une lettre, douze chiffres \
et une lettre (exemple : M081234567890P), sans espaces ;
- reference : le numéro de la facture ;
- date_emission : la date de la facture, au format AAAA-MM-JJ ;
- lignes : chaque ligne de prestation ou d'article avec sa désignation et son montant hors taxes ;
- montant_ht, montant_tva, montant_ttc : les totaux, en francs CFA, nombres entiers.

Règles :
- Si une valeur n'est pas imprimée, est illisible ou ambiguë, rends null avec une confiance de 0. \
N'invente jamais une valeur, et ne la calcule pas à partir des autres : un collaborateur \
la complètera.
- La confiance (entre 0 et 1) dit à quel point tu es certain d'avoir lu exactement ce qui est \
imprimé. Baisse-la pour un chiffre flou, une écriture manuscrite, un document coupé.
- est_une_facture vaut false si le document n'est pas une facture (reçu de caisse sans détail, \
relevé, contrat, photo sans rapport) ; remplis alors ce que tu peux et baisse les confiances.
"""


#: Ce que lit le collaborateur quand la lecture automatique ne peut pas servir.
_INCIDENTS = {
    "FORMAT": "Ce format ne se lit pas automatiquement (TIFF) : saisissez la facture.",
    "SATURE": "Lecture automatique saturée : réessayez dans une minute, ou saisissez la facture.",
    "DELAI": "La lecture automatique n'a pas répondu à temps : saisissez la facture.",
    "RESEAU": "Lecture automatique injoignable : saisissez la facture.",
    "REFUS": "La lecture automatique a décliné ce document : saisissez la facture.",
    "LONGUE": "La facture est trop longue pour une lecture automatique : saisissez-la.",
    "ILLISIBLE": "Réponse de lecture illisible : saisissez la facture.",
}


def _champ(type_valeur: Any) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["valeur", "confiance"],
        "properties": {"valeur": {"type": type_valeur}, "confiance": {"type": "number"}},
    }


#: La forme de la réponse, imposée à l'API : chaque champ porte sa valeur et sa confiance.
SCHEMA_DE_LECTURE: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "est_une_facture",
        "emetteur",
        "niu_emetteur",
        "reference",
        "date_emission",
        "lignes",
        "montant_ht",
        "montant_tva",
        "montant_ttc",
    ],
    "properties": {
        "est_une_facture": {"type": "boolean"},
        "emetteur": _champ(["string", "null"]),
        "niu_emetteur": _champ(["string", "null"]),
        "reference": _champ(["string", "null"]),
        "date_emission": _champ(["string", "null"]),
        "lignes": {
            "type": "object",
            "additionalProperties": False,
            "required": ["valeur", "confiance"],
            "properties": {
                "valeur": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["designation", "montant_ht"],
                        "properties": {
                            "designation": {"type": "string"},
                            "montant_ht": {"type": ["number", "null"]},
                        },
                    },
                },
                "confiance": {"type": "number"},
            },
        },
        "montant_ht": _champ(["number", "null"]),
        "montant_tva": _champ(["number", "null"]),
        "montant_ttc": _champ(["number", "null"]),
    },
}

#: Les champs de la réponse, sous le nom que porte le reste du produit.
_CHAMPS = {
    "emetteur": "emetteur",
    "niu_emetteur": "niu_emetteur",
    "reference": "reference_document",
    "date_emission": "date_emission",
    "montant_ht": "montant_ht",
    "montant_tva": "montant_tva",
    "montant_ttc": "montant_ttc",
    "lignes": "lignes",
}


class ExtractionClaude:
    """`ServiceExtraction` par l'API d'Anthropic. `client` s'injecte pour les essais."""

    MOTEUR = "claude"

    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=cle_api,
                # Une page se lit en quelques secondes ; au-delà de deux minutes, le
                # collaborateur est mieux servi par la saisie que par une attente.
                timeout=anthropic.Timeout(120.0, connect=10.0),
                max_retries=2,
            )
        self._client = client

    def extraire(self, contenu: bytes, *, piece: str, type_attendu: TypePiece) -> ExtractionOCR:
        instant = maintenant()
        vide = ExtractionOCR(piece=piece, moteur=self.MOTEUR, extraite_le=instant)
        type_mime = detecter_le_type(contenu)
        if type_mime == "application/pdf":
            bloc = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": type_mime,
                    "data": base64.standard_b64encode(contenu).decode(),
                },
            }
        elif type_mime in _IMAGES:
            bloc = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": type_mime,
                    "data": base64.standard_b64encode(contenu).decode(),
                },
            }
        else:
            return vide.model_copy(update={"incident": _INCIDENTS["FORMAT"]})

        import anthropic

        try:
            reponse = self._client.beta.messages.create(
                model=self._modele,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=_CONSIGNE,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": SCHEMA_DE_LECTURE},
                },
                messages=[
                    {
                        "role": "user",
                        "content": [
                            bloc,
                            {"type": "text", "text": "Relève les valeurs de cette facture."},
                        ],
                    }
                ],
            )
        except anthropic.RateLimitError:
            return vide.model_copy(update={"incident": _INCIDENTS["SATURE"]})
        except anthropic.APITimeoutError:
            return vide.model_copy(update={"incident": _INCIDENTS["DELAI"]})
        except anthropic.APIConnectionError:
            return vide.model_copy(update={"incident": _INCIDENTS["RESEAU"]})
        except anthropic.APIStatusError as echec:
            _journal.warning(
                "Lecture de %s refusée par l'API : %s %s", piece, echec.status_code, echec.message
            )
            return vide.model_copy(
                update={
                    "incident": (
                        f"Lecture automatique indisponible ({echec.status_code}) : "
                        "saisissez la facture."
                    )
                }
            )

        if reponse.stop_reason == "refusal":
            return vide.model_copy(update={"incident": _INCIDENTS["REFUS"]})
        if reponse.stop_reason == "max_tokens":
            return vide.model_copy(update={"incident": _INCIDENTS["LONGUE"]})
        texte = next((b.text for b in reponse.content if getattr(b, "type", None) == "text"), None)
        try:
            lu = json.loads(texte or "")
        except json.JSONDecodeError:
            return vide.model_copy(update={"incident": _INCIDENTS["ILLISIBLE"]})
        return vide.model_copy(update={"champs": _champs(lu, instant)})


def _champs(lu: dict, instant) -> dict[str, ValeurExtraite]:
    """La réponse, en `ValeurExtraite`. Une valeur mal formée tombe à confiance zéro."""
    champs: dict[str, ValeurExtraite] = {}
    plafond = Decimal(1) if lu.get("est_une_facture", True) else Decimal("0.5")
    for source, champ in _CHAMPS.items():
        bloc = lu.get(source) or {}
        valeur = bloc.get("valeur")
        try:
            confiance = min(max(Decimal(str(bloc.get("confiance", 0))), Decimal(0)), plafond)
        except InvalidOperation:
            confiance = Decimal(0)
        valeur, lisible = _normaliser(champ, valeur)
        if not lisible:
            confiance = Decimal(0)
        champs[champ] = ValeurExtraite(champ=champ, valeur_lue=valeur, confiance=confiance)
    return champs


def _normaliser(champ: str, valeur: Any) -> tuple[Any, bool]:
    """Ramène la valeur à la forme du produit ; `False` si elle n'en a pas."""
    if valeur is None:
        return None, False
    if champ in ("montant_ht", "montant_tva", "montant_ttc"):
        try:
            return str(Decimal(str(valeur)).quantize(Decimal(1))), True
        except InvalidOperation:
            return None, False
    if champ == "date_emission":
        try:
            return date.fromisoformat(str(valeur)).isoformat(), True
        except ValueError:
            return None, False
    if champ == "niu_emetteur":
        return str(valeur).replace(" ", "").upper(), True
    if champ == "lignes":
        lignes = []
        for ligne in valeur if isinstance(valeur, list) else []:
            try:
                montant = str(Decimal(str(ligne.get("montant_ht"))).quantize(Decimal(1)))
            except (InvalidOperation, TypeError):
                montant = None
            lignes.append(
                {"designation": str(ligne.get("designation", "")).strip(), "montant_ht": montant}
            )
        return lignes, bool(lignes)
    return str(valeur).strip(), bool(str(valeur).strip())
