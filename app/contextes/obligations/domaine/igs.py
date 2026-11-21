"""Estimer l'impôt général synthétique d'un adhérent (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE DIT LE CGI 2026
* C 40 (1) : un montant ANNUEL fixe par classe de chiffre d'affaires hors taxes,
  celui de l'exercice précédent ;
* C 40 (2) : réduit de MOITIÉ pour l'assujetti tenu à une comptabilité qui adhère à
  un Centre de Gestion Agréé ; l'obligation de comptabilité naît à 10 M (C 44 (1)) ;
* C 41 (2) : acquitté par trimestre.

⚠️ UNE ESTIMATION, JAMAIS UN MONTANT DÛ : le chiffre d'affaires est celui des livres
du cabinet, que l'administration peut retenir autrement. L'échéancier l'affiche comme
tel, avec ce que l'adhérent aurait payé sans adhérer : c'est l'avantage du centre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["EstimationIgs", "estimer_l_igs"]


@dataclass(frozen=True)
class EstimationIgs:
    chiffre_affaires: Decimal
    #: Le montant de la classe, avant réduction.
    plein_tarif: Decimal
    annuel: Decimal
    reduction_cga: bool

    @property
    def trimestriel(self) -> Decimal:
        return (self.annuel / 4).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def estimer_l_igs(
    chiffre_affaires: Decimal,
    *,
    classe: Callable[[Decimal], Decimal],
    adherent_cga: bool,
    reduction_pourcent: Decimal,
    seuil_comptabilite: Decimal,
) -> EstimationIgs:
    """`classe` : le barème résolu (le montant de la classe qui contient le chiffre)."""
    plein = classe(chiffre_affaires)
    reduite = adherent_cga and chiffre_affaires >= seuil_comptabilite
    annuel = plein * (Decimal(100) - reduction_pourcent) / Decimal(100) if reduite else plein
    return EstimationIgs(
        chiffre_affaires=chiffre_affaires,
        plein_tarif=plein,
        annuel=annuel.quantize(Decimal(1), rounding=ROUND_HALF_UP),
        reduction_cga=reduite,
    )
