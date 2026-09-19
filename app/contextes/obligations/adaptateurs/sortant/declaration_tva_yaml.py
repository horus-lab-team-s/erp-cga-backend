"""Charge le formulaire et les réglages du dépôt de TVA depuis le référentiel (pas 109).

`obligations/formulaire_tva.yaml` et `obligations/depot_tva.yaml`. Sans fichier, les valeurs du
domaine. Un fichier présent mais mal formé lève : une ligne mal rangée changerait un chiffre
déclaré, un niveau mal écrit laisserait déposer un mois que personne n'a relu.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.obligations.application.depot import ReglagesDuDepotTVA
from app.contextes.obligations.application.formulaire_tva import (
    FormulaireTVA,
    formulaire_par_defaut,
)

__all__ = ["charger_le_formulaire_tva", "charger_les_reglages_du_depot_tva"]


def _lire(chemin: Path) -> dict | None:
    if not chemin.is_file():
        return None
    return yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}


def charger_le_formulaire_tva(referentiel: Path) -> FormulaireTVA:
    donnees = _lire(referentiel / "obligations" / "formulaire_tva.yaml")
    if donnees is None:
        return formulaire_par_defaut()
    return FormulaireTVA.model_validate({**donnees, "source": "obligations/formulaire_tva.yaml"})


def charger_les_reglages_du_depot_tva(referentiel: Path) -> ReglagesDuDepotTVA:
    donnees = _lire(referentiel / "obligations" / "depot_tva.yaml")
    if donnees is None:
        return ReglagesDuDepotTVA()
    return ReglagesDuDepotTVA.model_validate({**donnees, "source": "obligations/depot_tva.yaml"})
