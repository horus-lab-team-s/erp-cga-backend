"""La reprise des dossiers, telle que la Souscription la déclare à l'ordonnanceur.

Même motif que `travail_de_relance` et `travail_de_veille` : **le service déclare,
l'ordonnanceur consulte.** Un ordonnanceur qui importerait chaque service dont il
fait tourner un travail deviendrait le point par lequel tout se charge.

⚠️ **Adaptateur entrant.** L'ordonnanceur appelle ce code, comme une requête HTTP
appelle une route ; ce qui entre dans le contexte est un adaptateur entrant, quel
que soit le protocole.
"""

from __future__ import annotations

from datetime import timedelta

from app.contextes.souscription.adaptateurs.sortant.annuaire_des_candidats import (
    monter_les_candidatures,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.regle_de_reprise import (
    charger_la_regle_de_reprise,
)
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.souscription.application.reprise_des_dossiers import (
    reprendre_les_dossiers,
)
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant
from app.partage.locataire import courant

__all__ = [
    "DOSSIER_ACQUISITION",
    "NOM_TRAVAIL",
    "reprendre",
    "travail_de_reprise",
]

NOM_TRAVAIL = "reprise-des-dossiers"

#: Le sous-dossier du référentiel où vit la règle de reprise.
DOSSIER_ACQUISITION = "acquisition"

#: Celui où vit la grille d'affectation, partagée avec la route d'affectation.
DOSSIER_AFFECTATION = "affectation"


def travail_de_reprise() -> Travail:
    """Le travail tel qu'il est déclaré à l'ordonnanceur.

    Même cadence que la veille : les deux balaient les mêmes lignes, et leur
    donner des cadences différentes ferait deux requêtes là où une suffirait à
    rien, sans rendre aucun délai plus juste. Le délai vient du référentiel.
    """
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=configuration().cadence_veille_minutes),
        objet=(
            "reprend la main sur les dossiers affectés sans mouvement au-delà du "
            "délai, en écartant celui qui n'a pas rappelé ; ne fait rien quand "
            "aucun autre candidat ne convient"
        ),
    )


def reprendre(boite) -> str:
    """Un passage de reprise, dans la transaction du tour.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA RÈGLE EST LUE À CHAQUE PASSAGE**, `actif` compris. C'est ce qui permet
    au centre d'arrêter le geste sans redéploiement : une règle mémoïsée au
    démarrage obligerait à redémarrer le service pour couper une réaffectation
    automatique qu'on vient de juger mauvaise, c'est-à-dire au pire moment.

    ⚠️ **L'ÉCRITURE DU DOSSIER ET LE DÉPÔT DE L'ÉVÉNEMENT PARTAGENT CETTE
    TRANSACTION.** Réaffecter sans déposer laisserait le nouveau responsable
    ignorer qu'il a un dossier ; déposer sans réaffecter préviendrait quelqu'un
    d'un dossier qu'il n'a pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    racine = configuration().dossier_referentiel
    regle = charger_la_regle_de_reprise(racine / DOSSIER_ACQUISITION)
    if not regle.actif:
        # ⚠️ Un compte rendu explicite, et non un passage muet. « rien fait »
        # et « coupé au référentiel » se ressemblent dans un journal, et c'est
        # la première chose qu'un exploitant cherche quand plus rien ne bouge.
        return "reprise coupée au référentiel (actif: false)"

    depot = _depot()
    instant = maintenant()
    rapport = reprendre_les_dossiers(
        lambda service: _candidatures(service, depot),
        charger_la_grille_d_affectation(racine / DOSSIER_AFFECTATION),
        dossiers=depot,
        boite=boite,
        a_l_instant=instant,
        delai_depasse=lambda dossier: instant - dossier.depuis_le > regle.apres,
        identifiant=lambda suffixe: f"reprise-{suffixe}",
    )
    return rapport.resume


def _depot():
    """Les dossiers, durables si la base est là.

    ⚠️ **Aucune fabrique mémoire ici**, le magasin unique est importé. Une reprise
    qui lirait son propre magasin ne verrait jamais un dossier déposé par le
    formulaire public, et ne reprendrait donc jamais rien, sans qu'aucune erreur
    ne se produise. Voir l'en-tête de `magasins_memoire`.
    """
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    if session is None:
        return dossiers_memoire()
    return DepotDossiersSql(session, courant())


def _candidatures(service: str, dossiers):
    """Les collaborateurs en mesure de prendre ce dossier, et ce qu'ils portent.

    ⚠️ **La charge est relue à chaque dossier**, jamais montée une fois pour le
    passage. Un collaborateur qui vient de recevoir un dossier repris est plus
    chargé pour le suivant ; monter les candidatures une seule fois verserait
    trois dossiers d'affilée au même, précisément parce qu'il était le moins
    chargé au début du passage.

    C'est la même discipline que la route d'affectation, et elle a la même
    raison d'être.
    """
    from app.contextes.transverse.api import atelier

    boutique = atelier()
    return monter_les_candidatures(
        comptes=boutique.comptes,
        habilitations=boutique.habilitations,
        charge=dossiers.charge_par_responsable(),
        service=service,
        a_la_date=maintenant().date(),
    )
