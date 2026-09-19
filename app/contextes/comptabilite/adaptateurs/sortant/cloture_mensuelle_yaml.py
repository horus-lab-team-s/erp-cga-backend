"""Charge les réglages de la clôture mensuelle depuis le référentiel (pas 107).

`Docs/referentiel/cloture_mensuelle/reglages.yaml`. Sans fichier, les valeurs par défaut du
domaine s'appliquent : tous les points actifs, tous bloquants sauf les pièces attendues de
l'adhérent, et le mois verrouillé tant qu'il est transmis ou validé. Un fichier présent mais
mal formé lève : un point qu'on croirait bloquant et qui ne le serait pas laisserait
transmettre un mois incomplet sans que personne ne le voie.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.comptabilite.domaine.cloture_mensuelle import ReglagesDeLaClotureMensuelle

__all__ = ["charger_les_reglages_de_la_cloture_mensuelle"]


def charger_les_reglages_de_la_cloture_mensuelle(referentiel: Path) -> ReglagesDeLaClotureMensuelle:
    chemin = referentiel / "cloture_mensuelle" / "reglages.yaml"
    if not chemin.is_file():
        return ReglagesDeLaClotureMensuelle()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLaClotureMensuelle.model_validate(
        {**donnees, "source": "cloture_mensuelle/reglages.yaml"}
    )
