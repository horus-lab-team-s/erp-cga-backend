"""Les dépôts de la lecture au dépôt (18 octobre 2026). Voir `domaine/lecture_au_depot.py`."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.contextes.collecte.adaptateurs.sortant.tables import (
    TableConsentementDeLecture,
    TableLecturePreparee,
    TableReglageDeLecture,
)
from app.contextes.collecte.domaine.lecture_au_depot import (
    ConsentementDeLecture,
    LecturePreparee,
    ReglageDeLecture,
)
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = ["depots_de_la_lecture"]


class _ReglageSql(DepotDocument[ReglageDeLecture]):
    _table = TableReglageDeLecture
    _entite = ReglageDeLecture

    def _cle(self, entite: ReglageDeLecture) -> dict[str, Any]:
        return {"locataire": self.locataire}

    def _colonnes(self, entite: ReglageDeLecture) -> dict[str, Any]:
        return {}

    def lire(self) -> ReglageDeLecture | None:
        return self._premier(self._requete())

    def enregistrer(self, reglage: ReglageDeLecture) -> None:
        self._poser(reglage)


class _ConsentementsSql(DepotDocument[ConsentementDeLecture]):
    _table = TableConsentementDeLecture
    _entite = ConsentementDeLecture

    def _cle(self, entite: ConsentementDeLecture) -> dict[str, Any]:
        return {"locataire": self.locataire, "niu": entite.niu}

    def _colonnes(self, entite: ConsentementDeLecture) -> dict[str, Any]:
        return {}

    def lire(self, niu: str) -> ConsentementDeLecture | None:
        return self._premier(self._requete().where(TableConsentementDeLecture.niu == niu))

    def accordes(self) -> list[ConsentementDeLecture]:
        return [c for c in self._tous(self._requete()) if c.accorde]

    def enregistrer(self, consentement: ConsentementDeLecture) -> None:
        self._poser(consentement)


class _LecturesSql(DepotDocument[LecturePreparee]):
    _table = TableLecturePreparee
    _entite = LecturePreparee

    def _cle(self, entite: LecturePreparee) -> dict[str, Any]:
        return {"locataire": self.locataire, "piece": entite.piece}

    def _colonnes(self, entite: LecturePreparee) -> dict[str, Any]:
        return {"entreprise": entite.entreprise}

    def lire(self, piece: str) -> LecturePreparee | None:
        return self._premier(self._requete().where(TableLecturePreparee.piece == piece))

    def enregistrer(self, lecture: LecturePreparee) -> None:
        self._poser(lecture)


class _ReglageMemoire:
    def __init__(self) -> None:
        self._reglage: ReglageDeLecture | None = None

    def lire(self) -> ReglageDeLecture | None:
        return self._reglage

    def enregistrer(self, reglage: ReglageDeLecture) -> None:
        self._reglage = reglage


class _ConsentementsMemoire:
    def __init__(self) -> None:
        self._consentements: dict[str, ConsentementDeLecture] = {}

    def lire(self, niu: str) -> ConsentementDeLecture | None:
        return self._consentements.get(niu)

    def accordes(self) -> list[ConsentementDeLecture]:
        return [c for c in self._consentements.values() if c.accorde]

    def enregistrer(self, consentement: ConsentementDeLecture) -> None:
        self._consentements[consentement.niu] = consentement


class _LecturesMemoire:
    def __init__(self) -> None:
        self._lectures: dict[str, LecturePreparee] = {}

    def lire(self, piece: str) -> LecturePreparee | None:
        return self._lectures.get(piece)

    def enregistrer(self, lecture: LecturePreparee) -> None:
        self._lectures[lecture.piece] = lecture


@lru_cache
def _memoire() -> tuple[_ReglageMemoire, _ConsentementsMemoire, _LecturesMemoire]:
    return _ReglageMemoire(), _ConsentementsMemoire(), _LecturesMemoire()


def depots_de_la_lecture():
    """Le réglage, les accords et les lectures : en base dans une requête, en mémoire sinon."""
    session = session_de_travail()
    if session is None:
        return _memoire()
    locataire = courant()
    return (
        _ReglageSql(session, locataire),
        _ConsentementsSql(session, locataire),
        _LecturesSql(session, locataire),
    )
