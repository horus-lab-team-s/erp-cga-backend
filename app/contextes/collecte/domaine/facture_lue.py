"""La facture telle qu'un collaborateur l'a lue et confirmée, portée par la pièce.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Une facture chargée doit vite devenir une écriture. » Le moteur qui propose
l'écriture d'une facture d'achat existait (6xx, 4451, 401, contrôle de conformité)
mais ne travaillait que sur les factures de démonstration : une pièce réelle ne
portait que son type, sa référence, sa date, son TTC et son émetteur. Ni lignes,
ni HT, ni TVA, ni NIU du fournisseur : rien dont on pût tirer une écriture.

⚠️ CE QUI EST ICI EST CONFIRMÉ PAR UN HUMAIN, JAMAIS SEULEMENT LU.

La lecture automatique (`ServiceExtraction`) propose des valeurs avec leur indice
de confiance ; c'est le collaborateur qui les retient. Une facture lue est donc
toujours signée (`lue_par`), et sa `source` dit si une lecture automatique l'a
préremplie. Une écriture tirée d'une valeur que personne n'a vue serait une
écriture que personne ne peut défendre devant un vérificateur.

⚠️ LES MONTANTS SE RECOUPENT, OU LA FACTURE N'EST PAS ENREGISTRÉE. Une facture
dont les lignes ne font pas le HT, ou dont HT + TVA ne font pas le TTC, n'a
aucune écriture équilibrée : mieux vaut le dire à la saisie qu'à la proposition.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["FactureLue", "LigneLue", "MontantsIncoherents", "SourceDeLecture"]


class MontantsIncoherents(ValueError):
    """Les montants de la facture ne se recoupent pas."""


class SourceDeLecture(StrEnum):
    #: Tout tapé par le collaborateur.
    SAISIE = "SAISIE"
    #: Prérempli par la lecture automatique, puis relu et confirmé.
    LECTURE_AUTOMATIQUE = "LECTURE_AUTOMATIQUE"


class LigneLue(BaseModel):
    model_config = ConfigDict(frozen=True)

    designation: str = Field(min_length=1, max_length=300)
    montant_ht: Decimal = Field(gt=0)


class FactureLue(BaseModel):
    model_config = ConfigDict(frozen=True)

    emetteur: str = Field(min_length=1, max_length=200)
    #: Le NIU du fournisseur, tel qu'imprimé. `None` si la facture n'en porte pas :
    #: c'est alors au contrôle de conformité de le relever, pas à la saisie de l'inventer.
    niu_emetteur: str | None = Field(default=None, max_length=32)
    #: Le collaborateur a vérifié sur le portail de la DGI que ce NIU est actif. `None` :
    #: non vérifié. ⚠️ La règle FAC-ID-003 l'exige pour déduire la TVA et la charge ; sans
    #: cette attestation, la proposition d'écriture reste bloquée, avec le message de la
    #: règle. L'interconnexion avec la DGI pourra un jour la remplir à sa place.
    niu_actif: bool | None = None
    reference: str = Field(min_length=1, max_length=64)
    date_emission: date
    lignes: tuple[LigneLue, ...] = ()
    montant_ht: Decimal = Field(gt=0)
    montant_tva: Decimal = Field(ge=0)
    montant_ttc: Decimal = Field(gt=0)
    source: SourceDeLecture = SourceDeLecture.SAISIE
    lue_par: str = Field(min_length=1)
    lue_le: datetime

    @model_validator(mode="after")
    def _montants_coherents(self) -> FactureLue:
        if self.montant_ht + self.montant_tva != self.montant_ttc:
            raise MontantsIncoherents(
                f"HT {self.montant_ht} + TVA {self.montant_tva} ≠ TTC {self.montant_ttc} : "
                "relisez les totaux de la facture."
            )
        if self.lignes:
            somme = sum((ligne.montant_ht for ligne in self.lignes), Decimal(0))
            if somme != self.montant_ht:
                raise MontantsIncoherents(
                    f"les lignes font {somme} HT, la facture annonce {self.montant_ht} : "
                    "une ligne manque ou est mal lue."
                )
        return self
