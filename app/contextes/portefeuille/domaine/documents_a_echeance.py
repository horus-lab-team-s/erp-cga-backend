"""Les documents à échéance d'un adhérent (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA RESPONSABLE A DEMANDÉ (réunion du 4 novembre 2026, demande 15)

« Le système doit signaler automatiquement au gestionnaire et au client les documents ou
abonnements expirés ou sur le point de l'être. » Rien, jusqu'ici, ne datait la validité d'un
document : une attestation de conformité fiscale de mars était périmée en juin sans que
personne ne le sache.

LA CONFIGURATION D'ABORD

Les sortes de documents et leur durée de validité sont un CATALOGUE du référentiel
(`Docs/referentiel/portefeuille/documents_a_echeance.yaml`), chacune avec sa source et son
statut. Une sorte dont la durée n'est pas établie n'en a pas : la date d'expiration se saisit
alors à la main. Le code n'écrit aucune durée — voir
`Docs/sources-reglementaires/2026-11-09-expirations-et-cga.md`.

⚠️ L'HISTOIRE SE GARDE. Une nouvelle attestation n'efface pas l'ancienne : la plus récente
d'une sorte est en vigueur, les précédentes restent lisibles.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CatalogueDesDocuments",
    "DocumentAEcheance",
    "SorteDeDocument",
    "documents_en_vigueur",
    "expiration_calculee",
]


class SorteDeDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,39}$")
    libelle: str = Field(min_length=3)
    #: Absente : la durée n'est pas établie, l'expiration se saisit.
    validite_mois: int | None = Field(default=None, ge=1, le=120)
    #: `A_VALIDER` tant que le cabinet ne l'a pas confirmée ; `VALIDE` ensuite.
    statut: str = "A_VALIDER"
    fondement: str | None = None
    source: str | None = None


class CatalogueDesDocuments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    documents: tuple[SorteDeDocument, ...] = ()
    source: str = "aucun catalogue"

    def sorte(self, code: str) -> SorteDeDocument | None:
        return next((s for s in self.documents if s.code == code), None)


def _mois_apres(depart: date, mois: int) -> date:
    annee, rang = divmod(depart.month - 1 + mois, 12)
    annee += depart.year
    jour = min(depart.day, calendar.monthrange(annee, rang + 1)[1])
    return date(annee, rang + 1, jour)


def expiration_calculee(sorte: SorteDeDocument, delivre_le: date) -> date | None:
    """La date d'expiration que la durée du catalogue donne, ou `None` sans durée."""
    if sorte.validite_mois is None:
        return None
    return _mois_apres(delivre_le, sorte.validite_mois)


class DocumentAEcheance(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    delivre_le: date
    expire_le: date
    #: Une référence, un numéro d'attestation : ce qui permet de le retrouver.
    reference: str | None = Field(default=None, max_length=120)
    enregistre_par: str = Field(min_length=1)
    enregistre_le: datetime

    @model_validator(mode="after")
    def _apres_la_delivrance(self) -> DocumentAEcheance:
        if self.expire_le <= self.delivre_le:
            raise ValueError("un document expire après sa délivrance.")
        return self

    def jours_restants(self, a_la_date: date) -> int:
        """Négatif une fois expiré."""
        return (self.expire_le - a_la_date).days


def documents_en_vigueur(documents: Iterable[DocumentAEcheance]) -> list[DocumentAEcheance]:
    """Le plus récent de chaque sorte : les précédents sont de l'histoire."""
    recents: dict[str, DocumentAEcheance] = {}
    for d in sorted(documents, key=lambda d: (d.delivre_le, d.enregistre_le)):
        recents[d.code] = d
    return sorted(recents.values(), key=lambda d: d.expire_le)
