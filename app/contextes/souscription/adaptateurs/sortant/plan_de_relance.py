"""Charge le plan de relance depuis le référentiel sur disque.

Un adaptateur, et rien d'autre.

Les délais et les tons sont ce que le centre décide, et il les révisera après
trois mois d'usage réel. Ce fichier convertit des jours en durées ; il ne décide
de rien.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml

from app.contextes.souscription.domaine.relance import PalierDeRelance, PlanDeRelance

__all__ = ["FICHIER_PALIERS", "charger_le_plan_de_relance"]

FICHIER_PALIERS = "paliers.yaml"


def charger_le_plan_de_relance(dossier: Path) -> PlanDeRelance:
    """Le plan tel que le centre l'a arrêté.

    Aucun repli sur un plan par défaut : un fichier absent est une installation
    incomplète, et relancer selon un calendrier inventé enverrait de vrais
    messages à de vrais clients sur une cadence que personne n'a décidée.
    """
    donnees = yaml.safe_load((dossier / FICHIER_PALIERS).read_text(encoding="utf-8"))
    return PlanDeRelance(
        paliers=tuple(
            PalierDeRelance(
                rang=entree["rang"],
                apres=timedelta(days=entree["apres_jours"]),
                modele=entree["modele"],
                ton=entree["ton"],
            )
            for entree in donnees["paliers"]
        ),
        impaye_apres=timedelta(days=donnees["impaye_apres_jours"]),
    )
