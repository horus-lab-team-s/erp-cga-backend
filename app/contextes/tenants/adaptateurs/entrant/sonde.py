"""La sonde du contexte N : la plateforme garde-t-elle encore ses propres noms ?

─────────────────────────────────────────────────────────────────────────────────
CE QU'ELLE SURVEILLE, ET CE QU'ELLE LAISSE AU TRANSVERSE

Le **répertoire** des tenants, celui que la passerelle consulte pour résoudre un nom
d'hôte, est vérifié par la sonde du Transverse, et c'est sa place : le répertoire
appartient à la passerelle. Voir `sonde_du_transverse`, qui l'explique.

Ce qui appartient au contexte N, c'est la **liste des noms que la plateforme se garde**.
Sans elle, `valider_le_slug` refuse toute attribution (un slug attribué est
inréattribuable : mieux vaut refuser une souscription, qui se rejoue, que donner `api`
à un client, ce qui ne se reprend pas). Autrement dit : liste absente, **plus aucun
tenant ne s'ouvre**, et rien d'autre ne le dirait avant qu'un client ne paie.

⚠️ **Cette sonde ne rend pas le service joignable.** Le contexte N n'expose aucune route
et reste `CAS_D_USAGE` : une sonde n'est pas une porte d'entrée pour un utilisateur.
`app/registre/etat.py` le sait, et ne la compte pas comme un adaptateur entrant.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.tenants.api import NomsReservesIndisponibles, noms_reserves
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_des_tenants"]


def sonde_des_tenants() -> Verdict | None:
    """Les noms réservés se lisent-ils, et y en a-t-il ?

    ⚠️ **Panne franche, et non suspicion.** Aucune explication innocente : le fichier est
    au dépôt. Son absence signifie que le référentiel monté n'est pas celui du projet, et
    la souscription est arrêtée tant qu'elle dure.
    """
    dossier = configuration().dossier_referentiel / "tenants"
    try:
        reserves = noms_reserves()
    except NomsReservesIndisponibles as indisponible:
        return Verdict.panne(
            f"{indisponible} : plus aucun tenant ne s'ouvrira. Vérifier {dossier}"
        )
    if not reserves:
        return Verdict.panne(
            f"aucun nom réservé chargé depuis {dossier} : un client pourrait se voir "
            "attribuer « api » ou « www », et les requêtes de la plateforme iraient chez lui"
        )
    return None
