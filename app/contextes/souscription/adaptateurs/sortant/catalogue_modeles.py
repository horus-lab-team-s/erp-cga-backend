"""Charge le catalogue des modèles de message depuis le référentiel sur disque.

Un adaptateur, et rien d'autre.

Le catalogue est ce que **le centre décide** : le texte, la catégorie, l'usage et
l'état d'approbation constaté. La fenêtre de service et la grille tarifaire, elles,
sont imposées par la plateforme et vivent dans le domaine avec leur citation. Voir
l'en-tête de `domaine/conversation.py`, qui explique pourquoi les mélanger serait un
défaut et non une commodité.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.souscription.domaine.conversation import ModeleDeMessage

__all__ = ["CatalogueDeModeles", "ModeleInconnu", "charger_les_modeles"]


class ModeleInconnu(LookupError):
    """Le modèle demandé n'est pas au catalogue."""


def charger_les_modeles(dossier: Path) -> list[ModeleDeMessage]:
    """Les modèles du dossier, triés par nom pour que l'ordre soit reproductible.

    Les fichiers sans corps sont ignorés sans bruit : c'est ainsi que le README
    cohabite avec les modèles.
    """
    modeles: list[ModeleDeMessage] = []
    for fichier in sorted(dossier.glob("*.yaml")):
        donnees = yaml.safe_load(fichier.read_text(encoding="utf-8"))
        if not isinstance(donnees, dict) or "corps" not in donnees:
            continue
        modeles.append(ModeleDeMessage.model_validate(donnees))
    return modeles


class CatalogueDeModeles:
    """Les modèles, indexés par nom.

    ⚠️ `prendre` lève sur un nom inconnu, et c'est délibéré : un modèle absent
    est une faute de frappe ou un fichier oublié, jamais un cas ordinaire. Rendre
    `None` ferait qu'un envoi disparaîtrait silencieusement, et la relance qu'on
    croyait partie ne serait jamais partie.
    """

    def __init__(self, modeles: list[ModeleDeMessage]) -> None:
        self._par_nom = {modele.nom: modele for modele in modeles}

    @classmethod
    def depuis(cls, dossier: Path) -> CatalogueDeModeles:
        return cls(charger_les_modeles(dossier))

    def prendre(self, nom: str) -> ModeleDeMessage:
        modele = self._par_nom.get(nom)
        if modele is None:
            connus = ", ".join(sorted(self._par_nom)) or "aucun"
            raise ModeleInconnu(
                f"aucun modèle « {nom} » au catalogue. Modèles connus : {connus}."
            )
        return modele

    def tous(self) -> list[ModeleDeMessage]:
        return [self._par_nom[nom] for nom in sorted(self._par_nom)]

    def envoyables(self) -> list[ModeleDeMessage]:
        """Ceux qui passeraient aujourd'hui.

        Sert au contrôle de mise en service : un catalogue dont aucun modèle
        n'est approuvé rend le premier contact impossible, et cela doit se voir
        avant le premier client, pas pendant.
        """
        return [m for m in self.tous() if m.envoyable]

    def __len__(self) -> int:
        return len(self._par_nom)
