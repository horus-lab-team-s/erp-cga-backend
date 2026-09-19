"""Charge le circuit de validation du référentiel (pas 95).

Sans fichier, `CircuitDeValidation()` : **personne** ne propose ni ne valide, le
référentiel reste en lecture seule. C'est le sens prudent : un référentiel légal qu'on
pourrait modifier sans que le cabinet ait dit qui en a le droit serait pire qu'un
référentiel figé. Un fichier présent mais mal formé lève.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.referentiel.domaine.surcouche import CircuitDeValidation

__all__ = ["charger_le_circuit"]


def charger_le_circuit(referentiel: Path) -> CircuitDeValidation:
    chemin = referentiel / "validation" / "circuit.yaml"
    if not chemin.is_file():
        return CircuitDeValidation()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return CircuitDeValidation.model_validate({**donnees, "source": "validation/circuit.yaml"})
