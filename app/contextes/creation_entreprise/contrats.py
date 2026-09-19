"""Contrats du contexte I · Création d'entreprise — rang 1.

N'expose que des entités, jamais un cas d'usage : c'est ce qui permet à un
domaine voisin de les emprunter sans emprunter du même coup une dépendance vers
une couche plus externe.

Un seul consommateur est prévu — J · Pilotage, qui compte les dossiers du
pipeline pour l'indicateur « où en sont les créations ». Il lit l'étape et la
date d'immobilité, rien d'autre.
"""

from __future__ import annotations

from app.contextes.creation_entreprise.domaine.entites import (
    DossierCreation,
    EtapeCreation,
    Fondateur,
    Immatriculation,
    Jalon,
    PieceConstitution,
    TransitionInterdite,
    etapes_ouvertes_depuis,
)

__all__ = [
    "DossierCreation",
    "EtapeCreation",
    "Fondateur",
    "Immatriculation",
    "Jalon",
    "PieceConstitution",
    "TransitionInterdite",
    "etapes_ouvertes_depuis",
]
