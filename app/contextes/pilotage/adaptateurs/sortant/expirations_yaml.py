"""Les paliers des alertes d'expiration (lot 4) : `Docs/referentiel/pilotage/expirations.yaml`."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.expirations import ReglagesDesExpirations

__all__ = ["charger_les_reglages_des_expirations"]


def charger_les_reglages_des_expirations(referentiel: Path) -> ReglagesDesExpirations:
    chemin = referentiel / "pilotage" / "expirations.yaml"
    if not chemin.is_file():
        return ReglagesDesExpirations()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDesExpirations.model_validate({**donnees, "source": "pilotage/expirations.yaml"})
