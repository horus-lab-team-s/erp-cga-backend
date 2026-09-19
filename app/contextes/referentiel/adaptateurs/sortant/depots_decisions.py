"""Le dépôt des décisions d'un cabinet sur le référentiel : SQL si une session est ouverte,
mémoire sinon (pas 95).

⚠️ La session vient de `app.infrastructure.base_de_donnees.session_courante()`, et non du
transverse : le référentiel appartient au socle, et lire le transverse créerait un cycle.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.referentiel.adaptateurs.sortant.tables import TableDecisionReferentiel
from app.contextes.referentiel.domaine.ports import DepotDecisions
from app.contextes.referentiel.domaine.surcouche import DecisionSurLeReferentiel
from app.infrastructure.base_de_donnees import session_courante
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotDecisionsMemoire",
    "DepotDecisionsSql",
    "depot_des_decisions",
    "vider_les_decisions",
]


class DepotDecisionsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str], DecisionSurLeReferentiel] = {}
        self._verrou = Lock()

    def toutes(self) -> list[DecisionSurLeReferentiel]:
        locataire = courant()
        with self._verrou:
            retenues = [d for (l_, _), d in self._decisions.items() if l_ == locataire]
        return sorted(retenues, key=lambda d: (d.propose_le, d.identifiant))

    def trouver(self, identifiant: str) -> DecisionSurLeReferentiel | None:
        return self._decisions.get((courant(), identifiant))

    def enregistrer(self, decision: DecisionSurLeReferentiel) -> None:
        with self._verrou:
            self._decisions[(courant(), decision.identifiant)] = decision


class DepotDecisionsSql(DepotDocument[DecisionSurLeReferentiel]):
    _table = TableDecisionReferentiel
    _entite = DecisionSurLeReferentiel

    def _cle(self, entite: DecisionSurLeReferentiel) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: DecisionSurLeReferentiel) -> dict[str, Any]:
        return {"code": entite.code, "statut": entite.statut.value, "propose_le": entite.propose_le}

    def toutes(self) -> list[DecisionSurLeReferentiel]:
        return self._tous(
            self._requete().order_by(
                TableDecisionReferentiel.propose_le, TableDecisionReferentiel.identifiant
            )
        )

    def trouver(self, identifiant: str) -> DecisionSurLeReferentiel | None:
        return self._premier(
            self._requete().where(TableDecisionReferentiel.identifiant == identifiant)
        )

    def enregistrer(self, decision: DecisionSurLeReferentiel) -> None:
        self._poser(decision)


@lru_cache
def _depot_memoire() -> DepotDecisionsMemoire:
    return DepotDecisionsMemoire()


def depot_des_decisions() -> DepotDecisions:
    session = session_courante()
    if session is None:
        return _depot_memoire()
    return DepotDecisionsSql(session, courant())


def vider_les_decisions() -> None:
    """Oublie les décisions en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
