"""Charge les réponses toutes faites de l'adhérent au cabinet (pas 112).

`Docs/referentiel/collecte/reponses_adherent.yaml`. Sans fichier, les valeurs du domaine (les mots
de la maquette, et « la semaine prochaine » à sept jours). Un fichier mal formé lève : une réponse
réglée deux fois, ou un « message » réglé comme une réponse toute faite, ne doivent pas s'afficher.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.collecte.domaine.espace_adherent import ReglagesDesReponses

__all__ = ["charger_les_reponses_de_l_adherent"]


def charger_les_reponses_de_l_adherent(referentiel: Path) -> ReglagesDesReponses:
    chemin = referentiel / "collecte" / "reponses_adherent.yaml"
    if not chemin.is_file():
        return ReglagesDesReponses()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDesReponses.model_validate(
        {**donnees, "source": "collecte/reponses_adherent.yaml"}
    )
