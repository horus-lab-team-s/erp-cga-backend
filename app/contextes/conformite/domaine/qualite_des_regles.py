"""La qualité des règles, mesurée par ce que le cabinet en écarte (pas 99).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI MESURER, ET POURQUOI PAR LES ÉCARTS

Une règle qui accuse à tort ne se voit pas dans le code : elle se charge, elle réagit, elle
produit des constats. Elle se voit dans ce qu'en fait le réviseur : il les écarte. Le taux
d'écartement d'une règle (constats écartés sur constats émis) est donc la mesure la plus
directe de sa justesse, et elle ne coûte rien à relever.

La boucle que cette mesure ferme : le réviseur repère la règle bruyante et la signale, le
fiscaliste la corrige au constructeur (pas 97) et l'éprouve, la file d'anomalies s'allège.

⚠️ CE QUE LA MESURE NE DIT PAS

Un taux faible ne prouve pas qu'une règle est juste : une règle qui accuse à tort peut ne
jamais être écartée si personne ne relit ses constats. La lecture « saine » veut dire « peu
contestée », et l'écran le formule ainsi. Les seuils sont au référentiel
(`ecarts/politique.yaml`, entrée `revue_des_regles`).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.contextes.conformite.domaine.ecarts import RevueDesRegles
from app.contextes.conformite.domaine.entites import RapportConformite, Regle

__all__ = ["LectureDeRegle", "StatistiqueDeRegle", "lire_la_regle", "mesurer_les_regles"]


class LectureDeRegle(StrEnum):
    PEU_CONTESTEE = "PEU_CONTESTEE"
    A_RECALIBRER = "A_RECALIBRER"
    TROP_BRUYANTE = "TROP_BRUYANTE"
    #: Trop peu de constats sur la période pour juger.
    NON_JUGEE = "NON_JUGEE"


class StatistiqueDeRegle(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    severite: str
    constats: int
    ecartes: int
    #: L'enjeu des constats **non écartés** : ce que la règle a effectivement fait payer.
    enjeu_retenu: Decimal
    #: `None` sans constat : un taux sur zéro n'existe pas.
    taux_d_ecartement: float | None
    lecture: LectureDeRegle


def lire_la_regle(constats: int, ecartes: int, revue: RevueDesRegles) -> LectureDeRegle:
    """La lecture d'une règle. Les seuils sont atteints dès leur valeur."""
    if constats < revue.constats_minimum:
        return LectureDeRegle.NON_JUGEE
    taux = ecartes / constats
    if taux >= revue.trop_bruyante:
        return LectureDeRegle.TROP_BRUYANTE
    if taux >= revue.a_recalibrer:
        return LectureDeRegle.A_RECALIBRER
    return LectureDeRegle.PEU_CONTESTEE


def mesurer_les_regles(
    regles: Iterable[Regle],
    controles: Iterable[tuple[RapportConformite, RapportConformite]],
    revue: RevueDesRegles,
) -> list[StatistiqueDeRegle]:
    """Une statistique par règle, des plus contestées aux moins contestées.

    `controles` : pour chaque facture, le rapport **brut** (ce que le moteur a émis) et le
    rapport **arbitré** (après écarts). Les constats se comptent sur le brut : un constat
    écarté a bien été émis. Les écartés se lisent sur l'arbitré.
    """
    constats: dict[str, int] = {}
    ecartes: dict[str, int] = {}
    enjeu: dict[str, Decimal] = {}
    for brut, arbitre in controles:
        for c in brut.constats:
            constats[c.code_regle] = constats.get(c.code_regle, 0) + 1
        for e in arbitre.constats_ecartes:
            code = e.constat.code_regle
            ecartes[code] = ecartes.get(code, 0) + 1
        for c in arbitre.constats:
            enjeu[c.code_regle] = enjeu.get(c.code_regle, Decimal(0)) + (c.enjeu or Decimal(0))
    statistiques = []
    for regle in regles:
        n, e = constats.get(regle.code, 0), ecartes.get(regle.code, 0)
        statistiques.append(
            StatistiqueDeRegle(
                code=regle.code,
                libelle=regle.libelle,
                severite=regle.severite.value,
                constats=n,
                ecartes=e,
                enjeu_retenu=enjeu.get(regle.code, Decimal(0)),
                taux_d_ecartement=None if n == 0 else round(e / n, 4),
                lecture=lire_la_regle(n, e, revue),
            )
        )
    return sorted(statistiques, key=lambda s: (-(s.taux_d_ecartement or 0), -s.constats, s.code))
