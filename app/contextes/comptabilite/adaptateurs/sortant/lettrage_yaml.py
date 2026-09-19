"""Charge les réglages du lettrage depuis le référentiel (pas 108).

`Docs/referentiel/lettrage/reglages.yaml`. Sans fichier, les valeurs prudentes du domaine :
aucun écart toléré, un même tiers exigé. Un fichier présent mais mal formé lève : un écart
toléré mal transcrit laisserait lettrer des restes dus.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.comptabilite.domaine.lettrage import ReglagesDuLettrage

__all__ = ["charger_les_reglages_du_lettrage"]


def charger_les_reglages_du_lettrage(referentiel: Path) -> ReglagesDuLettrage:
    chemin = referentiel / "lettrage" / "reglages.yaml"
    if not chemin.is_file():
        return ReglagesDuLettrage()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDuLettrage.model_validate({**donnees, "source": "lettrage/reglages.yaml"})
