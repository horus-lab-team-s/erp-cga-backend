"""Charge le plan de contact depuis le référentiel sur disque.

Un adaptateur, et rien d'autre.

Le plan dit quels canaux le centre exploite. C'est **sa** décision, et elle change :
ouvrir un compte de messagerie, arrêter le courriel automatique, réserver un canal.
Le jour où la messagerie s'ouvre, un `actif: true` dans un fichier suffit.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.souscription.domaine.canaux import EtatCanal, PlanDeContact

__all__ = ["FICHIER_CANAUX", "charger_le_plan_de_contact"]

FICHIER_CANAUX = "canaux.yaml"


def charger_le_plan_de_contact(dossier: Path) -> PlanDeContact:
    """Le plan tel que le centre l'a arrêté.

    Aucun repli silencieux sur un plan par défaut : un fichier absent est une
    installation incomplète, et démarrer avec un plan inventé ferait joindre les
    clients par un canal que personne n'a décidé d'exploiter.
    """
    chemin = dossier / FICHIER_CANAUX
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    return PlanDeContact(
        canaux=tuple(EtatCanal.model_validate(entree) for entree in donnees["canaux"])
    )
