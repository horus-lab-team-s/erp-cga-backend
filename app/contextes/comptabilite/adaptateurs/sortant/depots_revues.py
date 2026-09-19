"""Le dépôt des revues de mois transmis : SQL dans une requête, mémoire sinon (pas 102)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.comptabilite.adaptateurs.sortant.tables import TableRevueDeDossier
from app.contextes.comptabilite.domaine.revue import RevueDeDossier
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = ["DepotRevuesMemoire", "DepotRevuesSql", "depot_des_revues", "vider_les_revues"]


def _ordre(r: RevueDeDossier):
    return (r.du, r.dossier, r.identifiant)


class DepotRevuesMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._revues: dict[tuple[str, str, str], RevueDeDossier] = {}
        self._verrou = Lock()

    def toutes(self) -> list[RevueDeDossier]:
        locataire = courant()
        with self._verrou:
            retenues = [r for (l_, _, _), r in self._revues.items() if l_ == locataire]
        return sorted(retenues, key=_ordre)

    def du_dossier(self, dossier: str) -> list[RevueDeDossier]:
        return [r for r in self.toutes() if r.dossier == dossier]

    def enregistrer(self, revue: RevueDeDossier) -> None:
        with self._verrou:
            self._revues[(courant(), revue.dossier, revue.identifiant)] = revue


class DepotRevuesSql(DepotDocument[RevueDeDossier]):
    _table = TableRevueDeDossier
    _entite = RevueDeDossier

    def _cle(self, entite: RevueDeDossier) -> dict[str, Any]:
        return {
            "locataire": self.locataire,
            "entreprise": entite.dossier,
            "identifiant": entite.identifiant,
        }

    def _colonnes(self, entite: RevueDeDossier) -> dict[str, Any]:
        return {"du": entite.du, "au": entite.au, "statut": entite.statut.value}

    def toutes(self) -> list[RevueDeDossier]:
        return sorted(self._tous(self._requete()), key=_ordre)

    def du_dossier(self, dossier: str) -> list[RevueDeDossier]:
        return sorted(
            self._tous(self._requete().where(TableRevueDeDossier.entreprise == dossier)),
            key=_ordre,
        )

    def enregistrer(self, revue: RevueDeDossier) -> None:
        self._poser(revue)


@lru_cache
def _depot_memoire() -> DepotRevuesMemoire:
    return DepotRevuesMemoire()


def depot_des_revues():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotRevuesSql(session, courant())


def vider_les_revues() -> None:
    """Oublie les revues en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
