"""L'identité fiscale du cabinet, et la facture définitive des honoraires (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DÉCIDÉ : « donner la possibilité de saisir cela plus tard ». Le NIU,
le RCCM et le régime de TVA du cabinet ne sont pas encore connus de la plateforme. La
direction les saisit quand elle les a, un à un s'il le faut ; tant qu'il en manque un,
la facture définitive ne s'émet pas, et le refus dit lequel.

⚠️ AUCUNE MENTION INVENTÉE. Une facture qui porte un faux NIU est pire que pas de
facture : elle circule, et l'adhérent la verse à sa comptabilité. La note d'honoraires
(sans mention fiscale) reste disponible en attendant.

⚠️ UNE FACTURE ÉMISE NE CHANGE PLUS. Elle fige, au jour de son émission, l'identité du
cabinet, le montant et ses taxes. Corriger le RCCM demain ne réécrit pas les factures
d'hier : un document fiscal se lit tel qu'il a été remis.

⚠️ NUMÉROTATION CONTINUE, PAR ANNÉE D'ÉMISSION : F-2026-0001, F-2026-0002… Un numéro
dérivé de l'échéance (comme celui de la note) laisserait des trous ; une facture se
numérote dans l'ordre où elle est émise.

LE MONTANT CONVENU : HT OU TTC. Le contrat d'honoraires porte un montant ; selon ce que
le cabinet a convenu avec ses adhérents, il s'entend hors taxes (la TVA s'ajoute) ou
toutes taxes comprises (la TVA s'en extrait). La direction le dit une fois, avec le
régime ; la plateforme ne le suppose pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "FactureDHonoraires",
    "IdentiteFiscaleDuCabinet",
    "IdentiteIncomplete",
    "MontantsConvenus",
    "RegimeTvaDuCabinet",
    "numero_de_facture",
    "ventiler",
]

#: Le NIU camerounais : une lettre, douze chiffres, une lettre (« M081234567890P »).
_NIU = re.compile(r"^[A-Z]\d{12}[A-Z]$")


class IdentiteIncomplete(ValueError):
    """La facture ne s'émet pas : une mention du cabinet manque."""


class RegimeTvaDuCabinet(StrEnum):
    ASSUJETTI = "ASSUJETTI"
    NON_ASSUJETTI = "NON_ASSUJETTI"
    EXONERE = "EXONERE"


class MontantsConvenus(StrEnum):
    """Ce que le montant du contrat d'honoraires représente, pour un cabinet assujetti."""

    HT = "HT"
    TTC = "TTC"


class IdentiteFiscaleDuCabinet(BaseModel):
    """Ce que la facture porte du cabinet. Chaque champ peut attendre ; aucun ne s'invente."""

    model_config = ConfigDict(frozen=True)

    niu: str | None = None
    rccm: str | None = Field(default=None, min_length=5, max_length=60)
    regime_tva: RegimeTvaDuCabinet | None = None
    #: Pour un cabinet assujetti : le taux, en pour cent (19,25 au Cameroun en 2026).
    taux_tva: Decimal | None = Field(default=None, gt=0, le=100)
    montants_convenus: MontantsConvenus | None = None
    #: Pour un cabinet exonéré : le texte qui fonde l'exonération, porté sur la facture.
    fondement_exoneration: str | None = Field(default=None, min_length=5, max_length=300)
    modifiee_par: str | None = None
    modifiee_le: datetime | None = None

    @model_validator(mode="after")
    def _coherente(self) -> IdentiteFiscaleDuCabinet:
        if self.niu is not None and not _NIU.match(self.niu):
            raise ValueError(
                "le NIU compte quatorze caractères : une lettre, douze chiffres, une lettre."
            )
        if self.regime_tva is not RegimeTvaDuCabinet.ASSUJETTI and (
            self.taux_tva is not None or self.montants_convenus is not None
        ):
            raise ValueError("un taux de TVA ne se renseigne que pour un cabinet assujetti.")
        if (
            self.regime_tva is not RegimeTvaDuCabinet.EXONERE
            and self.fondement_exoneration is not None
        ):
            raise ValueError("le fondement d'une exonération ne vaut que pour un cabinet exonéré.")
        return self

    @property
    def manques(self) -> list[str]:
        """Ce qui empêche encore d'émettre une facture, en clair."""
        manques = []
        if not self.niu:
            manques.append("le NIU du cabinet")
        if not self.rccm:
            manques.append("le numéro RCCM du cabinet")
        if self.regime_tva is None:
            manques.append("le régime de TVA du cabinet")
        elif self.regime_tva is RegimeTvaDuCabinet.ASSUJETTI:
            if self.taux_tva is None:
                manques.append("le taux de TVA")
            if self.montants_convenus is None:
                manques.append("si les honoraires convenus s'entendent HT ou TTC")
        elif self.regime_tva is RegimeTvaDuCabinet.EXONERE and not self.fondement_exoneration:
            manques.append("le texte qui fonde l'exonération de TVA")
        return manques

    @property
    def complete(self) -> bool:
        return not self.manques


def _franc(montant: Decimal) -> Decimal:
    return montant.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def ventiler(
    montant: Decimal, identite: IdentiteFiscaleDuCabinet
) -> tuple[Decimal, Decimal, Decimal]:
    """(HT, TVA, TTC) d'un montant d'honoraires, selon le régime et ce qui a été convenu.

    Sans TVA (non assujetti, exonéré), le montant est à la fois HT et TTC. La TVA se
    calcule au franc près, et le TTC reste toujours HT + TVA.
    """
    if identite.regime_tva is not RegimeTvaDuCabinet.ASSUJETTI:
        return montant, Decimal(0), montant
    assert identite.taux_tva is not None
    taux = identite.taux_tva / Decimal(100)
    if identite.montants_convenus is MontantsConvenus.HT:
        tva = _franc(montant * taux)
        return montant, tva, montant + tva
    ht = _franc(montant / (1 + taux))
    return ht, montant - ht, montant


def numero_de_facture(annee: int, rang: int) -> str:
    return f"F-{annee}-{rang:04d}"


class FactureDHonoraires(BaseModel):
    """Une facture émise : tout ce qu'elle porte, figé au jour de son émission."""

    model_config = ConfigDict(frozen=True)

    numero: str = Field(pattern=r"^F-\d{4}-\d{4,}$")
    niu: str = Field(min_length=1)
    #: Le début de la période facturée : une facture par échéance.
    periode: date
    periode_fin: date
    adherent: str = Field(min_length=1)
    emise_le: date
    identite: IdentiteFiscaleDuCabinet
    montant_ht: Decimal = Field(ge=0)
    tva: Decimal = Field(ge=0)
    montant_ttc: Decimal = Field(ge=0)
    reglee_le: date
    reference_du_reglement: str

    @model_validator(mode="after")
    def _juste(self) -> FactureDHonoraires:
        if not self.identite.complete:
            raise ValueError(
                "une facture ne s'émet pas sans " + ", ".join(self.identite.manques) + "."
            )
        if self.montant_ht + self.tva != self.montant_ttc:
            raise ValueError("le TTC d'une facture est toujours HT + TVA.")
        return self
