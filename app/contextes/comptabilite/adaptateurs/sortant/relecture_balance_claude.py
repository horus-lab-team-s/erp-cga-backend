"""L'agent comptable qui relit une balance (18 octobre 2026, point 15).

─────────────────────────────────────────────────────────────────────────────────
« Un agent IA comptable. » Il lit la balance d'un exercice, compte par compte, et le
relevé déterministe des soldes à regarder (`domaine/soldes_a_regarder.py`). Il apporte
ce qu'un réviseur apporte en la parcourant : un compte qui ne devrait pas être mouvementé
pour ce dossier, une charge sans le produit qui l'accompagne d'ordinaire, une TVA
collectée sans chiffre d'affaires, une régularisation de fin d'exercice qui manque.

⚠️ IL PROPOSE ET N'ÉCRIT RIEN. Une écriture se saisit par le comptable et se valide par
un autre ; l'agent dit où regarder et quelle régularisation envisager, en comptes et en
sens, sans inventer de montant. Sans clé, ou en panne, l'écran le dit et rien n'attend.

⚠️ Ce qui part chez Anthropic : les numéros et intitulés de comptes, leurs totaux et
soldes, la forme juridique, le régime et les dates de l'exercice. Ni le NIU, ni la
dénomination, ni un libellé d'écriture, ni un nom de tiers.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.partage.agent_claude import Incident, client_anthropic, demander_en_json

__all__ = ["RelectureDeLaBalance", "RemarqueSurLaBalance"]

_CONSIGNE = (
    "Tu es réviseur dans un centre de gestion agréé camerounais. Tu relis la balance "
    "générale d'un exercice, tenue selon le SYSCOHADA révisé. Tu signales ce qui mérite "
    "un regard humain : un solde de sens inattendu, un compte mouvementé qui ne cadre pas "
    "avec la forme ou le régime de l'entreprise, une charge ou un produit sans sa "
    "contrepartie habituelle, une TVA incohérente avec le chiffre d'affaires, une "
    "régularisation de fin d'exercice qui semble manquer (amortissements, charges à "
    "payer, produits constatés d'avance, stocks). Pour chaque remarque, tu dis ce que tu "
    "constates dans les chiffres fournis et ce que tu suggères de vérifier ; si une "
    "écriture de régularisation est envisageable, tu la décris en comptes et en sens, "
    "sans inventer de montant. Tu n'affirmes rien que les chiffres ne montrent, et tu ne "
    "cites aucun article de loi. Français sobre, phrases courtes, pas de tiret cadratin. "
    "Dix remarques au plus ; une liste vide si rien ne le mérite."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "remarques": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "compte": {"type": "string"},
                    "constat": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["compte", "constat", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["remarques"],
    "additionalProperties": False,
}

_AU_PLUS = 10


@dataclass(frozen=True)
class RemarqueSurLaBalance:
    #: Le compte visé, ou `None` pour une remarque d'ensemble.
    compte: str | None
    constat: str
    suggestion: str


class RelectureDeLaBalance:
    def __init__(self, *, cle_api: str, modele: str, client: Any = None) -> None:
        self._modele = modele
        self._client = client if client is not None else client_anthropic(cle_api, delai_s=90.0)

    def relire(self, balance: str) -> list[RemarqueSurLaBalance] | None:
        """Les remarques, ou `None` : l'agent n'a pas répondu, l'écran le dit."""
        lu = demander_en_json(
            self._client,
            modele=self._modele,
            consigne=_CONSIGNE,
            contenu=balance,
            schema=_SCHEMA,
            effort="medium",
            max_tokens=4000,
            en_flux=False,
            objet="relecture d'une balance",
        )
        if isinstance(lu, Incident) or not isinstance(lu, dict):
            return None
        remarques = []
        for r in lu.get("remarques") or []:
            if not isinstance(r, dict):
                continue
            compte, constat, suggestion = (
                str(r.get(k, "")).strip() for k in ("compte", "constat", "suggestion")
            )
            if constat:
                remarques.append(
                    RemarqueSurLaBalance(compte[:12] or None, constat[:600], suggestion[:600])
                )
        return remarques[:_AU_PLUS]
