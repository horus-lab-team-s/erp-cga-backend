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

from typing import Any

from app.partage.agent_claude import (
    Incident,
    bloc_du_document,
    client_du_fournisseur,
    demander_en_json,
)

__all__ = ["AnalyseClaude", "LectureDeContrat", "SCHEMA_D_ANALYSE", "incident_de_lecture"]

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

    def __init__(
        self, *, cle_api: str, modele: str, client: Any = None, fournisseur: str = "anthropic"
    ) -> None:
        self._modele = modele
        # Le moteur inscrit sur ce qui a été lu ou rédigé : qui l'a fait, pour la trace.
        self.MOTEUR = "chatgpt" if fournisseur == "openai" else "claude"
        # Un contrat de quelques pages se lit en une à deux minutes ; au-delà de cinq, le
        # juriste est mieux servi par l'instruction à la main.
        self._client = (
            client
            if client is not None
            else client_du_fournisseur(fournisseur, cle_api, delai_s=300.0)
        )

    def lire(self, contenu: bytes) -> LectureDeContrat:
        bloc = bloc_du_document(contenu)
        if bloc is None:
            return LectureDeContrat(incident=_INCIDENTS["FORMAT"])
        lu = demander_en_json(
            self._client,
            modele=self._modele,
            consigne=_CONSIGNE,
            contenu=[bloc, {"type": "text", "text": "Prépare l'analyse de ce contrat."}],
            schema=SCHEMA_D_ANALYSE,
            effort="high",
            max_tokens=64000,
            en_flux=True,
            objet="Analyse",
        )
        try:
            points = lu["points"]
        except (KeyError, TypeError):
            return LectureDeContrat(incident=incident_de_lecture(lu))
        return LectureDeContrat(propositions=[_proposition(p) for p in points if _valable(p)])


def incident_de_lecture(lu: Any) -> str:
    """Les mots du juriste pour une panne de l'agent. Une réponse sans la clé attendue est
    illisible, comme une réponse qui n'est pas du JSON."""
    if not isinstance(lu, Incident):
        return _INCIDENTS["ILLISIBLE"]
    if lu.code == "INDISPONIBLE":
        return f"Lecture automatique indisponible ({lu.statut}) : instruisez à la main."
    return _INCIDENTS[lu.code]


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
