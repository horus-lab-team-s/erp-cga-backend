"""Adaptateur sortant : lecture des paramètres depuis des fichiers YAML.

Réalise le port `DepotParametres` du domaine. C'est le seul endroit du contexte A qui
touche un système de fichiers.

Étape suivante prévue : un `DepotParametresSql` réalisant le même port, alimenté par
la table `parametre_fiscal` et éditable via l'écran E11. Le remplacement se fera à la
composition, sans toucher au cas d'usage.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.referentiel.domaine.entites import Parametre

__all__ = ["DepotParametresYaml", "charger_parametres"]


def charger_parametres(chemin: Path) -> list[Parametre]:
    """Lit et valide un fichier de paramètres.

    Un fichier malformé fait échouer le chargement : mieux vaut un démarrage refusé
    qu'un référentiel amputé de moitié sans que personne ne s'en aperçoive.
    """
    contenu = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    if not isinstance(contenu, dict) or "parametres" not in contenu:
        raise ValueError(f"{chemin} : clé « parametres » attendue à la racine")
    return [Parametre.model_validate(brut) for brut in contenu["parametres"]]


class DepotParametresYaml:
    """Dépôt de paramètres adossé à un fichier YAML."""

    def __init__(self, chemin: Path) -> None:
        self._chemin = chemin

    def charger(self) -> list[Parametre]:
        return charger_parametres(self._chemin)
