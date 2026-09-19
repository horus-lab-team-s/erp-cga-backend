"""Charge les réglages et les profils de relevé du rapprochement bancaire (pas 101).

Un adaptateur, et rien d'autre. Ce qui se règle est écrit dans
`Docs/referentiel/rapprochement/` :

* `reglages.yaml` : les fenêtres de dates. Sans fichier, des valeurs sobres s'appliquent :
  un réglage de tolérance absent n'empêche pas de travailler ;
* `releves/*.yaml` : un profil par format d'export bancaire. Sans profil, l'import de
  fichier est impossible, mais **la saisie manuelle du relevé reste ouverte** (banques
  locales sans export) : le rapprochement ne dépend d'aucun fichier pour exister.

Un fichier présent mais mal formé lève : un profil mal transcrit lirait les montants de
travers, et l'écart serait cherché dans la comptabilité.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.comptabilite.domaine.rapprochement import (
    ProfilDeReleve,
    ReglagesDuRapprochement,
)

__all__ = ["charger_les_profils_de_releve", "charger_les_reglages_du_rapprochement"]

DOSSIER = "rapprochement"


def charger_les_reglages_du_rapprochement(referentiel: Path) -> ReglagesDuRapprochement:
    chemin = referentiel / DOSSIER / "reglages.yaml"
    if not chemin.is_file():
        return ReglagesDuRapprochement()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglagesDuRapprochement.model_validate({**donnees, "source": f"{DOSSIER}/reglages.yaml"})


def charger_les_profils_de_releve(referentiel: Path) -> dict[str, ProfilDeReleve]:
    """Les profils, indexés par **code** : le code du fichier fait foi sur son nom."""
    dossier = referentiel / DOSSIER / "releves"
    profils: dict[str, ProfilDeReleve] = {}
    if not dossier.is_dir():
        return profils
    for chemin in sorted(dossier.glob("*.yaml")):
        profil = ProfilDeReleve.model_validate(
            yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
        )
        if profil.code in profils:
            raise ValueError(
                f"profil de relevé « {profil.code} » déclaré deux fois ({chemin.name})."
            )
        profils[profil.code] = profil
    return profils
