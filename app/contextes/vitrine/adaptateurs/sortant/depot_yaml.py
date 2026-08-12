"""Adaptateur sortant : lecture du contenu de la vitrine depuis des fichiers YAML.

Réalise le port `DepotContenuVitrine` du domaine. C'est le seul endroit du contexte
L qui touche un système de fichiers.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI DU YAML, ET PAS UNE BASE, POUR COMMENCER

Parce que la personne qui corrige une faute dans un article, au cabinet, doit
pouvoir le faire sans écran d'administration à construire d'abord. Le YAML se lit,
se commente, se relit en revue, et se versionne : on sait qui a changé quoi et
quand, ce qu'aucune base ne donne gratuitement.

Le jour où le volume ou le nombre de rédacteurs le justifiera, un
`DepotContenuVitrineSql` réalisera le même port. Le remplacement se fera à la
composition, dans l'adaptateur entrant, sans toucher au cas d'usage.

POURQUOI UN FICHIER MALFORMÉ FAIT ÉCHOUER LE CHARGEMENT

Un article dont la rubrique est inconnue, une date incohérente, un bloc sans
`type` : le chargement s'arrête. Mieux vaut un démarrage refusé, bruyant et
immédiat, qu'un blog amputé de trois articles sans que personne ne s'en aperçoive
avant des semaines.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.contextes.vitrine.domaine.entites import Annonce, Article, Institution

__all__ = [
    "ContenuIllisible",
    "DepotContenuVitrineYaml",
    "charger_annonces",
    "charger_articles",
    "charger_institutions",
]

#: Noms des fichiers attendus dans le dossier de contenu. Ils sont fixes : un
#: dossier dont on peut deviner la structure se corrige sans documentation.
FICHIER_ARTICLES = "articles.yaml"
FICHIER_ANNONCES = "annonces.yaml"
FICHIER_INSTITUTIONS = "institutions.yaml"

TModele = TypeVar("TModele", bound=BaseModel)


class ContenuIllisible(ValueError):
    """Un fichier de contenu est absent, malformé, ou refusé par les entités.

    Le message porte le chemin du fichier et l'entrée fautive : la personne qui
    vient de corriger le YAML doit savoir quelle ligne reprendre, sans lire une
    trace d'exécution.
    """


def _lire_liste(chemin: Path, cle: str, modele: type[TModele]) -> list[TModele]:
    """Lit un fichier `<cle>: [ … ]` et valide chaque entrée.

    Le format est volontairement le même pour les trois fichiers : une clé unique à
    la racine, une liste dessous. Cela laisse la place d'ajouter un jour des
    métadonnées à côté de la liste — une date de dernière révision, par exemple —
    sans casser les fichiers existants.
    """
    if not chemin.exists():
        raise ContenuIllisible(
            f"{chemin} est introuvable. Le dossier de contenu de la vitrine doit "
            f"contenir {FICHIER_ARTICLES}, {FICHIER_ANNONCES} et {FICHIER_INSTITUTIONS}."
        )

    try:
        contenu: Any = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContenuIllisible(f"{chemin} : YAML illisible — {exc}") from exc

    if not isinstance(contenu, dict) or cle not in contenu:
        raise ContenuIllisible(f"{chemin} : clé « {cle} » attendue à la racine")

    brut = contenu[cle]
    if not isinstance(brut, list):
        raise ContenuIllisible(f"{chemin} : « {cle} » doit être une liste")

    valides: list[TModele] = []
    for rang, entree in enumerate(brut, start=1):
        try:
            valides.append(modele.model_validate(entree))
        except ValidationError as exc:
            raise ContenuIllisible(f"{chemin} : entrée n° {rang} refusée — {exc}") from exc
    return valides


def charger_articles(chemin: Path) -> list[Article]:
    return _lire_liste(chemin, "articles", Article)


def charger_annonces(chemin: Path) -> list[Annonce]:
    return _lire_liste(chemin, "annonces", Annonce)


def charger_institutions(chemin: Path) -> list[Institution]:
    return _lire_liste(chemin, "institutions", Institution)


class DepotContenuVitrineYaml:
    """Dépôt de contenu adossé à un dossier de fichiers YAML.

    Le dossier est passé en argument plutôt que lu dans la configuration : le
    dépôt reste utilisable dans un test, sur un dossier temporaire, sans variable
    d'environnement à poser.
    """

    def __init__(self, dossier: Path) -> None:
        self._dossier = dossier

    def charger_articles(self) -> list[Article]:
        return charger_articles(self._dossier / FICHIER_ARTICLES)

    def charger_annonces(self) -> list[Annonce]:
        return charger_annonces(self._dossier / FICHIER_ANNONCES)

    def charger_institutions(self) -> list[Institution]:
        return charger_institutions(self._dossier / FICHIER_INSTITUTIONS)
