"""Qui est en règle, et qui ne se traite plus (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ

« Les responsables doivent savoir ceux qui sont en règle et ceux qui ne le sont pas.
D'office, quand tu n'es pas en règle, on ne doit pas pouvoir traiter ton dossier ;
mais l'administrateur peut donner l'autorisation. Il définit la règle de dépassement
(deux semaines, deux mois), et peut faire des grâces. »

TROIS FAITS, UNE LECTURE

* l'échéancier d'honoraires dit depuis quand une échéance est **en retard** (quinze
  jours après son exigibilité, `DELAI_DE_REGLEMENT`) ;
* la **règle de dépassement** du cabinet dit combien de jours un retard se tolère
  encore avant que le dossier ne se traite plus. Sans règle fixée : zéro, c'est le
  « d'office » demandé ;
* une **grâce** levée pour un adhérent nommé, jusqu'à une date, rouvre le traitement.

La situation se calcule, elle ne se stocke pas : un règlement constaté à 10 h rend le
dossier traitable à 10 h, sans que personne n'ait à « débloquer » quoi que ce soit.

⚠️ BLOQUER LE TRAITEMENT, PAS L'ADHÉRENT. Il lit son dossier, dépose ses pièces et
surtout **paie** : couper ces portes-là empêcherait précisément de régulariser. Ce
qui s'arrête, c'est le travail du cabinet (saisie, validation, déclarations, DSF).
Voir `PERMISSIONS_DE_TRAITEMENT` dans `transverse/domaine/roles.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.souscription.domaine.honoraires import (
    DELAI_DE_REGLEMENT,
    ContratDHonoraires,
    EtatEcheanceDHonoraires,
    ReglementDHonoraires,
    echeancier_d_honoraires,
)

__all__ = [
    "EtatDeReglement",
    "GraceDeReglement",
    "RegleDeDepassement",
    "SituationDeReglement",
    "situation_de_reglement",
]


class RegleDeDepassement(BaseModel):
    """Combien de jours de retard le cabinet tolère avant d'arrêter le traitement."""

    model_config = ConfigDict(frozen=True)

    #: 0 : d'office, dès qu'une échéance est en retard. Au plus un an.
    delai_jours: int = Field(ge=0, le=365)
    motif: str = Field(min_length=5, max_length=500)
    fixee_par: str = Field(min_length=1)
    fixee_le: datetime


#: La règle d'un cabinet qui n'en a fixé aucune : le « d'office » du gérant.
REGLE_D_OFFICE = RegleDeDepassement(
    delai_jours=0,
    motif="règle d'office : aucun dépassement toléré",
    fixee_par="systeme",
    fixee_le=datetime(2026, 10, 18),
)


class GraceDeReglement(BaseModel):
    """L'autorisation de traiter un dossier en retard, jusqu'à une date."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    niu: str = Field(min_length=1)
    #: Dernier jour couvert, inclus.
    jusqu_au: date
    motif: str = Field(min_length=10, max_length=500)
    accordee_par: str = Field(min_length=1)
    accordee_le: datetime
    #: Une grâce se lève avant son terme ; elle ne s'efface pas.
    levee_le: datetime | None = None
    levee_par: str | None = None

    @model_validator(mode="after")
    def _levee_complete(self) -> GraceDeReglement:
        if (self.levee_le is None) != (self.levee_par is None):
            raise ValueError("une grâce levée dit quand et par qui.")
        return self

    def couvre(self, a_la_date: date) -> bool:
        return self.levee_le is None and a_la_date <= self.jusqu_au


class EtatDeReglement(StrEnum):
    #: Aucun contrat d'honoraires : rien n'est dû au cabinet.
    SANS_CONTRAT = "SANS_CONTRAT"
    EN_REGLE = "EN_REGLE"
    #: En retard, mais dans le dépassement toléré : il se traite encore.
    EN_RETARD = "EN_RETARD"
    #: Au-delà du dépassement, couvert par une grâce : il se traite.
    SOUS_GRACE = "SOUS_GRACE"
    #: Au-delà du dépassement, sans grâce : le cabinet ne le traite plus.
    BLOQUE = "BLOQUE"


class SituationDeReglement(BaseModel):
    model_config = ConfigDict(frozen=True)

    niu: str
    etat: EtatDeReglement
    #: Ce qui est exigible et non réglé (à régler et en retard).
    du: Decimal
    #: Le premier jour de retard de la plus ancienne échéance impayée.
    en_retard_depuis: date | None = None
    #: Le jour où le traitement s'arrête (ou s'est arrêté), selon la règle.
    bloque_le: date | None = None
    grace: GraceDeReglement | None = None

    @property
    def traitable(self) -> bool:
        return self.etat is not EtatDeReglement.BLOQUE


def situation_de_reglement(
    niu: str,
    contrat: ContratDHonoraires | None,
    reglements: Sequence[ReglementDHonoraires],
    regle: RegleDeDepassement,
    graces: Sequence[GraceDeReglement],
    a_la_date: date,
) -> SituationDeReglement:
    if contrat is None:
        return SituationDeReglement(niu=niu, etat=EtatDeReglement.SANS_CONTRAT, du=Decimal(0))
    echeances = echeancier_d_honoraires(contrat, reglements, a_la_date, a_venir=0)
    dues = [
        e
        for e in echeances
        if e.etat in (EtatEcheanceDHonoraires.A_REGLER, EtatEcheanceDHonoraires.EN_RETARD)
    ]
    du = sum((e.montant for e in dues), Decimal(0))
    retards = [e for e in dues if e.etat is EtatEcheanceDHonoraires.EN_RETARD]
    if not retards:
        return SituationDeReglement(niu=niu, etat=EtatDeReglement.EN_REGLE, du=du)
    plus_ancienne = min(retards, key=lambda e: e.exigible_le)
    en_retard_depuis = plus_ancienne.exigible_le + DELAI_DE_REGLEMENT + timedelta(days=1)
    bloque_le = en_retard_depuis + timedelta(days=regle.delai_jours)
    if a_la_date < bloque_le:
        etat = EtatDeReglement.EN_RETARD
        grace = None
    else:
        grace = next((g for g in graces if g.niu == niu and g.couvre(a_la_date)), None)
        etat = EtatDeReglement.SOUS_GRACE if grace else EtatDeReglement.BLOQUE
    return SituationDeReglement(
        niu=niu,
        etat=etat,
        du=du,
        en_retard_depuis=en_retard_depuis,
        bloque_le=bloque_le,
        grace=grace,
    )
