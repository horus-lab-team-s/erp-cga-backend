"""Le dépôt des règles construites par un cabinet : SQL dans une requête, mémoire sinon (pas 97)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.conformite.adaptateurs.sortant.tables import TablePropositionDeRegle
from app.contextes.conformite.domaine.regles_du_cabinet import PropositionDeRegle
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotPropositionsMemoire",
    "DepotPropositionsSql",
    "depot_des_regles_du_cabinet",
    "vider_les_regles_du_cabinet",
]


class DepotPropositionsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._propositions: dict[tuple[str, str], PropositionDeRegle] = {}
        self._verrou = Lock()

    def toutes(self) -> list[PropositionDeRegle]:
        locataire = courant()
        with self._verrou:
            retenues = [p for (l_, _), p in self._propositions.items() if l_ == locataire]
        return sorted(retenues, key=lambda p: (p.propose_le, p.identifiant))

    def trouver(self, identifiant: str) -> PropositionDeRegle | None:
        return self._propositions.get((courant(), identifiant))

    def enregistrer(self, proposition: PropositionDeRegle) -> None:
        with self._verrou:
            self._propositions[(courant(), proposition.identifiant)] = proposition


class DepotPropositionsSql(DepotDocument[PropositionDeRegle]):
    _table = TablePropositionDeRegle
    _entite = PropositionDeRegle

    def _cle(self, entite: PropositionDeRegle) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: PropositionDeRegle) -> dict[str, Any]:
        return {"statut": entite.statut.value, "propose_le": entite.propose_le}

    def toutes(self) -> list[PropositionDeRegle]:
        return self._tous(
            self._requete().order_by(
                TablePropositionDeRegle.propose_le, TablePropositionDeRegle.identifiant
            )
        )

    def trouver(self, identifiant: str) -> PropositionDeRegle | None:
        return self._premier(
            self._requete().where(TablePropositionDeRegle.identifiant == identifiant)
        )

    def enregistrer(self, proposition: PropositionDeRegle) -> None:
        self._poser(proposition)


@lru_cache
def _depot_memoire() -> DepotPropositionsMemoire:
    return DepotPropositionsMemoire()


def depot_des_regles_du_cabinet():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotPropositionsSql(session, courant())


def vider_les_regles_du_cabinet() -> None:
    """Oublie les propositions en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
