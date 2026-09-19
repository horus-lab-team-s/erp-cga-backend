"""Charge les réglages de l'échantillon de revue depuis le référentiel (pas 102).

`Docs/referentiel/revue/echantillon.yaml`. Sans fichier, des valeurs sobres s'appliquent :
l'échantillon est une aide au réviseur, jamais une condition pour travailler. Un fichier
présent mais mal formé lève : un seuil mal transcrit changerait en silence ce qui est relu.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.comptabilite.domaine.revue import ReglagesDeLEchantillon

__all__ = ["charger_les_reglages_de_l_echantillon"]


def charger_les_reglages_de_l_echantillon(referentiel: Path) -> ReglagesDeLEchantillon:
    chemin = referentiel / "revue" / "echantillon.yaml"
    if not chemin.is_file():
        return ReglagesDeLEchantillon()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLEchantillon.model_validate({**donnees, "source": "revue/echantillon.yaml"})
