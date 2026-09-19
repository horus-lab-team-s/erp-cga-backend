"""Charge le vocabulaire des motifs de classement depuis le référentiel.

Un adaptateur, et rien d'autre.

⚠️ **Le moteur ne connaît aucun motif.** Il sait qu'un motif doit appartenir à la
liste et qu'un motif peut exiger une précision ; il ne sait pas lesquels existent.
C'est ce qui permet au centre d'en ajouter un après trois mois d'usage réel sans
qu'on redéploie quoi que ce soit.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

__all__ = ["FICHIER_MOTIFS", "MotifDeClassement", "charger_les_motifs_de_classement"]

FICHIER_MOTIFS = "motifs-de-classement.yaml"


class MotifDeClassement(BaseModel):
    """Une entrée du vocabulaire."""

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    #: Le motif exige-t-il un texte en plus ? Vrai pour « autre » et « doublon »,
    #: qui ne disent rien tout seuls.
    precision_requise: bool = False


def charger_les_motifs_de_classement(dossier: Path) -> tuple[MotifDeClassement, ...]:
    """Les motifs tels que le centre les a arrêtés, dans l'ordre du fichier.

    L'ordre est conservé parce qu'il est celui d'une liste déroulante, et qu'une
    liste rangée par fréquence d'usage se remplit plus vite qu'une liste
    alphabétique. Le fichier place d'ailleurs « autre » en dernier à dessein.

    Aucun repli sur une liste par défaut : un fichier absent est une installation
    incomplète, et accepter n'importe quel motif ferait perdre en silence la
    seule chose que ce fichier existe pour produire.
    """
    donnees = yaml.safe_load((dossier / FICHIER_MOTIFS).read_text(encoding="utf-8"))
    return tuple(MotifDeClassement(**entree) for entree in donnees["motifs"])
