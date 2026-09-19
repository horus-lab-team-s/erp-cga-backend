"""Le dépôt des rapports mensuels : SQL dans une requête, mémoire sinon (pas 106)."""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.pilotage.adaptateurs.sortant.tables import TableRapportMensuel
from app.contextes.pilotage.domaine.rapport_mensuel import RapportMensuel
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotRapportsMemoire",
    "DepotRapportsSql",
    "depot_des_rapports",
    "vider_les_rapports",
]


def _ordre(r: RapportMensuel):
    return (r.mois, r.version)


class DepotRapportsMemoire:
    """Rangé par locataire : le dépôt est partagé par le processus."""

    def __init__(self) -> None:
        self._rapports: dict[tuple[str, str], RapportMensuel] = {}
        self._verrou = Lock()

    def tous(self) -> list[RapportMensuel]:
        locataire = courant()
        with self._verrou:
            retenus = [r for (l_, _), r in self._rapports.items() if l_ == locataire]
        return sorted(retenus, key=_ordre, reverse=True)

    def trouver(self, identifiant: str) -> RapportMensuel | None:
        return self._rapports.get((courant(), identifiant))

    def enregistrer(self, rapport: RapportMensuel) -> None:
        with self._verrou:
            self._rapports[(courant(), rapport.identifiant)] = rapport


class DepotRapportsSql(DepotDocument[RapportMensuel]):
    _table = TableRapportMensuel
    _entite = RapportMensuel

    def _cle(self, entite: RapportMensuel) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: RapportMensuel) -> dict[str, Any]:
        return {"mois": entite.mois, "version": entite.version, "empreinte": entite.empreinte}

    def tous(self) -> list[RapportMensuel]:
        return sorted(self._tous(self._requete()), key=_ordre, reverse=True)

    def trouver(self, identifiant: str) -> RapportMensuel | None:
        return self._premier(self._requete().where(TableRapportMensuel.identifiant == identifiant))

    def enregistrer(self, rapport: RapportMensuel) -> None:
        self._poser(rapport)


@lru_cache
def _depot_memoire() -> DepotRapportsMemoire:
    return DepotRapportsMemoire()


def depot_des_rapports():
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotRapportsSql(session, courant())


def vider_les_rapports() -> None:
    """Oublie les rapports en mémoire. Destinée aux tests."""
    _depot_memoire.cache_clear()
