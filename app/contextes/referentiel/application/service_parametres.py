"""Lecture du référentiel normatif à une date donnée."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.referentiel.domaine.entites import (
    Parametre,
    ParametreResolu,
    StatutValidation,
)
from app.contextes.referentiel.domaine.ports import DepotParametres

__all__ = ["ServiceParametres", "ParametreInconnu", "AucuneVersionApplicable"]


class ParametreInconnu(KeyError):
    """Code absent du référentiel."""


class AucuneVersionApplicable(LookupError):
    """Le paramètre existe mais aucune version ne couvre la date demandée.

    Volontairement une erreur, jamais une valeur par défaut : une valeur légale
    silencieusement absente est un bug fiscal, pas un cas limite.
    """


class ServiceParametres:
    """Toute lecture se fait à une date. Il n'existe volontairement aucune méthode
    permettant de lire « la valeur courante » sans préciser laquelle."""

    def __init__(self, parametres: list[Parametre]) -> None:
        doublons = {p.code for p in parametres if sum(q.code == p.code for q in parametres) > 1}
        if doublons:
            raise ValueError(f"codes de paramètre en double : {sorted(doublons)}")
        self._par_code: dict[str, Parametre] = {p.code: p for p in parametres}

    @classmethod
    def depuis_depot(cls, depot: DepotParametres) -> ServiceParametres:
        """Construit le service à partir de n'importe quelle source de paramètres.

        Le cas d'usage ignore s'il s'agit d'un fichier, d'une base ou d'un cache :
        c'est tout l'intérêt du port.
        """
        return cls(depot.charger())

    @property
    def codes(self) -> list[str]:
        return sorted(self._par_code)

    def existe(self, code: str) -> bool:
        return code in self._par_code

    def resoudre(self, code: str, a_la_date: date) -> ParametreResolu:
        parametre = self._par_code.get(code)
        if parametre is None:
            proches = [c for c in self._par_code if code.split("_")[0] in c]
            indice = f" Codes proches : {', '.join(sorted(proches)[:5])}." if proches else ""
            raise ParametreInconnu(f"paramètre « {code} » absent du référentiel.{indice}")

        for version in parametre.versions:
            if version.couvre(a_la_date):
                return ParametreResolu(
                    code=parametre.code,
                    libelle=parametre.libelle,
                    valeur=version.valeur,
                    unite=parametre.unite,
                    applicable_du=version.applicable_du,
                    statut=version.statut,
                    fondement=version.fondement,
                    note=version.note,
                )

        periodes = ", ".join(
            f"[{v.applicable_du} → {v.applicable_au or 'en vigueur'}[" for v in parametre.versions
        )
        raise AucuneVersionApplicable(
            f"« {code} » n'a aucune version applicable au {a_la_date}. "
            f"Versions connues : {periodes}"
        )

    def valeur_numerique(self, code: str, a_la_date: date) -> Decimal:
        return self.resoudre(code, a_la_date).valeur_decimale

    def valeur_texte(self, code: str, a_la_date: date) -> str:
        return str(self.resoudre(code, a_la_date).valeur)

    def codes_non_valides(self, a_la_date: date) -> list[str]:
        """Codes dont la version applicable n'a pas encore été confirmée sur le texte.

        Sert au bandeau d'avertissement des rapports : tant que cette liste n'est pas
        vide, aucun chiffre produit n'est opposable.
        """
        non_valides = []
        for code in self._par_code:
            try:
                if self.resoudre(code, a_la_date).statut is not StatutValidation.VALIDE:
                    non_valides.append(code)
            except AucuneVersionApplicable:
                continue
        return sorted(non_valides)
