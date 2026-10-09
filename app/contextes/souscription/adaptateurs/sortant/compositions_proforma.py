"""Le chargement des compositions de proforma, depuis le référentiel.

⚠️ **Un fichier du dépôt, relu à chaque appel de la sonde et mémoïsé ailleurs.**
Il est petit, et une composition corrigée doit valoir au document suivant.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.contextes.souscription.domaine.composition_proforma import (
    CompositionIntrouvable,
    CompositionProforma,
)

__all__ = ["RepertoireDesCompositions", "charger_les_compositions"]

FICHIER = "rubriques_proforma.yaml"


def charger_les_compositions(dossier: Path) -> list[CompositionProforma]:
    """Les compositions du fichier, ou une liste vide s'il n'y en a pas.

    ⚠️ **Absent se tait, présent et fautif lève.** Un déploiement peut n'avoir
    aucune composition : le document se rabat alors sur la mise en page générale,
    honoraires et débours. Un fichier **présent** qui ne se charge pas est autre
    chose : quelqu'un l'a posé, donc quelqu'un compte dessus.
    """
    chemin = dossier / FICHIER
    if not chemin.is_file():
        return []
    brut: dict[str, Any] = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return [CompositionProforma(**v) for v in brut.get("variantes", [])]


class RepertoireDesCompositions:
    """Ce que les appelants interrogent. Aucune date par défaut."""

    def __init__(self, compositions: list[CompositionProforma]) -> None:
        doublons = {
            c.code for c in compositions if sum(a.code == c.code for a in compositions) > 1
        }
        if doublons:
            raise ValueError(
                f"compositions en double : {sorted(doublons)}. Deux compositions "
                "d'un même code rendraient le document imprévisible."
            )
        self._toutes = list(compositions)

    @classmethod
    def depuis(cls, dossier: Path) -> RepertoireDesCompositions:
        return cls(charger_les_compositions(dossier))

    def __len__(self) -> int:
        return len(self._toutes)

    def toutes(self) -> list[CompositionProforma]:
        return list(self._toutes)

    def prendre(self, code: str, a_la_date: date) -> CompositionProforma:
        """La composition de ce code, applicable ce jour-là.

        ⚠️ Le message nomme les compositions disponibles. « composition
        introuvable » enverrait chercher ; celui-ci suffit à agir.
        """
        for composition in self._toutes:
            if composition.code == code and composition.couvre(a_la_date):
                return composition
        connues = sorted(c.code for c in self._toutes)
        raise CompositionIntrouvable(
            f"aucune composition « {code} » applicable au {a_la_date}. "
            f"Compositions au référentiel : {connues or 'aucune'}."
        )
