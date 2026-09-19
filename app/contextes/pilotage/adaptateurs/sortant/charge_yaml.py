"""Charge les réglages de la charge des collaborateurs depuis le référentiel (pas 105).

`Docs/referentiel/pilotage/charge.yaml`. Sans fichier, les valeurs de départ de
`ReglagesDeLaCharge` s'appliquent. Un fichier mal formé lève : une capacité mal transcrite
ferait paraître saturé quelqu'un qui ne l'est pas, et proposerait de lui retirer des dossiers.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.charge_et_production import ReglagesDeLaCharge

__all__ = ["charger_les_reglages_de_la_charge"]


def charger_les_reglages_de_la_charge(referentiel: Path) -> ReglagesDeLaCharge:
    chemin = referentiel / "pilotage" / "charge.yaml"
    if not chemin.is_file():
        return ReglagesDeLaCharge()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLaCharge.model_validate({**donnees, "source": "pilotage/charge.yaml"})
