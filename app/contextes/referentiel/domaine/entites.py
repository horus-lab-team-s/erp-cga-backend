"""Modèles du référentiel normatif.

Principe non négociable du projet : **aucune valeur légale en dur dans le code, aucune
lecture de paramètre sans date**. Un paramètre n'est pas une constante, c'est une
fonction du temps.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StatutValidation(StrEnum):
    """Le statut ne bloque pas le calcul : il le marque.

    Un rapport produit à partir d'un paramètre `A_VALIDER` le signale, ce qui permet de
    livrer sans attendre la validation juridique complète, sans faire croire à une
    exactitude qui n'existe pas.
    """

    A_VALIDER = "A_VALIDER"
    VALIDE = "VALIDE"
    ABROGE = "ABROGE"


class Unite(StrEnum):
    POURCENTAGE = "POURCENTAGE"
    FCFA = "FCFA"
    JOURS = "JOURS"
    JOUR_DU_MOIS = "JOUR_DU_MOIS"
    EXERCICES = "EXERCICES"
    REGEX = "REGEX"


class Fondement(BaseModel):
    """Sans fondement, on ne saura pas quoi mettre à jour à la loi de finances suivante,
    ni justifier un rejet auprès d'un adhérent mécontent. Il est donc obligatoire."""

    model_config = ConfigDict(frozen=True)

    texte: str = Field(min_length=1)
    source: str = Field(min_length=1)


class VersionParametre(BaseModel):
    """Une valeur valide sur l'intervalle [applicable_du, applicable_au[ — borne haute
    exclue. `applicable_au` à None signifie « toujours en vigueur »."""

    model_config = ConfigDict(frozen=True)

    valeur: bool | int | float | str
    applicable_du: date
    applicable_au: date | None = None
    statut: StatutValidation = StatutValidation.A_VALIDER
    fondement: Fondement
    note: str | None = None
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> VersionParametre:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(
                f"borne de validité incohérente : {self.applicable_au} <= {self.applicable_du}"
            )
        if self.statut is StatutValidation.VALIDE and not (self.valide_par and self.valide_le):
            raise ValueError(
                "une version VALIDE doit porter valide_par et valide_le : "
                "la validation engage une personne nommée, pas l'éditeur"
            )
        return self

    def couvre(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au


class Parametre(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    categorie: str
    unite: Unite
    versions: list[VersionParametre] = Field(min_length=1)

    @model_validator(mode="after")
    def _versions_sans_chevauchement(self) -> Parametre:
        triees = sorted(self.versions, key=lambda v: v.applicable_du)
        for precedente, suivante in zip(triees, triees[1:], strict=False):
            fin = precedente.applicable_au
            if fin is None or fin > suivante.applicable_du:
                raise ValueError(
                    f"{self.code} : les versions se chevauchent à partir du "
                    f"{suivante.applicable_du}. Fermer la version précédente."
                )
        return self


class ParametreResolu(BaseModel):
    """Le résultat d'une lecture, avec son contexte.

    C'est cette forme que le moteur de conformité conserve dans le rapport : la valeur
    exacte employée, sa date d'effet et son statut. Un rapport de juillet 2026 reste
    ainsi reproductible même si le seuil change en janvier 2027.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    valeur: bool | int | float | str
    unite: Unite
    applicable_du: date
    statut: StatutValidation
    fondement: Fondement
    note: str | None = None

    @property
    def valeur_decimale(self) -> Decimal:
        if isinstance(self.valeur, bool) or isinstance(self.valeur, str):
            raise TypeError(f"{self.code} n'est pas numérique (unité {self.unite})")
        return Decimal(str(self.valeur))
