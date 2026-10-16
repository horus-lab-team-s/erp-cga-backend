"""Le règlement d'honoraires constaté : l'adhérent reçoit son avis (15 octobre 2026).

Un abonné à part de l'écriture comptable (`comptabilite/.../abonne_honoraires.py`) : un
courriel en panne ne doit pas empêcher l'écriture, ni l'inverse.

⚠️ UNE FOIS : le relais garantit « au moins une fois ». L'envoi est inscrit au journal
sous l'identifiant du règlement ; un second passage le trouve et n'envoie rien.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import jour_a_douala

__all__ = ["abonne_avis_honoraires", "prevenir_du_reglement"]

ACTION_AVIS = "souscription.avis_honoraires_envoye"


def prevenir_du_reglement(evenement: EvenementSortant, *, unite: Any = None) -> bool:
    """Rend `True` si l'avis est parti à ce passage."""
    from app.contextes.transverse.api import prevenir_les_adherents
    from app.contextes.transverse.api import unite_de_travail as unite_de_la_plateforme
    from app.infrastructure.config import configuration
    from app.partage.formats import ESPACE_FINE, MOIS, montant
    from app.partage.locataire import etabli

    c = evenement.charge
    locataire = configuration().locataire_par_defaut
    with etabli(locataire), (unite or unite_de_la_plateforme)(locataire) as boutique:
        if boutique.journal.lister(objet_type="honoraires_avis", objet_id=c["identifiant"]):
            return False
        periode = date.fromisoformat(c["periode"])
        base = configuration().adresse_publique_site.rstrip("/")
        prevenir_les_adherents(
            boutique,
            c["niu"],
            "adherent.honoraires_regles",
            {
                "periode": f"{MOIS[periode.month - 1]} {periode.year}",
                "montant": montant(Decimal(c["montant"])).replace(ESPACE_FINE, " "),
                "reference": c["reference"],
                "denomination": _denomination(c["niu"]),
                "lien": f"{base}/fr/mon-espace/honoraires",
            },
            jour=jour_a_douala(datetime.fromisoformat(c["constate_le"])),
        )
        boutique.journal.ajouter(
            horodatage=datetime.fromisoformat(c["constate_le"]),
            acteur="systeme",
            action=ACTION_AVIS,
            objet_type="honoraires_avis",
            objet_id=c["identifiant"],
            apres={"dossier": c["niu"], "periode": c["periode"]},
        )
        return True


def _denomination(niu: str) -> str:
    """La dénomination au portefeuille, sinon le NIU.

    ⚠️ 15 octobre 2026 : l'abonné empruntait la garde de la route, qui lève 404 ; le relais
    rejouait l'événement jusqu'à la quarantaine et l'avis ne partait jamais. Un nom absent
    ne doit pas empêcher de prévenir l'adhérent.
    """
    from app.contextes.portefeuille.api import depot_entreprises

    try:
        return depot_entreprises().lire(niu).denomination
    except LookupError:
        return niu


def abonne_avis_honoraires(unite: Any = None):
    return lambda evenement: prevenir_du_reglement(evenement, unite=unite)
