"""Surface publique du contexte N · Tenants.

Ce que les autres peuvent employer, et rien de plus. Le reste — la machine à états, le
répertoire, la normalisation des slugs — appartient au contexte et peut changer sans
prévenir.

Ce qui est exposé se résume à deux besoins :

* **résoudre** un nom d'hôte en tenant, et savoir ce que la requête mérite. C'est le
  chemin critique de chaque requête, pris par la passerelle ;
* **lire** l'état d'un tenant, pour un contexte qui a besoin de savoir s'il est suspendu ;
* **garnir** un répertoire depuis la base, ce que fait la composition au démarrage. Le
  dépôt est exposé pour cela, et pour cela seulement : il n'est pas destiné au chemin
  critique, où la résolution doit rester une lecture de dictionnaire.

Ouvrir, suspendre ou résilier ne passe pas par ici : ces gestes appartiennent au plan de
contrôle et s'exercent par ses propres routes, sous habilitation.
"""

from __future__ import annotations

from app.contextes.tenants.adaptateurs.sortant.depot_tenants_sql import DepotTenantsSql
from app.contextes.tenants.adaptateurs.sortant.noms_reserves import (
    charger_les_noms_reserves,
)
from app.contextes.tenants.adaptateurs.sortant.provisionneur_local import (
    ProvisionneurLocal,
    RegistreDesTenants,
    SlugDejaPris,
)
from app.contextes.tenants.adaptateurs.sortant.registre_durable import RegistreDurable
from app.contextes.tenants.adaptateurs.sortant.repertoire_memoire import (
    RegistreEnMemoire,
    RepertoireEnMemoire,
)
from app.contextes.tenants.application.ouverture import (
    NOM_SAGA,
    Provisionneur,
    saga_d_ouverture,
)
from app.contextes.tenants.application.ouverture_sur_paiement import (
    ResultatOuverture,
    abonner_l_ouverture,
    ouvrir_sur_paiement,
)
from app.contextes.tenants.domaine.ports import RepertoireDesTenants
from app.contextes.tenants.domaine.resolution import (
    Verdict,
    slug_depuis_hote,
    verdict_pour,
)
from app.contextes.tenants.domaine.slug import MotifRejet, SlugInvalide
from app.contextes.tenants.domaine.slug import valider as _valider_la_forme
from app.contextes.tenants.domaine.substitution import (
    ouverture_reellement_complete,
    substituees,
)
from app.contextes.tenants.domaine.tenant import NatureTenant, StatutTenant, Tenant
from app.infrastructure.config import configuration


class NomsReservesIndisponibles(RuntimeError):
    """La liste des noms réservés n'a pas pu être lue.

    ⚠️ **Ce n'est pas un slug invalide**, et les confondre ferait répondre « ce nom est
    réservé » à un client dont le nom ne l'est peut-être pas. C'est un défaut
    d'installation, et il se répare côté plateforme.
    """


def noms_reserves() -> frozenset[str]:
    """Les sous-domaines que la plateforme se garde, lus au référentiel.

    ⚠️ **Relu à chaque appel, sans mémoïsation.** Un slug se valide à la souscription,
    quelques fois par jour ; garder la liste en mémoire ferait qu'un nom ajouté au
    référentiel n'interdirait rien jusqu'au redémarrage suivant, ce qui est exactement le
    contraire de ce que « la faire grandir ne doit pas demander une livraison » promet.
    """
    try:
        return charger_les_noms_reserves(configuration().dossier_referentiel / "tenants")
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        raise NomsReservesIndisponibles(
            "la liste des noms réservés ne se lit pas "
            f"({type(panne).__name__}) : aucun sous-domaine ne peut être attribué tant "
            "qu'elle manque"
        ) from panne


def valider_le_slug(slug: str) -> None:
    """Refuse un slug inattribuable : sa forme **et** les noms que la plateforme se garde.

    ─────────────────────────────────────────────────────────────────────────────────
    ⚠️ **POURQUOI CETTE FONCTION EXISTE PLUTÔT QUE LE RÉEXPORT DU DOMAINE**

    L'`api` réexportait `domaine.slug.valider`, dont le second paramètre `reserves` vaut
    `()` par défaut. Les deux appelants l'ont appelée avec un seul argument, et pendant
    tout ce temps **le fichier des noms réservés n'était lu nulle part** : `api`, `www`
    ou `admin` étaient attribuables à un client, ce qui aurait envoyé les requêtes de la
    plateforme chez lui.

    Un défaut de ce genre ne se répare pas en corrigeant les deux appels : il se répare
    en supprimant la façon de se tromper. Le domaine garde son paramètre, parce qu'il
    doit rester vérifiable sans fichier ; **la seule porte ouverte aux autres contextes**,
    elle, va chercher la liste et ne laisse pas le choix.

    ⚠️ **Sans liste, on refuse.** Un slug attribué est inréattribuable — le rendre
    enverrait les anciens liens chez quelqu'un d'autre. Entre refuser une souscription,
    qui se rejoue, et donner `api` à un client, qui ne se reprend pas, le choix n'est pas
    symétrique.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    _valider_la_forme(slug, noms_reserves())


__all__ = [
    "DepotTenantsSql",
    "NomsReservesIndisponibles",
    "noms_reserves",
    "NOM_SAGA",
    "NatureTenant",
    "Provisionneur",
    "ProvisionneurLocal",
    "RegistreDesTenants",
    "RegistreDurable",
    "RegistreEnMemoire",
    "RepertoireDesTenants",
    "RepertoireEnMemoire",
    "ResultatOuverture",
    "MotifRejet",
    "SlugDejaPris",
    "SlugInvalide",
    "valider_le_slug",
    "StatutTenant",
    "Tenant",
    "Verdict",
    "abonner_l_ouverture",
    "ouverture_reellement_complete",
    "ouvrir_sur_paiement",
    "saga_d_ouverture",
    "slug_depuis_hote",
    "substituees",
    "verdict_pour",
]
