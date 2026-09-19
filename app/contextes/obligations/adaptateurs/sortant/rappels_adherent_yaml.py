"""Charge les réglages des rappels d'échéance de l'adhérent (pas 115).

`Docs/referentiel/obligations/rappels_adherent.yaml`. Sans fichier, les valeurs du domaine (7 et
2 jours avant, actifs). Un fichier mal formé lève : des jalons par défaut hors des choix possibles,
ou des rappels actifs sans jalon, ne doivent pas s'afficher comme un réglage qui fonctionne.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.obligations.domaine.rappels_adherent import ReglagesDesRappels

__all__ = ["charger_les_rappels_de_l_adherent"]


def charger_les_rappels_de_l_adherent(referentiel: Path) -> ReglagesDesRappels:
    chemin = referentiel / "obligations" / "rappels_adherent.yaml"
    if not chemin.is_file():
        return ReglagesDesRappels()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDesRappels.model_validate(
        {**donnees, "source": "obligations/rappels_adherent.yaml"}
    )
