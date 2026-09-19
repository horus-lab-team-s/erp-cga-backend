"""Charge les réglages de la file d'anomalies (pas 117).

`Docs/referentiel/pilotage/file_d_anomalies.yaml`. Sans fichier, les valeurs du domaine (quinze
jours, les trois gravités qui appellent une décision). Un fichier mal formé lève : une file sans
gravité, ou sans les bloquants, ne doit pas s'afficher comme « rien à traiter ».
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.file_d_anomalies import ReglagesDeLaFile

__all__ = ["charger_les_reglages_de_la_file"]


def charger_les_reglages_de_la_file(referentiel: Path) -> ReglagesDeLaFile:
    chemin = referentiel / "pilotage" / "file_d_anomalies.yaml"
    if not chemin.is_file():
        return ReglagesDeLaFile()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLaFile.model_validate({**donnees, "source": "pilotage/file_d_anomalies.yaml"})
