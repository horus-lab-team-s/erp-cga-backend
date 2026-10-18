"""L'agent qui relit le dossier d'une DSF avant son dépôt (18 octobre 2026, point 24).

─────────────────────────────────────────────────────────────────────────────────
« Un agent rédacteur DSF. » Les chiffres de la liasse ne sont pas son affaire : la
plateforme les établit depuis la balance, et deux exécutions rendent la même liasse.
L'agent apporte ce qu'un second regard apporte : une activité sans code NACAM, un code qui
ne ressemble pas à l'activité, une pièce que l'activité rend sans doute nécessaire, une
fiche signalétique où il manque ce que le portail demandera, un contrôle en échec qui
appelle une note annexe.

⚠️ IL PROPOSE. Chaque remarque dit ce qu'il constate et ce qu'il suggère de vérifier ; le
préparateur juge. Rien ne se bloque sur lui : sans clé, ou en panne, l'écran le dit et la
DSF se prépare comme avant.

⚠️ Ce qui part chez Anthropic : la forme juridique, l'activité, les codes NACAM et le
chiffre d'affaires de chaque activité, le système (normal ou SMT), l'état des pièces, les
contrôles et avertissements de la liasse, le total du bilan et le résultat. Ni le NIU, ni
la dénomination, ni les noms des dirigeants, ni la banque.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.partage.agent_claude import Incident, client_anthropic, demander_en_json

__all__ = ["RelectureDeLaDsf", "RemarqueDeLAgent"]

_CONSIGNE = (
    "Tu relis, pour un centre de gestion agréé camerounais, le dossier d'une déclaration "
    "statistique et fiscale (DSF, référentiel SYSCOHADA révisé, DGI du Cameroun) avant son "
    "dépôt. Tu ne recalcules aucun montant et tu n'en proposes aucun : la plateforme les "
    "établit. Tu signales ce qui mérite un regard humain : une activité sans code NACAM ou "
    "un code qui ne correspond pas à l'activité décrite, une pièce à fournir que l'activité "
    "rend probablement nécessaire ou au contraire sans objet, une rubrique de la fiche "
    "signalétique manquante que le portail demandera, un contrôle en échec ou un "
    "avertissement qui appelle une note annexe. Chaque remarque dit ce que tu constates "
    "dans les données fournies et ce que tu suggères de vérifier ; tu n'affirmes rien que "
    "les données ne montrent, et tu ne cites aucun article de loi dont tu n'es pas certain. "
    "Français sobre, phrases courtes, pas de tiret cadratin. Huit remarques au plus ; une "
    "liste vide si rien ne le mérite."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "remarques": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sujet": {"type": "string"},
                    "constat": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["sujet", "constat", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["remarques"],
    "additionalProperties": False,
}

_AU_PLUS = 8


@dataclass(frozen=True)
class RemarqueDeLAgent:
    sujet: str
    constat: str
    suggestion: str


class RelectureDeLaDsf:
    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        self._client = client if client is not None else client_anthropic(cle_api, delai_s=90.0)

    def relire(self, dossier: str) -> list[RemarqueDeLAgent] | None:
        """Les remarques, ou `None` : l'agent n'a pas répondu, l'écran le dit."""
        lu = demander_en_json(
            self._client,
            modele=self._modele,
            consigne=_CONSIGNE,
            contenu=dossier,
            schema=_SCHEMA,
            effort="medium",
            max_tokens=3000,
            en_flux=False,
            objet="relecture d'une DSF",
        )
        if isinstance(lu, Incident) or not isinstance(lu, dict):
            return None
        remarques = []
        for r in lu.get("remarques") or []:
            if not isinstance(r, dict):
                continue
            sujet, constat, suggestion = (
                str(r.get(k, "")).strip() for k in ("sujet", "constat", "suggestion")
            )
            if sujet and constat:
                remarques.append(RemarqueDeLAgent(sujet[:120], constat[:600], suggestion[:600]))
        return remarques[:_AU_PLUS]
