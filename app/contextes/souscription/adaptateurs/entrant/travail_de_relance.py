"""Le balayage de relance, tel que la Souscription le déclare à l'ordonnanceur.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EST ICI ET NON CHEZ L'ORDONNANCEUR

La première tentative a fait importer la Souscription par le Transverse, où vit
l'ordonnanceur. Le test d'architecture l'a refusée : le graphe n'autorise au
Transverse que le Référentiel et les Tenants.

Il avait raison, et pas pour une raison de forme. Un ordonnanceur qui importe chaque
service dont il fait tourner un travail devient un point de couplage central : il
faut le modifier pour ajouter un travail, il traîne au démarrage tout ce que ces
services traînent, et le jour où l'un part vivre ailleurs il faut le découdre.

Le sens est donc inversé : **le service déclare, l'ordonnanceur consulte.** C'est
exactement ce que fait déjà le registre pour les sondes.

⚠️ **Adaptateur entrant, et non sortant.** L'ordonnanceur *appelle* ce code, comme
une requête HTTP appelle une route. Ce qui entre dans le contexte est un adaptateur
entrant, quel que soit le protocole — ici, un appel de fonction déclenché par une
horloge.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotSuivisDeRelanceMemoire,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotProformasSql,
    DepotSuivisDeRelanceSql,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    proformas_memoire,
    vider_les_proformas_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.plan_de_relance import (
    charger_le_plan_de_relance,
)
from app.contextes.souscription.application.balayage_de_relance import (
    balayer_les_relances,
)
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant
from app.partage.locataire import courant

__all__ = [
    "NOM_TRAVAIL",
    "balayer",
    "reinitialiser_le_balayage",
    "travail_de_relance",
]

NOM_TRAVAIL = "relance"

#: Le sous-dossier du référentiel où vit le plan de relance.
DOSSIER_DU_PLAN = "relance"


def travail_de_relance() -> Travail:
    """Le travail tel qu'il est déclaré à l'ordonnanceur.

    ⚠️ **Une heure par défaut**, et le choix se justifie par ce que le balayage
    produit : des messages à de vrais clients, selon un plan dont les paliers se
    comptent en **jours**. Balayer toutes les minutes ne rendrait aucune relance plus
    juste, et parcourrait des centaines de proformas soixante fois par heure pour ne
    rien trouver.

    La cadence ne commande pas *quand* un client est relancé — cela vient du plan, au
    référentiel — mais seulement la finesse avec laquelle l'échéance est rattrapée.
    """
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=configuration().cadence_relance_minutes),
        objet=(
            "balaie les proformas transmises sans réponse et dépose les relances "
            "dues, un palier à la fois ; remonte les acceptées impayées à un humain"
        ),
    )


def balayer(boite) -> str:
    """Un passage de balayage, dans la transaction du tour.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE DÉPÔT DE L'ÉVÉNEMENT ET L'INSCRIPTION DU SUIVI PARTAGENT CETTE
    TRANSACTION**, celle que l'unité de travail tient. C'est ce qui empêche qu'un
    client soit relancé deux fois au même palier : soit les deux écritures, soit ni
    l'une ni l'autre.

    Valider entre les deux rouvrirait exactement le trou qu'on ferme.

    ⚠️ **LE PLAN EST LU À CHAQUE PASSAGE**, et non mémoïsé au démarrage. Le centre
    peut changer ses paliers pendant que le service tourne ; un plan chargé une fois
    obligerait à redéployer pour passer de trois jours à cinq, ce qui est exactement
    le genre de réglage qu'on veut pouvoir toucher.

    Le coût est la lecture d'un petit fichier une fois par heure.
    ─────────────────────────────────────────────────────────────────────────────
    """
    plan = charger_le_plan_de_relance(
        configuration().dossier_referentiel / DOSSIER_DU_PLAN
    )
    proformas, suivis = _depots()
    rapport = balayer_les_relances(
        proformas.a_relancer(),
        plan,
        suivis=suivis,
        boite=boite,
        a_l_instant=maintenant(),
        identifiant=lambda suffixe: f"rel-{suffixe}",
    )
    return rapport.resume


def _depots():
    """Les proformas et les suivis, durables si la base est là.

    ⚠️ En mémoire, le suivi est perdu au redémarrage et le balayage suivant repart au
    premier palier : le client reçoit une seconde fois le message qu'il a déjà reçu.
    C'est la raison d'être de la table `suivi_de_relance`, et c'est pourquoi la
    persistance mémoire n'est pas un mode d'exploitation.
    """
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    if session is None:
        return _proformas_memoire(), _suivis_memoire()
    locataire = courant()
    return (
        DepotProformasSql(session, locataire),
        DepotSuivisDeRelanceSql(session, locataire),
    )


#: ⚠️ **Le magasin des proformas est importé, non redéclaré.**
#:
#: Il a été déclaré ici, et la route en déclarait un second. Deux caches sont deux
#: magasins : la route écrivait dans l'un, le balayage lisait l'autre, et il ne
#: trouvait **jamais rien à relancer**, quel que soit le nombre de proformas
#: émises, sans qu'aucune erreur ne se produise.
#:
#: L'ancien commentaire renvoyait ici à `test_travail_de_relance.py`, « qui garde
#: cette unicité en comptant les fabriques ». Ce fichier n'existait pas. Le garde
#: existe maintenant, et c'est `test_magasins_memoire.py` : il lit le code plutôt
#: qu'une liste, et c'est lui qui a trouvé ce défaut-ci.
_proformas_memoire = proformas_memoire


@lru_cache
def _suivis_memoire() -> DepotSuivisDeRelanceMemoire:
    return DepotSuivisDeRelanceMemoire()


def reinitialiser_le_balayage() -> None:
    """Repart de magasins mémoire vierges. Destiné aux tests."""
    vider_les_proformas_memoire()
    _suivis_memoire.cache_clear()
