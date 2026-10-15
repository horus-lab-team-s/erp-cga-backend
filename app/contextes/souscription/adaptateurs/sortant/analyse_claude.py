"""La lecture d'un contrat par Claude : des propositions, jamais des décisions.

─────────────────────────────────────────────────────────────────────────────────
DÉCISION DU CABINET (14-15 octobre 2026) : les prestations ponctuelles (analyse et
rédaction de contrats) se font « avec des outils et un agent, l'humain dans la
boucle ». Cet adaptateur est l'agent de l'analyse : il lit le contrat et rend des
PROPOSITIONS structurées. Il ne retient rien, n'écrit rien chez le client : c'est
`Mission.decider` qui fait d'une proposition un point du livrable, et seulement par
la main d'un juriste.

⚠️ IL NE LÈVE PAS (même règle que `collecte/.../extraction_claude.py`). Clé absente,
réseau coupé, refus, réponse illisible : la lecture revient avec un `incident` et
sans proposition, et le juriste instruit la mission à la main.

⚠️ LE DOCUMENT SORT DU SERVEUR, vers Anthropic, et seulement si
`CGA_ANTHROPIC_CLE_API` est posée. La politique de confidentialité du cabinet doit
le dire au client.

CHOIX D'APPEL

* `claude-opus-5-5`, pensée adaptative, effort `high` : une analyse juridique est un
  raisonnement, pas une extraction ;
* en flux (`messages.stream` + `get_final_message`) : un contrat long et une réponse
  longue dépasseraient les délais d'un appel simple ;
* sortie structurée par schéma JSON strict : la réponse se valide, elle ne se devine
  pas dans un texte ;
* `fallbacks: "default"` : un refus d'un filtre relance la lecture sur le modèle de
  repli recommandé, dans le même appel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.partage.magasin_de_fichiers import detecter_le_type

__all__ = ["AnalyseClaude", "LectureDeContrat", "SCHEMA_D_ANALYSE"]

_journal = logging.getLogger(__name__)

_IMAGES = {"image/jpeg", "image/png"}

_CONSIGNE = """\
Tu assistes le juriste d'un cabinet d'expertise comptable et juridique au Cameroun \
(espace OHADA). Il te confie un contrat pour en préparer l'analyse. Tu PROPOSES ; il \
décide, point par point, de ce qui ira au client.

Relève, en français juridique clair :
- RESUME : l'objet du contrat en quelques phrases (un seul point) ;
- PARTIES : qui s'engage envers qui, avec leur qualité (un point par partie) ;
- OBLIGATION : les obligations essentielles de chaque partie (prix, délais, livraison, \
non-concurrence, confidentialité…) ;
- CLAUSE_A_RISQUE : les clauses déséquilibrées, ambiguës ou dangereuses pour le client \
du cabinet (pénalités, résiliation unilatérale, limitation de responsabilité, \
juridiction, reconduction tacite…), avec une gravité FAIBLE, MOYENNE ou ELEVEE ;
- MANQUE : ce qui devrait figurer et ne figure pas (clause de règlement des litiges, \
force majeure, révision du prix, protection des données…) ;
- RECOMMANDATION : ce que le juriste pourrait conseiller (renégocier, préciser, ajouter).

Règles :
- Pour chaque point qui repose sur le texte, cite l'extrait MOT POUR MOT dans \
`citation` ; s'il n'y a pas d'extrait (un manque), laisse `citation` à null.
- N'invente aucune clause, aucun article de loi. Si tu évoques le droit OHADA ou \
camerounais, reste général et signale que la référence est à vérifier par le juriste.
- Si le document n'est pas un contrat ou est illisible, dis-le dans un seul point \
RESUME et ne rends rien d'autre.
"""

_INCIDENTS = {
    "FORMAT": "Ce format ne se lit pas automatiquement : instruisez la mission à la main.",
    "SATURE": "Lecture automatique saturée : réessayez dans une minute, ou instruisez à la main.",
    "DELAI": "La lecture automatique n'a pas répondu à temps : instruisez à la main.",
    "RESEAU": "Lecture automatique injoignable : instruisez la mission à la main.",
    "REFUS": "La lecture automatique a décliné ce document : instruisez la mission à la main.",
    "LONGUE": "Le contrat est trop long pour une lecture automatique : instruisez à la main.",
    "ILLISIBLE": "Réponse de lecture illisible : instruisez la mission à la main.",
}

SCHEMA_D_ANALYSE: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["points"],
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rubrique", "titre", "texte", "citation", "gravite"],
                "properties": {
                    "rubrique": {
                        "type": "string",
                        "enum": [
                            "RESUME",
                            "PARTIES",
                            "OBLIGATION",
                            "CLAUSE_A_RISQUE",
                            "MANQUE",
                            "RECOMMANDATION",
                        ],
                    },
                    "titre": {"type": "string"},
                    "texte": {"type": "string"},
                    "citation": {"type": ["string", "null"]},
                    "gravite": {"type": ["string", "null"]},
                },
            },
        }
    },
}


class LectureDeContrat:
    """Ce que rend une lecture : des propositions (dictionnaires prêts pour
    `Mission.ajouter`), ou un incident."""

    def __init__(self, *, propositions: list[dict] | None = None, incident: str | None = None):
        self.propositions = propositions or []
        self.incident = incident


class AnalyseClaude:
    """L'agent d'analyse de contrat. `client` s'injecte pour les essais."""

    MOTEUR = "claude"

    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=cle_api,
                # Un contrat de quelques pages se lit en une à deux minutes ; au-delà de
                # cinq, le juriste est mieux servi par l'instruction à la main.
                timeout=anthropic.Timeout(300.0, connect=10.0),
                max_retries=2,
            )
        self._client = client

    def lire(self, contenu: bytes) -> LectureDeContrat:
        type_mime = detecter_le_type(contenu)
        donnees = base64.standard_b64encode(contenu).decode()
        if type_mime == "application/pdf":
            bloc = {
                "type": "document",
                "source": {"type": "base64", "media_type": type_mime, "data": donnees},
            }
        elif type_mime in _IMAGES:
            bloc = {
                "type": "image",
                "source": {"type": "base64", "media_type": type_mime, "data": donnees},
            }
        else:
            return LectureDeContrat(incident=_INCIDENTS["FORMAT"])

        import anthropic

        try:
            with self._client.beta.messages.stream(
                model=self._modele,
                max_tokens=64000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=_CONSIGNE,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "high",
                    "format": {"type": "json_schema", "schema": SCHEMA_D_ANALYSE},
                },
                messages=[
                    {
                        "role": "user",
                        "content": [
                            bloc,
                            {"type": "text", "text": "Prépare l'analyse de ce contrat."},
                        ],
                    }
                ],
            ) as flux:
                reponse = flux.get_final_message()
        except anthropic.RateLimitError:
            return LectureDeContrat(incident=_INCIDENTS["SATURE"])
        except anthropic.APITimeoutError:
            return LectureDeContrat(incident=_INCIDENTS["DELAI"])
        except anthropic.APIConnectionError:
            return LectureDeContrat(incident=_INCIDENTS["RESEAU"])
        except anthropic.APIStatusError as echec:
            _journal.warning("Analyse refusée par l'API : %s %s", echec.status_code, echec.message)
            return LectureDeContrat(
                incident=(
                    f"Lecture automatique indisponible ({echec.status_code}) : "
                    "instruisez à la main."
                )
            )

        if reponse.stop_reason == "refusal":
            return LectureDeContrat(incident=_INCIDENTS["REFUS"])
        if reponse.stop_reason == "max_tokens":
            return LectureDeContrat(incident=_INCIDENTS["LONGUE"])
        texte = next((b.text for b in reponse.content if getattr(b, "type", None) == "text"), None)
        try:
            points = json.loads(texte or "")["points"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return LectureDeContrat(incident=_INCIDENTS["ILLISIBLE"])
        return LectureDeContrat(propositions=[_proposition(p) for p in points if _valable(p)])


def _valable(point: Any) -> bool:
    return (
        isinstance(point, dict)
        and str(point.get("titre") or "").strip() != ""
        and str(point.get("texte") or "").strip() != ""
    )


def _proposition(point: dict) -> dict:
    """Ramène un point à la forme de `Proposition` ; une gravité hors des clauses à
    risque tombe : elle ne veut rien dire ailleurs."""
    rubrique = point.get("rubrique")
    gravite = point.get("gravite") if rubrique == "CLAUSE_A_RISQUE" else None
    if gravite not in ("FAIBLE", "MOYENNE", "ELEVEE"):
        gravite = None
    citation = (point.get("citation") or "").strip() or None
    return {
        "rubrique": rubrique,
        "titre": str(point["titre"]).strip()[:200],
        "texte": str(point["texte"]).strip()[:4000],
        "citation": citation[:2000] if citation else None,
        "gravite": gravite,
    }
