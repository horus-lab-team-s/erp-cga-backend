"""Charge les sous-domaines que la plateforme se garde.

Un adaptateur, et rien d'autre. La liste vit au référentiel parce qu'elle grandit : un
sous-domaine technique de plus, une marque à protéger. La faire grandir ne doit pas
demander une livraison.
"""

from __future__ import annotations

from pathlib import Path

import yaml

__all__ = ["charger_les_noms_reserves"]

FICHIER = "noms-reserves.yaml"


def charger_les_noms_reserves(dossier: Path) -> frozenset[str]:
    """Les noms réservés, en minuscules.

    Rendus en minuscules ici plutôt qu'à la comparaison : la casse du fichier est une
    affaire de lisibilité pour celui qui l'édite, pas une donnée. Un « API » écrit en
    capitales dans le fichier doit interdire « api » sans que personne y pense.
    """
    donnees = yaml.safe_load((dossier / FICHIER).read_text(encoding="utf-8"))
    return frozenset(nom.strip().lower() for nom in donnees["noms"] if nom and nom.strip())
