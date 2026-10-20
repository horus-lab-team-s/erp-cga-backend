"""La rédaction d'un contrat par Claude : un projet d'articles, chacun à décider.

Même règle que l'analyse (`analyse_claude.py`) : l'agent PROPOSE des articles, le
juriste retient, reformule ou écarte chacun, un second valide. Le livrable est le
projet de contrat, fait des seuls articles retenus, dans les mots du juriste.

⚠️ Rien ne sort du serveur sans `CGA_ANTHROPIC_CLE_API`. Le cahier de rédaction (le
brief du juriste) part chez Anthropic : il ne doit porter que ce que le contrat
demande.
"""

from __future__ import annotations

from typing import Any

from app.contextes.souscription.adaptateurs.sortant.analyse_claude import (
    LectureDeContrat,
    incident_de_lecture,
)
from app.partage.agent_claude import Incident, client_anthropic, demander_en_json

__all__ = ["RedactionClaude", "SCHEMA_DE_REDACTION"]

_CONSIGNE = """\
Tu assistes le juriste d'un cabinet d'expertise comptable et juridique au Cameroun \
(espace OHADA). Il te donne le cahier de rédaction d'un contrat. Tu PROPOSES un projet \
d'articles ; il décide de chacun, le reformule ou l'écarte.

Rédige, en français juridique clair et équilibré :
- un article par sujet (objet, durée, prix et paiement, obligations de chaque partie, \
responsabilité, confidentialité si utile, résiliation avec préavis réciproque, force \
majeure, règlement des litiges devant les juridictions du Cameroun sauf indication \
contraire, dispositions finales) ;
- chaque article avec un titre court et son texte complet ;
- n'invente aucun fait absent du cahier : écris [À COMPLÉTER : …] là où une donnée manque ;
- ne cite aucun article de loi précis ; si une règle légale compte, signale-la comme \
« à vérifier par le juriste ».
"""

SCHEMA_DE_REDACTION: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["articles"],
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["titre", "texte"],
                "properties": {"titre": {"type": "string"}, "texte": {"type": "string"}},
            },
        }
    },
}


class RedactionClaude:
    """L'agent de rédaction. `client` s'injecte pour les essais."""

    MOTEUR = "claude"

    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        self._client = client if client is not None else client_anthropic(cle_api, delai_s=300.0)

    def rediger(self, *, type_de_contrat: str, cahier: str) -> LectureDeContrat:
        lu = demander_en_json(
            self._client,
            modele=self._modele,
            consigne=_CONSIGNE,
            contenu=(
                f"Type de contrat : {type_de_contrat}\n\n"
                f"Cahier de rédaction :\n{cahier}\n\nPropose le projet d'articles."
            ),
            schema=SCHEMA_DE_REDACTION,
            effort="high",
            max_tokens=64000,
            en_flux=True,
            objet="Rédaction",
        )
        try:
            articles = lu["articles"]
        except (KeyError, TypeError):
            if isinstance(lu, Incident) and lu.code == "INDISPONIBLE":
                return LectureDeContrat(
                    incident=(
                        f"Rédaction automatique indisponible ({lu.statut}) : rédigez à la main."
                    )
                )
            return LectureDeContrat(incident=incident_de_lecture(lu))
        return LectureDeContrat(
            propositions=[
                {
                    "rubrique": "ARTICLE",
                    "titre": str(a["titre"]).strip()[:200],
                    "texte": str(a["texte"]).strip()[:4000],
                    "citation": None,
                    "gravite": None,
                }
                for a in articles
                if isinstance(a, dict)
                and str(a.get("titre") or "").strip()
                and str(a.get("texte") or "").strip()
            ]
        )
