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

from app.contextes.referentiel.domaine.entites import Bareme, Parametre

__all__ = [
    "DepotBaremesYaml",
    "DepotParametresYaml",
    "charger_baremes",
    "charger_parametres",
]


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


def charger_baremes(chemin: Path) -> list[Bareme]:
    """Lit et valide un fichier de barèmes progressifs.

    Un fichier absent rend une liste vide plutôt que de lever : tous les
    déploiements n'ont pas de barème, et le premier à en avoir besoin est la paie.
    Un fichier **présent et malformé**, lui, fait échouer le chargement — c'est la
    même discipline que pour les paramètres, et pour la même raison : un barème
    amputé de sa dernière tranche calculerait un impôt faux sur les hauts revenus,
    en silence.
    """
    if not chemin.exists():
        return []
    contenu = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    if not isinstance(contenu, dict) or "baremes" not in contenu:
        raise ValueError(f"{chemin} : clé « baremes » attendue à la racine")
    return [Bareme.model_validate(brut) for brut in contenu["baremes"]]


class DepotBaremesYaml:
    """Dépôt de barèmes adossé à un fichier YAML."""

    def __init__(self, chemin: Path) -> None:
        self._chemin = chemin

    def charger(self) -> list[Bareme]:
        return charger_baremes(self._chemin)
