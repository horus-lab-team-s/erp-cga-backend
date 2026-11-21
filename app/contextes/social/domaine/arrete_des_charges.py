"""L'arrêté des charges sociales d'un mois (18 octobre 2026, point 8).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ, PUIS DÉCIDÉ

« Les frais IRPP et CNPS ne doivent pas être calculés automatiquement. » Décision du
même jour, au choix proposé : « proposition, et modification possible ». La paie
calcule, comme avant, et ce calcul devient une **proposition** ; un collaborateur
l'**arrête**, en corrigeant s'il le faut. Seul l'arrêté sert ensuite (appel de fonds
d'un adhérent mandaté, montant constaté au dépôt).

Pourquoi garder le calcul : il est juste dans le cas courant, il dit sur quel
paramètre il repose, et un montant arrêté sans point de comparaison n'est plus
contrôlable. Pourquoi ne pas s'y fier seul : une régularisation, un rappel de la
CNPS, un salarié payé hors plateforme le rendent faux, et seul le cabinet le sait.

⚠️ UN ÉCART SE JUSTIFIE. Un montant qui s'éloigne de la proposition exige un motif :
dans un an, « pourquoi la CNPS de septembre n'est pas celle des bulletins » doit avoir
une réponse écrite. La proposition du jour de l'arrêté est conservée avec lui.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.social.domaine.entites import Bulletin

__all__ = [
    "RUBRIQUES_PAR_OBLIGATION",
    "ArreteDesCharges",
    "RubriqueSociale",
    "montant_arrete_pour",
    "proposition_des_charges",
]


class RubriqueSociale(StrEnum):
    CNPS = "CNPS"
    IRPP = "IRPP"
    CFC = "CFC"
    FNE = "FNE"
    TDL = "TDL"
    RAV = "RAV"


#: Les lignes du bulletin qui composent chaque rubrique, parts salariale et patronale.
_LIGNES: dict[RubriqueSociale, frozenset[str]] = {
    RubriqueSociale.CNPS: frozenset({"CNPS_PVID_S", "CNPS_PVID_P", "CNPS_PF", "CNPS_AT"}),
    RubriqueSociale.IRPP: frozenset({"IRPP"}),
    RubriqueSociale.CFC: frozenset({"CFC_S", "CFC_P"}),
    RubriqueSociale.FNE: frozenset({"FNE"}),
    RubriqueSociale.TDL: frozenset({"TDL"}),
    RubriqueSociale.RAV: frozenset({"RAV"}),
}


def proposition_des_charges(bulletins: Iterable[Bulletin]) -> dict[RubriqueSociale, Decimal]:
    """Ce que la paie propose pour le mois : la somme des lignes de chaque rubrique."""
    totaux = {rubrique: Decimal(0) for rubrique in RubriqueSociale}
    for bulletin in bulletins:
        for ligne in bulletin.lignes:
            for rubrique, codes in _LIGNES.items():
                if ligne.code in codes:
                    totaux[rubrique] += ligne.montant
    return totaux


class ArreteDesCharges(BaseModel):
    model_config = ConfigDict(frozen=True)

    entreprise: str = Field(min_length=1)
    #: « 2026-09 ».
    periode: str = Field(pattern=r"^\d{4}-\d{2}$")
    montants: dict[RubriqueSociale, Decimal]
    #: La proposition de la paie au moment de l'arrêté, gardée pour comparaison.
    proposition: dict[RubriqueSociale, Decimal]
    arrete_par: str = Field(min_length=1)
    arrete_le: datetime
    motif: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _justifie(self) -> ArreteDesCharges:
        if set(self.montants) != set(RubriqueSociale):
            raise ValueError("un arrêté porte les six rubriques : CNPS, IRPP, CFC, FNE, TDL, RAV.")
        if any(m < 0 for m in self.montants.values()):
            raise ValueError("un montant arrêté n'est jamais négatif.")
        if self.ecarts and not (self.motif and len(self.motif.strip()) >= 10):
            raise ValueError(
                f"{', '.join(sorted(self.ecarts))} s'écarte(nt) de la proposition de la paie : "
                "le motif de l'écart est obligatoire (dix caractères au moins)."
            )
        return self

    @property
    def ecarts(self) -> list[str]:
        return [
            r.value
            for r in RubriqueSociale
            if self.montants.get(r, Decimal(0)) != self.proposition.get(r, Decimal(0))
        ]


#: Ce que chaque obligation de l'échéancier déclare, parmi les rubriques arrêtées (18
#: octobre 2026). La CNPS ne recouvre que ses cotisations ; « Retenues sur salaires :
#: IRPP et taxes assises » réunit l'IRPP et les taxes que la DGI perçoit avec lui.
RUBRIQUES_PAR_OBLIGATION: dict[str, tuple[RubriqueSociale, ...]] = {
    "CNPS": (RubriqueSociale.CNPS,),
    "IRPP_SALAIRES": (
        RubriqueSociale.IRPP,
        RubriqueSociale.CFC,
        RubriqueSociale.FNE,
        RubriqueSociale.TDL,
        RubriqueSociale.RAV,
    ),
}


def montant_arrete_pour(arrete: ArreteDesCharges, code_obligation: str) -> Decimal | None:
    """Le montant arrêté qu'une obligation déclare, ou `None` si elle n'en porte pas."""
    rubriques = RUBRIQUES_PAR_OBLIGATION.get(code_obligation)
    if rubriques is None:
        return None
    return sum((arrete.montants[r] for r in rubriques), Decimal(0))
