"""Charge les délais de veille depuis le référentiel sur disque.

Un adaptateur, et rien d'autre. Il convertit des heures en durées ; il ne
décide de rien, et surtout pas de ce qui est sous veille : c'est la présence
d'un état dans le fichier qui le décide.

⚠️ **Un état inconnu est une erreur, pas une ligne ignorée.** Une faute de
frappe sur `EN_CONVERSATON` produirait, si on l'ignorait, une veille qui tourne
sans jamais rien remonter et sans jamais rien dire. Le silence d'une
surveillance ne se distingue pas de la tranquillité, et c'est ce qui la rend
dangereuse.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml

from app.contextes.souscription.domaine.dossier_commercial import EtatDossier

__all__ = ["FICHIER_VEILLE", "EtatSousVeilleInconnu", "charger_les_delais_de_veille"]

FICHIER_VEILLE = "veille.yaml"


class EtatSousVeilleInconnu(ValueError):
    """Le référentiel nomme un état qui n'existe pas au domaine."""


def charger_les_delais_de_veille(dossier: Path) -> dict[EtatDossier, timedelta]:
    """Les délais tels que le centre les a arrêtés, état par état.

    Aucun repli sur des délais par défaut : un fichier absent est une
    installation incomplète, et veiller selon un calendrier inventé remonterait
    des alertes que personne n'a décidées, ou n'en remonterait aucune.
    """
    donnees = yaml.safe_load((dossier / FICHIER_VEILLE).read_text(encoding="utf-8"))
    delais: dict[EtatDossier, timedelta] = {}
    for entree in donnees["etats"]:
        nom = entree["etat"]
        try:
            etat = EtatDossier(nom)
        except ValueError as echec:
            connus = ", ".join(sorted(e.value for e in EtatDossier))
            raise EtatSousVeilleInconnu(
                f"{FICHIER_VEILLE} met sous veille l'état « {nom} », qui n'existe "
                f"pas. Les états du parcours sont : {connus}."
            ) from echec
        delais[etat] = timedelta(hours=entree["apres_heures"])
    return delais
