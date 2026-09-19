"""Le dépôt des lettrages : SQL dans une requête, mémoire sinon (pas 108)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.comptabilite.adaptateurs.sortant.tables import TableLettrage
from app.contextes.comptabilite.domaine.lettrage import LettrageDeLignes
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotLettragesMemoire",
    "DepotLettragesSql",
    "depot_des_lettrages",
    "vider_les_lettrages",
]


def _ordre(l_: LettrageDeLignes):
    # La lettre dans l'ordre d'attribution : B avant AA, d'où la longueur d'abord.
    return (l_.compte, len(l_.lettre), l_.lettre)


class DepotLettragesMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._lettrages: dict[tuple[str, str, str], LettrageDeLignes] = {}
        self._verrou = Lock()

    def du_dossier(self, dossier: str, exercice: str) -> list[LettrageDeLignes]:
        locataire = courant()
        with self._verrou:
            retenus = [
                l_
                for (loc, dos, _), l_ in self._lettrages.items()
                if loc == locataire and dos == dossier and l_.exercice == exercice
            ]
        return sorted(retenus, key=_ordre)

    def du_compte(self, dossier: str, exercice: str, compte: str) -> list[LettrageDeLignes]:
        return [l_ for l_ in self.du_dossier(dossier, exercice) if l_.compte == compte]

    def enregistrer(self, lettrage: LettrageDeLignes) -> None:
        with self._verrou:
            self._lettrages[(courant(), lettrage.dossier, lettrage.identifiant)] = lettrage


class DepotLettragesSql(DepotDocument[LettrageDeLignes]):
    _table = TableLettrage
    _entite = LettrageDeLignes

    def _cle(self, entite: LettrageDeLignes) -> dict[str, Any]:
        return {
            "locataire": self.locataire,
            "entreprise": entite.dossier,
            "identifiant": entite.identifiant,
        }

    def _colonnes(self, entite: LettrageDeLignes) -> dict[str, Any]:
        return {
            "exercice": entite.exercice,
            "compte": entite.compte,
            "statut": entite.statut.value,
        }

    def du_dossier(self, dossier: str, exercice: str) -> list[LettrageDeLignes]:
        requete = self._requete().where(
            TableLettrage.entreprise == dossier, TableLettrage.exercice == exercice
        )
        return sorted(self._tous(requete), key=_ordre)

    def du_compte(self, dossier: str, exercice: str, compte: str) -> list[LettrageDeLignes]:
        requete = self._requete().where(
            TableLettrage.entreprise == dossier,
            TableLettrage.exercice == exercice,
            TableLettrage.compte == compte,
        )
        return sorted(self._tous(requete), key=_ordre)

    def enregistrer(self, lettrage: LettrageDeLignes) -> None:
        self._poser(lettrage)


@lru_cache
def _depot_memoire() -> DepotLettragesMemoire:
    return DepotLettragesMemoire()


def depot_des_lettrages():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotLettragesSql(session, courant())


def vider_les_lettrages() -> None:
    """Oublie les lettrages en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
