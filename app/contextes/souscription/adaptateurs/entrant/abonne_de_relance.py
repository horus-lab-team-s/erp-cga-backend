"""L'abonné qui poste les relances, tel que la Souscription le déclare au relais.

⚠️ **Adaptateur entrant**, comme le travail de balayage et pour la même raison : le
relais *appelle* ce code. Ce qui entre dans le contexte est un adaptateur entrant,
quel que soit le protocole — ici, un appel de fonction déclenché par un événement.

Il assemble ce que le cas d'usage réclame — le plan de contact, le service de
courriel, le carnet des rappels — et ne décide de rien lui-même. La décision, c'est-
à-dire quel canal employer et quoi en faire, vit dans `remise_des_relances`.
"""

from __future__ import annotations

from functools import lru_cache

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotRappelsMemoire,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotDossiersSql,
    DepotRappelsSql,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
    vider_les_dossiers_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.plan_de_contact import (
    charger_le_plan_de_contact,
)
from app.contextes.souscription.application.remise_des_relances import (
    Remise,
    remettre_une_relance,
)
from app.contextes.souscription.domaine.rappels import RappelAPasser
from app.infrastructure.config import configuration
from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant
from app.partage.locataire import courant

__all__ = [
    "DOSSIER_DU_PLAN",
    "abonne_de_relance",
    "poster_une_relance",
    "reinitialiser_les_rappels",
]

#: Le sous-dossier du référentiel où vit le plan de contact.
DOSSIER_DU_PLAN = "messagerie"


class _CarnetDeRappels:
    """Réalise `TachesHumaines` en écrivant au carnet.

    ⚠️ **L'identifiant dérive de l'événement, il n'est pas tiré au hasard.** Le
    relais garantit « au moins une fois » : le même `RelanceDue` peut être remis
    deux fois après une coupure. Un identifiant aléatoire produirait alors deux
    lignes au carnet, et deux collaborateurs appelleraient le même client.
    """

    def __init__(self, depot, identifiant_evenement: str) -> None:
        self._depot = depot
        self._identifiant = identifiant_evenement

    def inscrire(self, dossier: str, motif: str, *, a_l_instant) -> None:
        self._depot.enregistrer(
            RappelAPasser(
                identifiant=f"rap-{self._identifiant}",
                dossier=dossier,
                motif=motif,
                cree_le=a_l_instant,
            )
        )


def poster_une_relance(evenement: EvenementSortant) -> None:
    """Reçoit un `RelanceDue` et joint le client. Lève si la remise a échoué.

    ⚠️ **Elle lève, et le relais s'en sert.** Il compte l'échec, retient les
    événements suivants de la même clé — donc les paliers suivants de la même
    proforma — et met en quarantaine au delà du seuil. Avaler l'échec ici ferait
    compter la relance comme remise alors que le client n'a rien reçu.
    """
    dossiers, rappels = _depots()
    reference = evenement.charge.get("dossier", "")
    dossier = dossiers.lire(reference)

    remettre_une_relance(
        evenement,
        dossier,
        Remise(
            plan=charger_le_plan_de_contact(
                configuration().dossier_referentiel / DOSSIER_DU_PLAN
            ),
            courriels=_courriels(),
            taches=_CarnetDeRappels(rappels, evenement.identifiant),
            horloge=maintenant,
            # ⚠️ **Faux, et il le restera tant que le compte de messagerie n'aura
            # pas au moins un modèle approuvé.** C'est ce qui empêche le système de
            # dépendre d'une validation extérieure qu'il ne contrôle pas : le repli
            # descend vers le courriel puis vers l'appel, et le parcours fonctionne.
            messagerie_prete=False,
        ),
    )


def abonne_de_relance():
    """La fabrique inscrite au relais. Rend un abonné neuf sur la session du moment.

    Une fabrique et non l'abonné lui-même : les dépôts se construisent sur la
    session de l'atelier en cours, et un abonné mémorisé au démarrage porterait une
    session déjà fermée.
    """
    return poster_une_relance


def _depots():
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    if session is None:
        return _dossiers_memoire(), _rappels_memoire()
    locataire = courant()
    return DepotDossiersSql(session, locataire), DepotRappelsSql(session, locataire)


def _courriels():
    from app.contextes.transverse.api import service_de_notification

    return service_de_notification()


#: ⚠️ **L'unique magasin, importé et non redéclaré.** Une fabrique locale a été
#: déclarée ici, et l'abonné lisait alors un magasin que personne n'alimentait :
#: chaque relance levait `DossierIntrouvable` et finissait en quarantaine, sans
#: qu'aucune erreur ne se voie. Voir l'en-tête de `magasins_memoire`.
_dossiers_memoire = dossiers_memoire


@lru_cache
def _rappels_memoire() -> DepotRappelsMemoire:
    return DepotRappelsMemoire()


def reinitialiser_les_rappels() -> None:
    """Repart de carnets mémoire vierges. Destiné aux tests."""
    vider_les_dossiers_memoire()
    _rappels_memoire.cache_clear()
