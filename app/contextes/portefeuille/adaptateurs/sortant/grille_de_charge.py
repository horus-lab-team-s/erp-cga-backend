"""Charge la grille d'évaluation de charge depuis le référentiel sur disque.

Un adaptateur, et rien d'autre : il lit des fichiers et rend des objets du domaine.

La grille n'est pas du code, et c'est le point. Critères, paliers et charges journalières
changent quand le centre les révise, et un ajustement de grille ne doit pas demander une
livraison. C'est le principe n° 1 appliqué à des valeurs qui ne sont pas légales mais qui
varient pour les mêmes raisons.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from app.contextes.portefeuille.domaine.charge import RegleDeCharge, Tranche

__all__ = ["charger_la_grille", "charger_les_tranches"]

#: Le fichier des paliers, distinct des critères qui vivent un par fichier.
FICHIER_TRANCHES = "tranches.yaml"


def charger_la_grille(dossier: Path) -> list[RegleDeCharge]:
    """Les critères du dossier, triés par code pour que l'ordre soit reproductible.

    Un ordre stable n'est pas cosmétique : deux évaluations du même dossier doivent
    produire le même détail, sinon les comparer d'un mois sur l'autre devient pénible.
    """
    criteres: list[RegleDeCharge] = []
    for fichier in sorted(dossier.glob("*.yaml")):
        if fichier.name == FICHIER_TRANCHES:
            continue
        donnees = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        if isinstance(donnees, dict) and "predicat" in donnees:
            criteres.append(RegleDeCharge.model_validate(donnees))
    return criteres


def charger_les_tranches(dossier: Path) -> tuple[Tranche, ...]:
    """Les paliers de charge, dans l'ordre croissant du fichier.

    L'ordre du fichier fait foi : c'est lui qui décide quelle tranche est essayée en
    premier, et le trier ici masquerait un fichier mal rédigé au lieu de le signaler.
    Le contrôle de continuité relève du domaine, qui sait ce qu'un trou signifie.
    """
    donnees = yaml.safe_load((dossier / FICHIER_TRANCHES).read_text(encoding="utf-8"))
    return tuple(
        Tranche(
            code=t["code"],
            libelle=t["libelle"],
            minimum=t["minimum"],
            maximum=t["maximum"],
            jours_par_mois=Decimal(str(t["jours_par_mois"])),
        )
        for t in donnees["tranches"]
    )
