"""Charge les mots de « Mon entreprise » (pas 114).

`Docs/referentiel/portefeuille/espace_adherent.yaml`. Sans fichier, les valeurs du domaine : les
codes (« SARL », « CDI », « REEL ») et les natures de changement de la maquette. Un fichier mal formé
lève : un régime expliqué deux fois, ou des textes « validés » sans nom, ne s'affichent pas.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.portefeuille.domaine.espace_adherent import ReglagesDeLaFicheAdherent

__all__ = ["charger_la_fiche_adherent"]


def charger_la_fiche_adherent(referentiel: Path) -> ReglagesDeLaFicheAdherent:
    chemin = referentiel / "portefeuille" / "espace_adherent.yaml"
    if not chemin.is_file():
        return ReglagesDeLaFicheAdherent()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDeLaFicheAdherent.model_validate(
        {**donnees, "source": "portefeuille/espace_adherent.yaml"}
    )
