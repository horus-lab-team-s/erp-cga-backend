"""Charge les réglages du rapport mensuel depuis le référentiel (pas 106).

`Docs/referentiel/pilotage/rapport_mensuel.yaml` : le numéro d'agrément, l'engagement de
sincérité, les sections incluses. Sans fichier, toutes les sections et un agrément « à
renseigner ». Un fichier mal formé lève : une section mal écrite disparaîtrait du rapport de
comité sans que personne le voie.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.rapport_mensuel import ReglagesDuRapport

__all__ = ["charger_les_reglages_du_rapport"]


def charger_les_reglages_du_rapport(referentiel: Path) -> ReglagesDuRapport:
    chemin = referentiel / "pilotage" / "rapport_mensuel.yaml"
    if not chemin.is_file():
        return ReglagesDuRapport()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDuRapport.model_validate({**donnees, "source": "pilotage/rapport_mensuel.yaml"})
