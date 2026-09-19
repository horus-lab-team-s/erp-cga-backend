"""Les ports du contexte J · Pilotage (pas 104).

Le pilotage ne stockait rien jusqu'au pas 100 ; il conserve depuis les décisions de la
direction. Leur port vit ici, et non dans la couche application, pour que
`test_conformite_des_ports` confronte ses deux réalisations (mémoire et SQL) : un Protocol
ne vérifie rien à l'exécution, et une méthode oubliée d'un côté ne se verrait qu'en production.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.pilotage.domaine.decisions import DecisionDeDirection
from app.contextes.pilotage.domaine.rapport_mensuel import RapportMensuel

__all__ = ["DepotDecisions", "DepotRapports"]


class DepotDecisions(Protocol):
    def du_dossier(self, dossier: str) -> list[DecisionDeDirection]:
        """Les décisions du dossier, de la plus ancienne à la plus récente."""
        ...

    def enregistrer(self, decision: DecisionDeDirection) -> None: ...


class DepotRapports(Protocol):
    """Les rapports mensuels archivés (pas 106). Aucune méthode de modification ni de
    suppression : un rapport présenté au comité ne se réécrit pas, il se regénère en version
    suivante."""

    def tous(self) -> list[RapportMensuel]:
        """Du plus récent au plus ancien (mois, puis version)."""
        ...

    def trouver(self, identifiant: str) -> RapportMensuel | None: ...

    def enregistrer(self, rapport: RapportMensuel) -> None: ...
