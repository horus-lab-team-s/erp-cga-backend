"""L'impôt sur les sociétés estimé de l'exercice, minimum de perception compris (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE DIT LE CGI 2026
    art. 17 (1)   le taux de l'IS est de 30 % ;
    art. 22 (2)   l'impôt dû ne peut être inférieur à 2 % de la base de l'art. 23 ;
                  minimum et impôt sont majorés de 10 % de centimes additionnels ;
    art. 23       la base : le chiffre d'affaires global hors taxes de l'exercice,
                  arrondi au millier inférieur ;
    art. 119 (1)  l'abattement CGA s'applique « sans que l'impôt dû soit inférieur au
                  minimum de perception » : il est déjà dans le résultat fiscal, et le
                  plus élevé des deux montants le respecte.

CE QUE LE CALCUL NE FAIT PAS, ET LE DIT
* l'IRPP d'un entrepreneur individuel (barème progressif sur le bénéfice) ;
* l'IS des activités à marge administrée (14 % sur la marge brute) ;
* l'imputation des acomptes mensuels versés et des précomptes subis : le solde à payer
  se lit sur le portail, qui préremplit l'IS de la déclaration des soldes.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    StatutValidation,
)

__all__ = ["FORMES_A_L_IS", "ImpotEstime", "estimer_l_impot"]

#: Les sociétés de capitaux, imposées à l'IS. Les autres formes : l'IRPP de l'exploitant
#: (ETS), la transparence (GIE, SCI selon l'option), hors de cette estimation.
FORMES_A_L_IS = frozenset({"SA", "SARL", "SARLU", "SAS"})
_FRANC = Decimal(1)
_CENT = Decimal(100)


@dataclass(frozen=True)
class ImpotEstime:
    #: `False` : l'impôt n'est pas estimé ici, et `motif` dit pourquoi.
    estime: bool
    motif: str
    is_au_taux: Decimal | None = None
    minimum_de_perception: Decimal | None = None
    #: Le plus élevé des deux, centimes additionnels compris.
    impot_du: Decimal | None = None
    #: « MINIMUM », « TAUX », ou « AUCUN » sans bénéfice ni chiffre d'affaires.
    retenu: str | None = None
    base_du_minimum: Decimal | None = None
    #: Un taux employé n'est pas encore validé par le cabinet.
    non_valide: bool = False


def _avec_cac(montant: Decimal, cac: Decimal) -> Decimal:
    return (montant * (1 + cac / _CENT)).quantize(_FRANC, rounding=ROUND_HALF_UP)


def estimer_l_impot(
    *,
    forme_juridique: str,
    regime: str | None,
    resultat_fiscal: Decimal,
    chiffre_affaires: Decimal,
    parametres: ServiceParametres,
    cloture: date,
) -> ImpotEstime:
    if regime == "IGS":
        return ImpotEstime(
            estime=False,
            motif="à l'IGS, l'impôt est l'IGS, libératoire de l'IS et de l'IRPP (CGI art. C 38) : "
            "voir l'échéancier.",
        )
    if forme_juridique not in FORMES_A_L_IS:
        return ImpotEstime(
            estime=False,
            motif=f"forme {forme_juridique} : le bénéfice relève de l'IRPP de l'exploitant ou "
            "des associés, au barème progressif, que la plateforme n'estime pas encore.",
        )
    try:
        taux = parametres.resoudre("TAUX_IS_REEL_NORMAL", cloture)
        minimum = parametres.resoudre("IS_MINIMUM_PERCEPTION_TAUX", cloture)
        cac = parametres.resoudre("CAC_TAUX", cloture)
    except (ParametreInconnu, AucuneVersionApplicable) as absence:
        return ImpotEstime(
            estime=False, motif=f"le référentiel ne fixe pas un taux nécessaire : {absence}."
        )

    au_taux = _avec_cac(
        max(resultat_fiscal, Decimal(0)) * taux.valeur_decimale / _CENT, cac.valeur_decimale
    )
    # Art. 23 : arrondi au millier inférieur.
    base = (max(chiffre_affaires, Decimal(0)) / 1000).to_integral_value(rounding=ROUND_DOWN) * 1000
    plancher = _avec_cac(base * minimum.valeur_decimale / _CENT, cac.valeur_decimale)
    # « AUCUN » : ni bénéfice ni chiffre d'affaires, rien n'est dû (un exercice qui
    # commence). Dire « l'IS au taux s'applique » sur zéro n'avait pas de sens.
    retenu = "AUCUN" if not (au_taux or plancher) else "MINIMUM" if plancher > au_taux else "TAUX"
    return ImpotEstime(
        estime=True,
        motif=(
            f"IS au taux de {taux.valeur_decimale:g} % sur le résultat fiscal, au moins "
            f"{minimum.valeur_decimale:g} % du chiffre d'affaires (minimum de perception), "
            f"majorés de {cac.valeur_decimale:g} % de centimes additionnels. Acomptes versés "
            "et précomptes subis à imputer sur le portail."
        ),
        is_au_taux=au_taux,
        minimum_de_perception=plancher,
        impot_du=max(au_taux, plancher),
        retenu=retenu,
        base_du_minimum=base,
        non_valide=any(p.statut is not StatutValidation.VALIDE for p in (taux, minimum, cac)),
    )
