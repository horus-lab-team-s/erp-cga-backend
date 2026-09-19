"""La sonde du contexte E : le plan de comptes est-il là ?"""

from __future__ import annotations

from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
)
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_comptabilite"]


def sonde_de_la_comptabilite() -> Verdict | None:
    """Le plan de comptes et les journaux sont-ils chargés ?

    ⚠️ **Ils vivent en code et non en base**, et la sonde le vérifie quand même. Ce
    n'est pas superflu : ce sont des données de configuration, identiques pour tous
    les locataires, et une erreur de fusion qui viderait la liste ne se verrait
    qu'à la première écriture comptable, c'est-à-dire chez un adhérent.
    """
    if not COMPTES_SYSCOHADA:
        return Verdict.panne(
            "le plan de comptes SYSCOHADA est vide : aucune écriture ne pourrait "
            "être imputée"
        )
    if not JOURNAUX_CABINET:
        return Verdict.panne(
            "aucun journal comptable déclaré : aucune écriture ne pourrait être "
            "classée"
        )
    return None
