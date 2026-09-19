"""Contrats de domaine du contexte C · Collecte de pièces.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types,
  aucun service, aucune entrée-sortie. C'est la seule chose qu'une couche
  `domaine` d'un autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage**. Réservé aux couches `application`
  et `adaptateurs`.

Trois contextes lisent la collecte : E pour relier une écriture à sa pièce,
F pour savoir si un dossier est déposable, J pour mesurer la friction.
"""

from __future__ import annotations

from app.contextes.collecte.domaine.demandes import (
    DemandePiece,
    RelancePiece,
    StatutDemande,
)
from app.contextes.collecte.domaine.doublons import NiveauSuspicion, SuspicionDoublon
from app.contextes.collecte.domaine.extraction import (
    ExtractionOCR,
    ValeurExtraite,
    ValeurNonRetenue,
)
from app.contextes.collecte.domaine.pieces import (
    CanalDepot,
    EtatPiece,
    PieceJustificative,
    TransitionRefusee,
    TypePiece,
)

__all__ = [
    "CanalDepot",
    "DemandePiece",
    "EtatPiece",
    "ExtractionOCR",
    "NiveauSuspicion",
    "PieceJustificative",
    "RelancePiece",
    "StatutDemande",
    "SuspicionDoublon",
    "TransitionRefusee",
    "TypePiece",
    "ValeurExtraite",
    "ValeurNonRetenue",
]
