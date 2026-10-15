"""La rédaction d'un contrat par Claude : un projet d'articles, chacun à décider.

Même règle que l'analyse (`analyse_claude.py`) : l'agent PROPOSE des articles, le
juriste retient, reformule ou écarte chacun, un second valide. Le livrable est le
projet de contrat, fait des seuls articles retenus, dans les mots du juriste.

⚠️ Rien ne sort du serveur sans `CGA_ANTHROPIC_CLE_API`. Le cahier de rédaction (le
brief du juriste) part chez Anthropic : il ne doit porter que ce que le contrat
demande.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.contextes.souscription.adaptateurs.sortant.analyse_claude import (
    _INCIDENTS,
    LectureDeContrat,
)

__all__ = ["RedactionClaude", "SCHEMA_DE_REDACTION"]

_journal = logging.getLogger(__name__)

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
        if client is None:
            import anthropic

            client = anthropic.Anthropic(
                api_key=cle_api, timeout=anthropic.Timeout(300.0, connect=10.0), max_retries=2
            )
        self._client = client

    def rediger(self, *, type_de_contrat: str, cahier: str) -> LectureDeContrat:
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
                    "format": {"type": "json_schema", "schema": SCHEMA_DE_REDACTION},
                },
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Type de contrat : {type_de_contrat}\n\n"
                            f"Cahier de rédaction :\n{cahier}\n\nPropose le projet d'articles."
                        ),
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
            _journal.warning(
                "Rédaction refusée par l'API : %s %s", echec.status_code, echec.message
            )
            return LectureDeContrat(
                incident=(
                    f"Rédaction automatique indisponible ({echec.status_code}) : rédigez à la main."
                )
            )
        if reponse.stop_reason == "refusal":
            return LectureDeContrat(incident=_INCIDENTS["REFUS"])
        if reponse.stop_reason == "max_tokens":
            return LectureDeContrat(incident=_INCIDENTS["LONGUE"])
        texte = next((b.text for b in reponse.content if getattr(b, "type", None) == "text"), None)
        try:
            articles = json.loads(texte or "")["articles"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return LectureDeContrat(incident=_INCIDENTS["ILLISIBLE"])
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
