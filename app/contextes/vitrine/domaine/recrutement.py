"""Les offres de poste du cabinet et les candidatures reçues (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
LE BESOIN

Le cabinet recrute. Il rédige l'offre dans la console (intitulé, lieu, contrat, et les
qualifications attendues, mises en forme au mini-éditeur), la publie, et le site
l'affiche : le candidat lit les qualifications avant de postuler, puis dépose son CV.

LE CYCLE

    BROUILLON ──publier──▶ PUBLIEE ──clore──▶ CLOSE

Seule une offre publiée et non échue reçoit des candidatures. Une offre close reste
lisible au cabinet, avec ses candidatures, mais disparaît du site.

⚠️ La date limite est comprise : on postule encore le jour même.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Candidature",
    "EtatOffre",
    "OffreDePoste",
    "OffreRefusee",
    "TypeDeContrat",
]


class OffreRefusee(ValueError):
    """Un geste que l'état de l'offre interdit. Rendu en 409."""


class EtatOffre(StrEnum):
    BROUILLON = "BROUILLON"
    PUBLIEE = "PUBLIEE"
    CLOSE = "CLOSE"


class TypeDeContrat(StrEnum):
    CDI = "CDI"
    CDD = "CDD"
    STAGE = "STAGE"
    PRESTATION = "PRESTATION"


class OffreDePoste(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    intitule: str = Field(min_length=3, max_length=160)
    lieu: str = Field(min_length=2, max_length=120)
    type_de_contrat: TypeDeContrat
    #: Le texte du mini-éditeur : voir `texte_riche.py`.
    description: str = Field(default="", max_length=6000)
    qualifications: str = Field(min_length=10, max_length=8000)
    date_limite: date | None = None
    etat: EtatOffre = EtatOffre.BROUILLON
    creee_le: datetime
    creee_par: str
    publiee_le: datetime | None = None
    close_le: datetime | None = None

    def modifier(self, **champs) -> OffreDePoste:
        if self.etat is EtatOffre.CLOSE:
            raise OffreRefusee("une offre close ne se modifie plus : ouvrez-en une nouvelle.")
        return OffreDePoste.model_validate(self.model_dump() | champs)

    def publier(self, a_l_instant: datetime, *, jour: date) -> OffreDePoste:
        """`jour` est la date à Douala : l'instant, lui, est en UTC."""
        if self.etat is not EtatOffre.BROUILLON:
            raise OffreRefusee(f"l'offre est déjà {self.etat.value.lower()}.")
        if self.date_limite is not None and self.date_limite < jour:
            raise OffreRefusee("la date limite est passée : reportez-la avant de publier.")
        return self.model_copy(update={"etat": EtatOffre.PUBLIEE, "publiee_le": a_l_instant})

    def clore(self, a_l_instant: datetime) -> OffreDePoste:
        if self.etat is not EtatOffre.PUBLIEE:
            raise OffreRefusee("seule une offre publiée se clôt.")
        return self.model_copy(update={"etat": EtatOffre.CLOSE, "close_le": a_l_instant})

    def ouverte_au(self, jour: date) -> bool:
        """Visible sur le site et ouverte aux candidatures ce jour-là."""
        return self.etat is EtatOffre.PUBLIEE and (
            self.date_limite is None or jour <= self.date_limite
        )


class Candidature(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    offre: str = Field(min_length=1)
    nom: str = Field(min_length=2, max_length=120)
    telephone: str = Field(min_length=6, max_length=30)
    courriel: str | None = Field(default=None, max_length=200)
    message: str = Field(default="", max_length=3000)
    #: Le CV au magasin de fichiers : son empreinte et son nom d'origine.
    cv_empreinte: str
    cv_nom: str = Field(max_length=200)
    recue_le: datetime
