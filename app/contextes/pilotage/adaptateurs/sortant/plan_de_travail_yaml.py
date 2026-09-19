"""Charge les réglages du plan de travail depuis le référentiel (pas 104).

`Docs/referentiel/pilotage/plan_de_travail.yaml`. Sans fichier, les valeurs sobres de
`ReglagesDuPlan` s'appliquent : le plan est une aide, jamais une condition pour travailler.
Un fichier mal formé lève : un délai mal transcrit changerait en silence ce qui est urgent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.plan_de_travail import ReglagesDuPlan

__all__ = ["charger_les_reglages_du_plan"]


def charger_les_reglages_du_plan(referentiel: Path) -> ReglagesDuPlan:
    chemin = referentiel / "pilotage" / "plan_de_travail.yaml"
    if not chemin.is_file():
        return ReglagesDuPlan()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDuPlan.model_validate({**donnees, "source": "pilotage/plan_de_travail.yaml"})
