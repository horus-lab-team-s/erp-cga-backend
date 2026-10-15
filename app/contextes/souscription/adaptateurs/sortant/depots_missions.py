"""Les missions des prestations ponctuelles : en base dans une requête, en mémoire sinon."""

from __future__ import annotations

from typing import Any

from app.contextes.souscription.adaptateurs.sortant.tables import TableMission
from app.contextes.souscription.domaine.missions import Mission
from app.infrastructure.depot_document import DepotDocument

__all__ = ["DepotMissionsMemoire", "DepotMissionsSql", "missions_en_memoire"]


class DepotMissionsSql(DepotDocument[Mission]):
    _table = TableMission
    _entite = Mission

    def _cle(self, entite: Mission) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: Mission) -> dict[str, Any]:
        return {
            "type": entite.type.value,
            "etat": entite.etat.value,
            "ouverte_le": entite.ouverte_le,
        }

    def lire(self, identifiant: str) -> Mission | None:
        return self._premier(self._requete().where(TableMission.identifiant == identifiant))

    def lister(self) -> list[Mission]:
        return self._tous(self._requete().order_by(TableMission.ouverte_le.desc()))

    def enregistrer(self, mission: Mission) -> None:
        self._poser(mission)


class DepotMissionsMemoire:
    def __init__(self) -> None:
        self._missions: dict[str, Mission] = {}

    def lire(self, identifiant: str) -> Mission | None:
        return self._missions.get(identifiant)

    def lister(self) -> list[Mission]:
        return sorted(self._missions.values(), key=lambda m: m.ouverte_le, reverse=True)

    def enregistrer(self, mission: Mission) -> None:
        self._missions[mission.identifiant] = mission


_MEMOIRE = DepotMissionsMemoire()


def missions_en_memoire() -> DepotMissionsMemoire:
    return _MEMOIRE
