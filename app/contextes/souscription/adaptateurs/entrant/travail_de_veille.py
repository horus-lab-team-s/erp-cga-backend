"""La veille des dossiers, telle que la Souscription la déclare à l'ordonnanceur.

Même motif que `travail_de_relance`, et pour la même raison : **le service
déclare, l'ordonnanceur consulte.** Un ordonnanceur qui importerait chaque service
dont il fait tourner un travail deviendrait le point par lequel tout se charge.

⚠️ **Adaptateur entrant.** L'ordonnanceur *appelle* ce code, comme une requête HTTP
appelle une route. Ce qui entre dans le contexte est un adaptateur entrant, quel
que soit le protocole : ici, un appel de fonction déclenché par une horloge.
"""

from __future__ import annotations

from datetime import timedelta

from app.contextes.souscription.adaptateurs.sortant.delais_de_veille import (
    charger_les_delais_de_veille,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
)
from app.contextes.souscription.application.veille_des_dossiers import (
    veiller_les_dossiers,
)
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant
from app.partage.locataire import courant

__all__ = [
    "DOSSIER_DE_LA_VEILLE",
    "NOM_TRAVAIL",
    "travail_de_veille",
    "veiller",
]

NOM_TRAVAIL = "veille-des-dossiers"

#: Le sous-dossier du référentiel où vivent les délais.
DOSSIER_DE_LA_VEILLE = "acquisition"


def travail_de_veille() -> Travail:
    """Le travail tel qu'il est déclaré à l'ordonnanceur.

    La cadence ne commande pas *à partir de quand* un dossier dort : cela vient du
    référentiel. Elle commande la finesse avec laquelle l'échéance est rattrapée,
    et donc le retard maximal d'une alerte sur le fait qu'elle décrit.
    """
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=configuration().cadence_veille_minutes),
        objet=(
            "balaie les dossiers commerciaux immobiles au-delà du délai de leur "
            "état et dépose une alerte interne par dossier, une seule fois"
        ),
    )


def veiller(boite) -> str:
    """Un passage de veille, dans la transaction du tour.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE DÉPÔT DE L'ALERTE ET LE MARQUAGE DU DOSSIER PARTAGENT CETTE
    TRANSACTION**, celle que l'unité de travail tient. Marquer sans déposer
    perdrait l'alerte définitivement, le marqueur empêchant le passage suivant de
    recommencer.

    ⚠️ **LES DÉLAIS SONT LUS À CHAQUE PASSAGE**, et non mémoïsés au démarrage. Le
    centre peut décider un lundi matin que deux jours sont trop longs ; des délais
    chargés une fois obligeraient à redéployer pour un réglage, ce qui est
    exactement ce qu'on veut éviter en les mettant au référentiel.

    Le coût est la lecture d'un petit fichier quatre fois par heure.
    ─────────────────────────────────────────────────────────────────────────────
    """
    delais = charger_les_delais_de_veille(
        configuration().dossier_referentiel / DOSSIER_DE_LA_VEILLE
    )
    rapport = veiller_les_dossiers(
        delais,
        dossiers=_depot(),
        boite=boite,
        a_l_instant=maintenant(),
        identifiant=lambda suffixe: f"veille-{suffixe}",
    )
    return rapport.resume


def _depot():
    """Les dossiers, durables si la base est là.

    ⚠️ En mémoire, le marqueur de signalement est perdu au redémarrage et la veille
    resignale tout ce qui dormait déjà. C'est sans conséquence sur un poste de
    développement, et c'est une raison de plus pour que la persistance mémoire ne
    soit pas un mode d'exploitation.
    """
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    if session is None:
        return dossiers_memoire()
    return DepotDossiersSql(session, courant())


#: ⚠️ **Aucune fabrique ici.** Une veille qui lirait son propre magasin ne verrait
#: jamais un dossier déposé par le formulaire public, et ne remonterait donc jamais
#: rien, sans qu'aucune erreur ne se produise. C'est précisément le défaut qui a
#: été trouvé chez l'abonné de relance ; voir l'en-tête de `magasins_memoire`.
