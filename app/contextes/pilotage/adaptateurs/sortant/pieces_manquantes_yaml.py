"""Charge les réglages des pièces manquantes et de leur relance (pas 111).

`Docs/referentiel/pilotage/pieces_manquantes.yaml`. Sans fichier, les valeurs du domaine. Un
fichier mal formé lève : un modèle qui tairait la date limite, un canal inactif sans motif ou
coché d'office ne doivent pas partir en silence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.pieces_manquantes import ReglagesDesPiecesManquantes

__all__ = ["charger_les_reglages_des_pieces_manquantes"]


def charger_les_reglages_des_pieces_manquantes(referentiel: Path) -> ReglagesDesPiecesManquantes:
    chemin = referentiel / "pilotage" / "pieces_manquantes.yaml"
    if not chemin.is_file():
        return ReglagesDesPiecesManquantes()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDesPiecesManquantes.model_validate(
        {**donnees, "source": "pilotage/pieces_manquantes.yaml"}
    )
