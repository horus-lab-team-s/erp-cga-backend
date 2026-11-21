"""Les offres de poste et les candidatures : en base dans une requête, en mémoire sinon."""

from __future__ import annotations

from typing import Any

from app.contextes.vitrine.adaptateurs.sortant.tables import TableCandidature, TableOffreDePoste
from app.contextes.vitrine.domaine.recrutement import Candidature, OffreDePoste
from app.infrastructure.depot_document import DepotDocument

__all__ = [
    "DepotCandidaturesMemoire",
    "DepotCandidaturesSql",
    "DepotOffresMemoire",
    "DepotOffresSql",
    "depot_candidatures",
    "depot_offres",
]


class DepotOffresSql(DepotDocument[OffreDePoste]):
    _table = TableOffreDePoste
    _entite = OffreDePoste

    def _cle(self, entite: OffreDePoste) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: OffreDePoste) -> dict[str, Any]:
        return {"etat": entite.etat.value, "creee_le": entite.creee_le}

    def lire(self, identifiant: str) -> OffreDePoste | None:
        return self._premier(self._requete().where(TableOffreDePoste.identifiant == identifiant))

    def lister(self) -> list[OffreDePoste]:
        return self._tous(self._requete().order_by(TableOffreDePoste.creee_le.desc()))

    def enregistrer(self, offre: OffreDePoste) -> None:
        self._poser(offre)


class DepotCandidaturesSql(DepotDocument[Candidature]):
    _table = TableCandidature
    _entite = Candidature

    def _cle(self, entite: Candidature) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: Candidature) -> dict[str, Any]:
        return {"offre": entite.offre, "recue_le": entite.recue_le}

    def lire(self, identifiant: str) -> Candidature | None:
        return self._premier(self._requete().where(TableCandidature.identifiant == identifiant))

    def de_l_offre(self, offre: str) -> list[Candidature]:
        return self._tous(
            self._requete()
            .where(TableCandidature.offre == offre)
            .order_by(TableCandidature.recue_le.desc())
        )

    def enregistrer(self, candidature: Candidature) -> None:
        self._poser(candidature)


class DepotOffresMemoire:
    def __init__(self) -> None:
        self._offres: dict[str, OffreDePoste] = {}

    def lire(self, identifiant: str) -> OffreDePoste | None:
        return self._offres.get(identifiant)

    def lister(self) -> list[OffreDePoste]:
        return sorted(self._offres.values(), key=lambda o: o.creee_le, reverse=True)

    def enregistrer(self, offre: OffreDePoste) -> None:
        self._offres[offre.identifiant] = offre


class DepotCandidaturesMemoire:
    def __init__(self) -> None:
        self._candidatures: dict[str, Candidature] = {}

    def lire(self, identifiant: str) -> Candidature | None:
        return self._candidatures.get(identifiant)

    def de_l_offre(self, offre: str) -> list[Candidature]:
        return sorted(
            (c for c in self._candidatures.values() if c.offre == offre),
            key=lambda c: c.recue_le,
            reverse=True,
        )

    def enregistrer(self, candidature: Candidature) -> None:
        self._candidatures[candidature.identifiant] = candidature


_OFFRES = DepotOffresMemoire()
_CANDIDATURES = DepotCandidaturesMemoire()


def depot_offres():
    from app.contextes.transverse.api import session_de_travail
    from app.partage.locataire import courant

    session = session_de_travail()
    return _OFFRES if session is None else DepotOffresSql(session, courant())


def depot_candidatures():
    from app.contextes.transverse.api import session_de_travail
    from app.partage.locataire import courant

    session = session_de_travail()
    return _CANDIDATURES if session is None else DepotCandidaturesSql(session, courant())
