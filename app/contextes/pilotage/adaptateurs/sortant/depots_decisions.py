"""Le dépôt des décisions de direction : SQL dans une requête, mémoire sinon (pas 100)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.pilotage.adaptateurs.sortant.tables import TableDecisionDeDirection
from app.contextes.pilotage.domaine.decisions import DecisionDeDirection
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotDecisionsMemoire",
    "DepotDecisionsSql",
    "depot_des_decisions_de_direction",
    "vider_les_decisions_de_direction",
]


class DepotDecisionsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus, et deux cabinets de
    démonstration ne doivent pas lire les décisions l'un de l'autre."""

    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str], DecisionDeDirection] = {}
        self._verrou = Lock()

    def du_dossier(self, dossier: str) -> list[DecisionDeDirection]:
        locataire = courant()
        with self._verrou:
            retenues = [
                d
                for (l_, _), d in self._decisions.items()
                if l_ == locataire and d.dossier == dossier
            ]
        return sorted(retenues, key=lambda d: (d.prise_le, d.identifiant))

    def enregistrer(self, decision: DecisionDeDirection) -> None:
        with self._verrou:
            self._decisions[(courant(), decision.identifiant)] = decision


class DepotDecisionsSql(DepotDocument[DecisionDeDirection]):
    _table = TableDecisionDeDirection
    _entite = DecisionDeDirection

    def _cle(self, entite: DecisionDeDirection) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: DecisionDeDirection) -> dict[str, Any]:
        return {
            "dossier": entite.dossier,
            "statut": entite.statut.value,
            "prise_le": entite.prise_le,
        }

    def du_dossier(self, dossier: str) -> list[DecisionDeDirection]:
        return self._tous(
            self._requete()
            .where(TableDecisionDeDirection.dossier == dossier)
            .order_by(TableDecisionDeDirection.prise_le, TableDecisionDeDirection.identifiant)
        )

    def enregistrer(self, decision: DecisionDeDirection) -> None:
        self._poser(decision)


@lru_cache
def _depot_memoire() -> DepotDecisionsMemoire:
    return DepotDecisionsMemoire()


def depot_des_decisions_de_direction():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotDecisionsSql(session, courant())


def vider_les_decisions_de_direction() -> None:
    """Oublie les décisions en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
