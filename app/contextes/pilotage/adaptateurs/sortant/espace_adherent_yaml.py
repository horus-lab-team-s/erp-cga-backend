"""Charge les mots de l'accueil de l'adhérent (pas 112).

`Docs/referentiel/pilotage/espace_adherent.yaml`. Sans fichier, les valeurs du domaine. Un fichier
mal formé lève : un bandeau qui tairait la date limite, ou citerait une valeur inconnue, ne doit pas
arriver sur le téléphone de l'adhérent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.mois_de_l_adherent import ReglagesDeLEspaceAdherent

__all__ = ["charger_les_reglages_de_l_espace_adherent"]


def charger_les_reglages_de_l_espace_adherent(referentiel: Path) -> ReglagesDeLEspaceAdherent:
    chemin = referentiel / "pilotage" / "espace_adherent.yaml"
    if not chemin.is_file():
        return ReglagesDeLEspaceAdherent()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLEspaceAdherent.model_validate(
        {**donnees, "source": "pilotage/espace_adherent.yaml"}
    )
