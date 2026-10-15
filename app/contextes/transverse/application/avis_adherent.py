"""Prévenir les adhérents d'un dossier par courriel (15 octobre 2026).

Les mêmes destinataires que les relances (`pilotage/.../routes_relance.py`) : les
comptes ADHERENT habilités sur le dossier ce jour-là, et actifs. Un compte suspendu
ne reçoit rien.

⚠️ NE LÈVE JAMAIS : le service de notification ne lève pas, et un courriel qui ne part
pas ne doit pas annuler le verdict, l'encaissement ou le règlement qui l'a déclenché.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.contextes.transverse.domaine.habilitations import habilitations_actives
from app.contextes.transverse.domaine.identites import EtatCompte
from app.contextes.transverse.domaine.roles import Role

__all__ = ["prevenir_les_adherents"]


def prevenir_les_adherents(
    boutique: Any, niu: str, code: str, contexte: dict[str, Any], *, jour: date
) -> int:
    """Envoie le gabarit `code` à chaque adhérent actif du dossier ; rend le nombre envoyé."""
    envoyes = 0
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        if h.role is not Role.ADHERENT:
            continue
        compte = boutique.comptes.lire(h.compte)
        if compte.etat is not EtatCompte.ACTIF:
            continue
        if boutique.notifications.envoyer(
            code, destinataire=compte.courriel, contexte={"prenom": compte.appellation, **contexte}
        ):
            envoyes += 1
    return envoyes
