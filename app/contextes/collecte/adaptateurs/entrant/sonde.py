"""La sonde du contexte C : une pièce déposée peut-elle être conservée ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CETTE SONDE N'EXISTAIT PAS, ET POURQUOI ELLE EXISTE MAINTENANT

`main.py` affirmait que la Collecte « n'a aujourd'hui aucune configuration propre dont
l'absence l'empêcherait de travailler ». C'était vrai quand la phrase a été écrite. La
Collecte a pris depuis **deux ressources à elle** : le dossier où les fichiers déposés
sont écrits, et le fichier des réponses que l'adhérent peut faire au cabinet.

⚠️ **Le premier est le plus coûteux de tous les silences possibles.** Un adhérent qui
dépose une facture reçoit un accusé ; si le dossier n'est pas inscriptible, la pièce est
perdue et l'adhérent, lui, croit l'avoir remise. Il ne la redéposera pas, et personne ne
la réclamera avant le contrôle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from pathlib import Path

from app.contextes.collecte.adaptateurs.sortant.reponses_adherent_yaml import (
    charger_les_reponses_de_l_adherent,
)
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_collecte"]


def sonde_de_la_collecte() -> Verdict | None:
    """Le magasin des fichiers d'abord, les réponses de l'adhérent ensuite.

    Deux vérifications dans une seule sonde, et dans cet ordre : une sonde par service,
    et la plus grave des deux fait foi. Voir `sonde_du_transverse`, qui enchaîne de la
    même manière.
    """
    verdict = _le_magasin_des_fichiers()
    return verdict if verdict is not None else _les_reponses_de_l_adherent()


def _le_magasin_des_fichiers() -> Verdict | None:
    """Le dossier existe-t-il, et peut-on y écrire ?

    ⚠️ **Constaté, pas écrit.** `os.access` demande au système ; déposer un fichier
    témoin à chaque appel de sonde, toutes les dix secondes, salirait le magasin et
    userait le disque pour répondre à une question que le système sait déjà.

    ⚠️ **Panne franche.** Un dépôt qui échoue n'a aucune explication innocente : le
    volume n'est pas monté, ou les droits sont faux. Dans les deux cas, ce que
    l'adhérent croit avoir remis n'existe nulle part.
    """
    dossier = Path(configuration().dossier_fichiers)
    if not dossier.is_dir():
        return Verdict.panne(
            f"le magasin des pièces déposées est introuvable ({dossier}) : un dépôt "
            "serait accusé puis perdu, et l'adhérent croirait avoir remis sa pièce"
        )
    if not os.access(dossier, os.W_OK | os.X_OK):
        return Verdict.panne(
            f"le magasin des pièces déposées n'est pas inscriptible ({dossier}) : un "
            "dépôt serait accusé puis perdu, et l'adhérent croirait avoir remis sa pièce"
        )
    return None


def _les_reponses_de_l_adherent() -> Verdict | None:
    """Les réponses proposées à l'adhérent se chargent-elles ?

    ⚠️ **Suspect et non panne.** Le cabinet continue de recevoir, de classer et de
    contrôler les pièces : ce qui cesse, c'est la réponse de l'adhérent à une demande,
    et elle se rattrape par téléphone. Une alarme ici retirerait du trafic un service
    qui fait encore l'essentiel de son métier.
    """
    referentiel = configuration().dossier_referentiel
    try:
        charger_les_reponses_de_l_adherent(referentiel)
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.suspect(
            f"les réponses de l'adhérent ne se chargent pas ({type(panne).__name__}) : "
            f"il ne pourra plus répondre à une demande du cabinet. Vérifier "
            f"{referentiel / 'collecte' / 'reponses_adherent.yaml'}"
        )
    return None
