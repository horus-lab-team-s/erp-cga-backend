"""Charge la grille d'affectation depuis le référentiel sur disque.

Un adaptateur, et rien d'autre : il lit des fichiers et rend des objets du domaine.

La grille n'est pas du code, et c'est tout le point du pas. Critères, poids et
caractère rédhibitoire changent quand le centre révise son organisation, et une
révision ne doit pas demander une livraison.

⚠️ **LE SCHÉMA EST VÉRIFIÉ AU CHARGEMENT, PAS À L'ÉVALUATION**

Un prédicat qui lit `{"var": "competence"}` au lieu de `{"var": "competences"}`
ne lève pas : JSONLogic rend `None` pour un chemin absent, et le critère devient
silencieusement toujours faux. Un responsable parfaitement compétent serait alors
écarté de tous les dossiers, et rien dans le journal ne le dirait.

Le contrôle est donc fait ici, une fois, au démarrage, avec une suggestion quand
le chemin ressemble à un fait connu. Une faute de frappe dans un fichier édité par
un responsable de pôle doit échouer bruyamment au chargement, jamais mentir à
l'évaluation.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.souscription.domaine.affectation import (
    SCHEMA_AFFECTATION,
    RegleDAffectation,
)
from app.moteur.chemins import chemins_cites

__all__ = ["charger_la_grille_d_affectation"]


def charger_la_grille_d_affectation(dossier: Path) -> list[RegleDAffectation]:
    """Les critères du dossier, triés par code pour que l'ordre soit reproductible.

    Un ordre stable n'est pas cosmétique : le motif conservé pour audit énumère
    les critères déclenchés, et deux affectations identiques doivent produire le
    même texte. Sinon les comparer d'un trimestre sur l'autre devient pénible.

    Les fichiers qui ne portent pas de prédicat sont ignorés sans bruit : c'est
    ainsi que le README cohabite avec les critères dans le même dossier.
    """
    criteres: list[RegleDAffectation] = []
    for fichier in sorted(dossier.glob("*.yaml")):
        donnees = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        if not isinstance(donnees, dict) or "predicat" not in donnees:
            continue
        critere = RegleDAffectation.model_validate(donnees)
        SCHEMA_AFFECTATION.valider_predicat(
            chemins_cites(critere.predicat),
            origine=f"{fichier.name} ({critere.code})",
        )
        criteres.append(critere)
    return criteres
