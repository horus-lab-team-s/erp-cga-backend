"""Les dépôts des écarts de constats : SQL dans une requête, mémoire sinon (pas 92).

Même choix que le social (voir `social/adaptateurs/sortant/magasins.py`) : le contexte
propriétaire tient **le seul** choix du dépôt, et ses routes comme ses voisins le
lisent par `depot_des_ecarts()`. Deux choix séparés finiraient par lire les écarts en
mémoire pendant qu'on les écrit en base, et un écart confirmé ne s'appliquerait pas.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from app.contextes.conformite.adaptateurs.sortant.tables import TableEcartDeConstat
from app.contextes.conformite.domaine.ecarts import EcartDeConstat, StatutEcart
from app.contextes.conformite.domaine.ports import DepotEcarts
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = [
    "DepotEcartsMemoire",
    "DepotEcartsSql",
    "depot_des_ecarts",
    "vider_les_ecarts",
]


class DepotEcartsMemoire:
    """Les écarts en mémoire, **rangés par locataire**.

    ⚠️ Le dépôt est partagé par tout le processus. Sans le locataire dans la clé, un
    écart posé par un cabinet s'appliquerait au rapport d'un autre cabinet dont un
    client aurait reçu une facture de même référence.
    """

    def __init__(self) -> None:
        self._ecarts: dict[tuple[str, str], EcartDeConstat] = {}
        self._verrou = Lock()

    def pour_la_piece(self, dossier: str, reference_document: str) -> list[EcartDeConstat]:
        locataire = courant()
        with self._verrou:
            retenus = [
                e
                for (proprietaire, _), e in self._ecarts.items()
                if proprietaire == locataire
                and e.dossier == dossier
                and e.reference_document == reference_document
            ]
        return sorted(retenus, key=lambda e: (e.propose_le, e.identifiant))

    def toutes(self) -> list[EcartDeConstat]:
        locataire = courant()
        with self._verrou:
            retenus = [
                e for (proprietaire, _), e in self._ecarts.items() if proprietaire == locataire
            ]
        return sorted(retenus, key=lambda e: (e.propose_le, e.identifiant))

    def en_attente(self) -> list[EcartDeConstat]:
        locataire = courant()
        with self._verrou:
            retenus = [
                e
                for (proprietaire, _), e in self._ecarts.items()
                if proprietaire == locataire and e.statut is StatutEcart.EN_ATTENTE
            ]
        return sorted(retenus, key=lambda e: (e.propose_le, e.identifiant))

    def enregistrer(self, ecart: EcartDeConstat) -> None:
        with self._verrou:
            self._ecarts[(courant(), ecart.identifiant)] = ecart


class DepotEcartsSql(DepotDocument[EcartDeConstat]):
    """Les écarts en base, une ligne par décision."""

    _table = TableEcartDeConstat
    _entite = EcartDeConstat

    def _cle(self, entite: EcartDeConstat) -> dict[str, Any]:
        return {"locataire": self.locataire, "identifiant": entite.identifiant}

    def _colonnes(self, entite: EcartDeConstat) -> dict[str, Any]:
        return {
            "dossier": entite.dossier,
            "reference_document": entite.reference_document,
            "statut": entite.statut.value,
            "propose_le": entite.propose_le,
        }

    def pour_la_piece(self, dossier: str, reference_document: str) -> list[EcartDeConstat]:
        return self._tous(
            self._requete()
            .where(TableEcartDeConstat.dossier == dossier)
            .where(TableEcartDeConstat.reference_document == reference_document)
            .order_by(TableEcartDeConstat.propose_le, TableEcartDeConstat.identifiant)
        )

    def toutes(self) -> list[EcartDeConstat]:
        return self._tous(
            self._requete().order_by(
                TableEcartDeConstat.propose_le, TableEcartDeConstat.identifiant
            )
        )

    def en_attente(self) -> list[EcartDeConstat]:
        return self._tous(
            self._requete()
            .where(TableEcartDeConstat.statut == StatutEcart.EN_ATTENTE.value)
            .order_by(TableEcartDeConstat.propose_le, TableEcartDeConstat.identifiant)
        )

    def enregistrer(self, ecart: EcartDeConstat) -> None:
        self._poser(ecart)


def depot_des_ecarts() -> DepotEcarts:
    """Le dépôt en vigueur : celui de la session SQL de la requête, s'il y en a une."""
    session = session_de_travail()
    if session is None:
        return _depot_memoire()
    return DepotEcartsSql(session, courant())


@lru_cache
def _depot_memoire() -> DepotEcartsMemoire:
    return DepotEcartsMemoire()


def vider_les_ecarts() -> None:
    """Oublie les écarts en mémoire. Destinée aux tests et à la réinitialisation."""
    _depot_memoire.cache_clear()
