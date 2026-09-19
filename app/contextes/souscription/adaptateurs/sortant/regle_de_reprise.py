"""Charge la règle de reprise depuis le référentiel sur disque.

Un adaptateur, et rien d'autre. Il convertit des heures en durée et lit un
booléen ; il ne décide de rien.

⚠️ **`actif` n'a pas de valeur par défaut ici.** Un fichier absent est une
installation incomplète, et le défaut qu'on choisirait serait mauvais dans les
deux sens : à vrai, la plateforme se mettrait à réaffecter des dossiers sans
que personne l'ait décidé ; à faux, le geste s'arrêterait en silence et le
retard qu'il existe pour corriger reviendrait sans qu'aucune alerte ne le dise.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

__all__ = ["FICHIER_REPRISE", "RegleDeReprise", "charger_la_regle_de_reprise"]

FICHIER_REPRISE = "reprise.yaml"


class RegleDeReprise(BaseModel):
    """Ce que le centre a arrêté sur la reprise automatique."""

    model_config = ConfigDict(frozen=True)

    #: Le geste est-il autorisé ? Faux arrête tout, sans redéploiement, et
    #: laisse la veille en place.
    actif: bool
    #: Au bout de combien de temps sans mouvement la main est reprise.
    apres: timedelta


def charger_la_regle_de_reprise(dossier: Path) -> RegleDeReprise:
    donnees = yaml.safe_load((dossier / FICHIER_REPRISE).read_text(encoding="utf-8"))
    return RegleDeReprise(
        actif=donnees["actif"],
        apres=timedelta(hours=donnees["apres_heures"]),
    )
