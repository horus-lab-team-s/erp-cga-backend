"""Charge le catalogue des mesures de direction depuis le référentiel (pas 100).

Un adaptateur, et rien d'autre : ce que la direction peut décider sur un dossier à risque
est écrit dans `Docs/referentiel/pilotage/mesures.yaml`, pas ici.

⚠️ Sans fichier, **aucune mesure** (`CatalogueDesMesures.prudent()`). La vue risque reste
entière : le score, ses éléments, les décisions passées. Seul le geste de décider
disparaît, et l'écran dit pourquoi. Inventer des mesures par défaut mettrait sous les yeux
de la direction des actions que personne au cabinet n'a formulées.

Un fichier présent mais mal formé lève (clé inconnue, niveau mal écrit, code en double) :
un catalogue mal transcrit ne doit pas passer pour le catalogue en vigueur.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.decisions import CatalogueDesMesures

__all__ = ["DOSSIER_MESURES", "FICHIER_MESURES", "charger_le_catalogue_des_mesures"]

DOSSIER_MESURES = "pilotage"
FICHIER_MESURES = "mesures.yaml"


def charger_le_catalogue_des_mesures(referentiel: Path) -> CatalogueDesMesures:
    chemin = referentiel / DOSSIER_MESURES / FICHIER_MESURES
    if not chemin.is_file():
        return CatalogueDesMesures.prudent()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    # Relu à chaque appel, sans mémoïsation : c'est un petit fichier, et une mesure ajoutée
    # par la direction doit apparaître au prochain écran, pas au prochain redémarrage.
    return CatalogueDesMesures.model_validate(
        {**donnees, "source": f"{DOSSIER_MESURES}/{FICHIER_MESURES}"}
    )
