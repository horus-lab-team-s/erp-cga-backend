"""La position de lecture des notifications : SQL dans une requête, mémoire sinon (pas 94)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session as SessionSql

from app.contextes.transverse.adaptateurs.sortant.tables import TableLectureDesNotifications
from app.contextes.transverse.domaine.ports import DepotLectures
from app.partage.locataire import courant

__all__ = [
    "DepotLecturesMemoire",
    "DepotLecturesSql",
    "depot_des_lectures",
    "vider_les_lectures",
]


class DepotLecturesMemoire:
    """Rangé par locataire : le dépôt est partagé par tout le processus."""

    def __init__(self) -> None:
        self._positions: dict[tuple[str, str], int] = {}
        self._verrou = Lock()

    def lu_jusqu_au_rang(self, compte: str) -> int | None:
        return self._positions.get((courant(), compte))

    def avancer(self, compte: str, rang: int) -> int:
        cle = (courant(), compte)
        with self._verrou:
            retenu = max(self._positions.get(cle, 0), rang)
            self._positions[cle] = retenu
        return retenu


class DepotLecturesSql:
    def __init__(self, session: SessionSql, locataire: str) -> None:
        self._session = session
        self.locataire = locataire

    def _ligne(self, compte: str) -> TableLectureDesNotifications | None:
        return self._session.scalars(
            select(TableLectureDesNotifications)
            .where(TableLectureDesNotifications.locataire == self.locataire)
            .where(TableLectureDesNotifications.compte == compte)
        ).first()

    def lu_jusqu_au_rang(self, compte: str) -> int | None:
        ligne = self._ligne(compte)
        return None if ligne is None else ligne.lu_jusqu_au_rang

    def avancer(self, compte: str, rang: int) -> int:
        ligne = self._ligne(compte)
        if ligne is None:
            ligne = TableLectureDesNotifications(
                locataire=self.locataire, compte=compte, lu_jusqu_au_rang=rang
            )
            self._session.add(ligne)
        else:
            ligne.lu_jusqu_au_rang = max(ligne.lu_jusqu_au_rang, rang)
        self._session.flush()
        return ligne.lu_jusqu_au_rang


@lru_cache
def _depot_memoire() -> DepotLecturesMemoire:
    return DepotLecturesMemoire()


def depot_des_lectures(session: SessionSql | None) -> DepotLectures:
    """Le dépôt de la session SQL de la requête, ou celui du processus en mémoire."""
    if session is None:
        return _depot_memoire()
    return DepotLecturesSql(session, courant())


def vider_les_lectures() -> None:
    """Oublie les positions en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
