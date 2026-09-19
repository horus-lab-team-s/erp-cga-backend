"""Contrats de domaine du contexte F · Obligations et déclarations.

**Deux surfaces publiques, pas une.** `contrats.py` n'expose que des entités
pures — c'est la seule chose qu'une couche `domaine` d'un autre contexte a le
droit d'importer. `api.py` expose en plus les cas d'usage.

Consommateurs prévus : `cloture` (la DSF est elle-même une obligation
déclarative), `pilotage` (retards, charge, dossiers à risque).
"""

from __future__ import annotations

from app.contextes.obligations.domaine.echeances import (
    Penalite,
    Periodicite,
    TypeObligation,
)
from app.contextes.obligations.domaine.obligations import (
    ObligationInstance,
    Relance,
    StatutObligation,
)

__all__ = [
    "ObligationInstance",
    "Penalite",
    "Periodicite",
    "Relance",
    "StatutObligation",
    "TypeObligation",
]
