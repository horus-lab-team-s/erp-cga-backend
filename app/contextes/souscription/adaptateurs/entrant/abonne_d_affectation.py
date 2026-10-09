"""L'abonné qui affecte un dossier dès son dépôt, tel que la Souscription le déclare au relais.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CET ABONNÉ EXISTE

Le parcours d'acquisition annonce au client « un responsable vous recontacte ».
Jusqu'ici, rien ne désignait ce responsable : le dossier restait `DÉPOSÉE` jusqu'à
ce qu'un collaborateur ouvre la console et clique sur « affecter ». L'événement
`DemandeDéposée` était cité dans le code et jamais publié.

Le dépôt le publie désormais, et cet abonné le consomme : il exécute **la même
affectation** que le bouton de la console, avec les mêmes candidats (l'annuaire
et la charge lue au moment même) et la même grille du référentiel. Il ne décide
de rien de plus.

⚠️ REJOUABLE, PARCE QUE LE RELAIS GARANTIT « AU MOINS UNE FOIS »

Le même événement peut arriver deux fois après une coupure, et un collaborateur a
pu affecter le dossier à la main entre le dépôt et la remise. L'abonné ne touche
donc qu'un dossier encore `DÉPOSÉE` : tout autre état veut dire que quelqu'un est
passé avant lui, et le réaffecter changerait de responsable en plein échange.

⚠️ AUCUN CANDIDAT CONVENABLE N'EST PAS UNE PANNE

L'abonné ne lève pas : le dossier reste `DÉPOSÉE`, et la veille le remonte au
terme du délai que le référentiel accorde à cet état, comme pour la route. Lever
ferait retenter l'événement puis le mettre en quarantaine, pour un fait métier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging

from app.contextes.souscription.api import EtatDossier, affecter_le_dossier
from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant

__all__ = ["abonne_d_affectation", "affecter_a_l_arrivee"]

_journal = logging.getLogger(__name__)


def affecter_a_l_arrivee(evenement: EvenementSortant) -> None:
    """Reçoit un `DemandeDéposée` et désigne un responsable, s'il y en a un."""
    # Importé à l'appel : les dépôts et le référentiel d'acquisition vivent avec
    # les routes, et ce module est chargé à la composition de l'application.
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        _candidatures_du_cabinet,
        _dossiers,
        _referentiel_d_acquisition,
    )

    depot = _dossiers()
    dossier = depot.lire(evenement.charge.get("dossier", ""))
    if dossier.etat is not EtatDossier.DEPOSEE:
        return

    resultat = affecter_le_dossier(
        dossier,
        _candidatures_du_cabinet(dossier.service, depot),
        _referentiel_d_acquisition()["affectation"],
        maintenant(),
    )
    if resultat.affecte:
        depot.enregistrer(resultat.dossier)
        return
    _journal.info(
        "Dossier %s non affecté à l'arrivée : %s. La veille le remontera.",
        dossier.reference,
        "; ".join(resultat.empechements) or "aucun candidat",
    )


def abonne_d_affectation():
    """La fabrique inscrite au relais. Rend un abonné neuf sur la session du moment."""
    return affecter_a_l_arrivee
