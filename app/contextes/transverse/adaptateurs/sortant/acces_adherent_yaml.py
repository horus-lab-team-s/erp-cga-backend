"""Charge les réglages du renvoi de lien d'accès (pas 116). `Docs/referentiel/acces/adherents.yaml`.

Sans fichier, les valeurs du domaine (deux renvois par jour, quinze caractères de vérification).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.transverse.domaine.acces_adherent import ReglagesDuRenvoi

__all__ = ["charger_les_reglages_du_renvoi"]


def charger_les_reglages_du_renvoi(referentiel: Path) -> ReglagesDuRenvoi:
    chemin = referentiel / "acces" / "adherents.yaml"
    if not chemin.is_file():
        return ReglagesDuRenvoi()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDuRenvoi.model_validate({**donnees, "source": "acces/adherents.yaml"})
