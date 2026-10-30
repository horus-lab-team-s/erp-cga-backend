"""Le dépôt du registre des immobilisations : SQL dans une requête, mémoire sinon."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.comptabilite.adaptateurs.sortant.tables import TableImmobilisation
from app.contextes.comptabilite.domaine.immobilisations import Immobilisation
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotImmobilisationsMemoire",
    "DepotImmobilisationsSql",
    "depot_des_immobilisations",
    "vider_les_immobilisations",
]


def _ordre(i: Immobilisation):
    return (i.categorie.value, i.mise_en_service, i.code)


class DepotImmobilisationsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._fiches: dict[tuple[str, str, str], Immobilisation] = {}
        self._verrou = Lock()

    def du_dossier(self, dossier: str) -> list[Immobilisation]:
        locataire = courant()
        with self._verrou:
            retenues = [
                i
                for (loc, dos, _), i in self._fiches.items()
                if loc == locataire and dos == dossier
            ]
        return sorted(retenues, key=_ordre)

    def lire(self, dossier: str, code: str) -> Immobilisation | None:
        with self._verrou:
            return self._fiches.get((courant(), dossier, code))

    def enregistrer(self, fiche: Immobilisation) -> None:
        with self._verrou:
            self._fiches[(courant(), fiche.dossier, fiche.code)] = fiche


class DepotImmobilisationsSql(DepotDocument[Immobilisation]):
    _table = TableImmobilisation
    _entite = Immobilisation

    def _cle(self, entite: Immobilisation) -> dict[str, Any]:
        return {"locataire": self.locataire, "entreprise": entite.dossier, "code": entite.code}

    def _colonnes(self, entite: Immobilisation) -> dict[str, Any]:
        return {"categorie": entite.categorie.value, "mise_en_service": entite.mise_en_service}

    def du_dossier(self, dossier: str) -> list[Immobilisation]:
        requete = self._requete().where(TableImmobilisation.entreprise == dossier)
        return sorted(self._tous(requete), key=_ordre)

    def lire(self, dossier: str, code: str) -> Immobilisation | None:
        requete = self._requete().where(
            TableImmobilisation.entreprise == dossier, TableImmobilisation.code == code
        )
        trouvees = self._tous(requete)
        return trouvees[0] if trouvees else None

    def enregistrer(self, fiche: Immobilisation) -> None:
        self._poser(fiche)


@lru_cache
def _depot_memoire() -> DepotImmobilisationsMemoire:
    return DepotImmobilisationsMemoire()


def depot_des_immobilisations():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotImmobilisationsSql(session, courant())


def vider_les_immobilisations() -> None:
    """Oublie les fiches en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
