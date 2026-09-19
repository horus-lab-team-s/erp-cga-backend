"""Charge les échéances telles que l'adhérent les lit (pas 113).

`Docs/referentiel/obligations/explications_adherent.yaml`. Sans fichier, les valeurs du domaine :
aucun texte, le libellé du catalogue seul. Un fichier mal formé lève : une obligation expliquée deux
fois, ou des textes « validés » sans nom, ne doivent pas arriver sur le téléphone de l'adhérent.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.obligations.domaine.echeances_adherent import ReglagesDesEcheancesDeLAdherent

__all__ = ["charger_les_echeances_de_l_adherent"]


def charger_les_echeances_de_l_adherent(referentiel: Path) -> ReglagesDesEcheancesDeLAdherent:
    chemin = referentiel / "obligations" / "explications_adherent.yaml"
    if not chemin.is_file():
        return ReglagesDesEcheancesDeLAdherent()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDesEcheancesDeLAdherent.model_validate(
        {**donnees, "source": "obligations/explications_adherent.yaml"}
    )
