"""Le dépôt des rapprochements bancaires : SQL dans une requête, mémoire sinon (pas 101)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.comptabilite.adaptateurs.sortant.tables import TableRapprochementBancaire
from app.contextes.comptabilite.domaine.rapprochement import RapprochementBancaire
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotRapprochementsMemoire",
    "DepotRapprochementsSql",
    "depot_des_rapprochements",
    "vider_les_rapprochements",
]


def _ordre(r: RapprochementBancaire):
    return (r.journal, r.au, r.identifiant)


class DepotRapprochementsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._rapprochements: dict[tuple[str, str, str], RapprochementBancaire] = {}
        self._verrou = Lock()

    def du_dossier(self, dossier: str) -> list[RapprochementBancaire]:
        locataire = courant()
        with self._verrou:
            retenus = [
                r
                for (l_, d, _), r in self._rapprochements.items()
                if l_ == locataire and d == dossier
            ]
        return sorted(retenus, key=_ordre)

    def enregistrer(self, rapprochement: RapprochementBancaire) -> None:
        with self._verrou:
            cle = (courant(), rapprochement.dossier, rapprochement.identifiant)
            self._rapprochements[cle] = rapprochement


class DepotRapprochementsSql(DepotDocument[RapprochementBancaire]):
    _table = TableRapprochementBancaire
    _entite = RapprochementBancaire

    def _cle(self, entite: RapprochementBancaire) -> dict[str, Any]:
        return {
            "locataire": self.locataire,
            "entreprise": entite.dossier,
            "identifiant": entite.identifiant,
        }

    def _colonnes(self, entite: RapprochementBancaire) -> dict[str, Any]:
        return {
            "journal": entite.journal,
            "du": entite.du,
            "au": entite.au,
            "statut": entite.statut.value,
        }

    def du_dossier(self, dossier: str) -> list[RapprochementBancaire]:
        return sorted(
            self._tous(self._requete().where(TableRapprochementBancaire.entreprise == dossier)),
            key=_ordre,
        )

    def enregistrer(self, rapprochement: RapprochementBancaire) -> None:
        self._poser(rapprochement)


@lru_cache
def _depot_memoire() -> DepotRapprochementsMemoire:
    return DepotRapprochementsMemoire()


def depot_des_rapprochements():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotRapprochementsSql(session, courant())


def vider_les_rapprochements() -> None:
    """Oublie les rapprochements en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
