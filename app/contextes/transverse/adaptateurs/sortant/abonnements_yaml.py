"""Charge les abonnements aux notifications depuis le référentiel (pas 94).

Un adaptateur, et rien d'autre : ce que le cabinet veut annoncer, et à qui, est écrit dans
`Docs/referentiel/notifications/abonnements.yaml`.

⚠️ Sans fichier, **aucune notification** : c'est le sens prudent ici. Une notification
inventée par défaut porterait un texte que personne n'a relu, à des destinataires que
personne n'a choisis. L'écran dit alors pourquoi la cloche reste muette.

Un fichier présent mais mal formé lève (clé inconnue, abonnement sans destinataire) : un
réglage mal transcrit ne doit pas passer pour un réglage en vigueur.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.transverse.domaine.notifications import PolitiqueDeNotification

__all__ = ["charger_les_abonnements"]

DOSSIER = "notifications"
FICHIER = "abonnements.yaml"


def charger_les_abonnements(referentiel: Path) -> PolitiqueDeNotification:
    chemin = referentiel / DOSSIER / FICHIER
    if not chemin.is_file():
        return PolitiqueDeNotification()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return PolitiqueDeNotification.model_validate({**donnees, "source": f"{DOSSIER}/{FICHIER}"})
