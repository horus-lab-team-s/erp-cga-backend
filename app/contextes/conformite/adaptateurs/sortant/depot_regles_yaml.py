"""Adaptateur sortant : lecture des règles de conformité depuis des fichiers YAML.

Réalise le port `DepotRegles`. Un fichier par règle, ce qui rend une modification
lisible en revue de code — un catalogue en un seul fichier deviendrait vite
indéchiffrable en diff.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.conformite.domaine.entites import Regle

__all__ = ["DepotReglesYaml", "charger_regles"]


def charger_regles(dossier: Path) -> list[Regle]:
    """Charge toutes les règles d'un dossier.

    Une règle malformée fait échouer le chargement : mieux vaut un démarrage refusé
    qu'un contrôle silencieusement absent.
    """
    regles: list[Regle] = []
    for chemin in sorted(dossier.glob("*.yaml")):
        brut = yaml.safe_load(chemin.read_text(encoding="utf-8"))
        if not isinstance(brut, dict):
            raise ValueError(f"{chemin.name} : un objet YAML est attendu à la racine")
        try:
            regles.append(Regle.model_validate(brut))
        except Exception as exc:
            raise ValueError(f"{chemin.name} : règle invalide — {exc}") from exc

    doublons = {r.code for r in regles if sum(x.code == r.code for x in regles) > 1}
    if doublons:
        raise ValueError(f"codes de règle en double : {sorted(doublons)}")
    return regles


class DepotReglesYaml:
    """Dépôt de règles adossé à un dossier de fichiers YAML."""

    def __init__(self, dossier: Path) -> None:
        self._dossier = dossier

    def charger(self) -> list[Regle]:
        return charger_regles(self._dossier)
