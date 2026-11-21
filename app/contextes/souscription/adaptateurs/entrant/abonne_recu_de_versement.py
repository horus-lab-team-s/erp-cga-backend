"""Une somme reçue du client : il reçoit son reçu par courriel, PDF joint (13 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CET ABONNÉ EXISTE

Constaté en recette en ligne le 13 novembre 2026 : la cliente réglait ses frais de
traitement, puis une tranche, puis le solde de sa proforma, et ne recevait rien. Le reçu
n'existait qu'au règlement complet, et seulement à télécharger dans « Ma création ». Une
cliente qui vient de payer 353 500 FCFA par mobile money doit avoir, dans sa boîte, la
preuve que le cabinet les a reçus.

Chaque versement du parcours (frais de traitement, tranche, pénalité, trop-perçu) est
publié par `recevoir_un_versement` sous `NOM_VERSEMENT_RECU`, quel que soit son chemin :
paiement mobile money notifié par l'opérateur, ou encaissement constaté au guichet.

⚠️ UNE FOIS : le relais garantit « au moins une fois ». L'envoi est inscrit au journal
sous l'identifiant du versement ; un second passage le trouve et n'envoie rien.

⚠️ UN ENVOI MANQUÉ SE REJOUE : si le poste ne part pas (Brevo lent, refus passager),
l'abonné lève, et le relais représente l'événement plus tard. Le journal n'est écrit
qu'après l'envoi, pour que le rejeu ne se taise pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import a_douala

__all__ = ["abonne_recu_de_versement", "envoyer_le_recu", "OBJET_RECU"]

ACTION_RECU = "souscription.recu_de_versement_envoye"
OBJET_RECU = "recu_de_versement"

_OBJETS = {
    "FRAIS_DE_TRAITEMENT": "frais de traitement de votre dossier",
    "TRANCHE": "règlement de la proforma {proforma}",
    "PENALITE_RENDEZ_VOUS": "pénalité pour un rendez-vous manqué",
    "PENALITE_DE_RETARD": "pénalité de retard",
    "TROP_PERCU": "somme reçue au-delà du montant dû, que le cabinet vous restituera",
}

_CANAUX = {
    "MOBILE_MONEY": "par mobile money",
    "GUICHET": "au guichet du cabinet",
}


class _Demandeur:
    """Le nom de la demande, tel que la charge le transporte.

    ⚠️ La salutation ne doit jamais être vide (garde de `test_courriel.py`) : la demande
    impose deux lettres au moins, et un nom absent ou trop court se replie sur une
    formule de politesse plutôt que d'écrire « Merci,  ».
    """

    def __init__(self, charge: dict) -> None:
        nom = (charge.get("nom") or "").strip()
        self.nom = nom if len(nom) >= 2 else "Madame, Monsieur"


class ReçuNonEnvoye(RuntimeError):
    """Le poste n'a pas pris le message : le relais rejouera l'événement."""


def _francs(valeur: str | Decimal) -> str:
    return f"{Decimal(valeur):,.0f}".replace(",", " ")


def _objet(c: dict) -> str:
    modele = _OBJETS.get(c["nature"], "versement au cabinet")
    return modele.format(proforma=c.get("proforma") or "")


def _situation(c: dict) -> str:
    """La phrase qui situe ce versement dans le dossier. Rien d'inventé : ce que la
    situation calculée au moment du versement dit, et seulement si elle le dit."""
    if c["nature"] == "TROP_PERCU":
        return "Cette somme ne vous est pas due au titre du dossier : le cabinet vous la rendra."
    if c["nature"] in ("PENALITE_RENDEZ_VOUS", "PENALITE_DE_RETARD"):
        return "Cette pénalité est réglée ; elle ne s'impute pas sur le montant de la proforma."
    if c.get("solde"):
        return "Votre proforma est entièrement réglée. Merci de votre confiance."
    if c.get("total") and c.get("reste"):
        return (
            f"Vous avez versé {_francs(c['paye'])} FCFA sur {_francs(c['total'])} FCFA ; "
            f"il reste {_francs(c['reste'])} FCFA à régler."
        )
    if c["nature"] == "FRAIS_DE_TRAITEMENT":
        return (
            "Ces frais seront déduits du montant de la proforma que le cabinet vous adressera "
            "après l'étude de votre demande."
        )
    return "Le cabinet vous tiendra informé de la suite de votre dossier."


def _numero(c: dict) -> str:
    return "R-" + c["identifiant"].removeprefix("ver-").upper()


def rendre_le_recu_du_versement(c: dict) -> bytes:
    from app.contextes.souscription.adaptateurs.sortant.document_recu_honoraires import (
        rendre_un_recu,
    )

    canal = _CANAUX.get(c.get("canal", ""), c.get("canal", "").lower() or "au cabinet")
    apres = [("Réglé", f"{canal}, référence {c.get('reference_externe') or c['identifiant']}")]
    if c["nature"] in ("TRANCHE", "FRAIS_DE_TRAITEMENT") and c.get("total"):
        apres.append(("Total de la proforma", f"{_francs(c['total'])} FCFA"))
        apres.append(("Versé à ce jour", f"{_francs(c['paye'])} FCFA"))
        if c.get("reste") not in (None, ""):
            apres.append(("Reste à régler", f"{_francs(c['reste'])} FCFA"))
    return rendre_un_recu(
        titre="REÇU DE VERSEMENT · ACQUITTÉ",
        numero=_numero(c),
        le=datetime.fromisoformat(c["constate_le"]),
        avant=[
            ("Client", c["nom"]),
            ("Dossier", c["dossier"]),
            ("Objet", _objet(c).capitalize()),
        ],
        montant=Decimal(c["montant"]),
        apres=apres,
    )


def envoyer_le_recu(
    evenement: EvenementSortant, *, unite: Any = None, notifier: Any = None
) -> bool:
    """Rend `True` si le reçu est parti à ce passage. Lève `ReçuNonEnvoye` si le poste
    refuse, pour que le relais rejoue."""
    from app.contextes.transverse.api import service_de_notification
    from app.contextes.transverse.api import unite_de_travail as unite_de_la_plateforme
    from app.infrastructure.config import configuration
    from app.partage.locataire import etabli

    c = evenement.charge
    courriel = (c.get("courriel") or "").strip()
    if not courriel:
        # L'adresse est facultative au formulaire public : le reçu reste téléchargeable
        # et la caisse en remet un au guichet. Rien à rejouer.
        return False
    locataire = configuration().locataire_par_defaut
    with etabli(locataire), (unite or unite_de_la_plateforme)(locataire) as boutique:
        if boutique.journal.lister(objet_type=OBJET_RECU, objet_id=c["identifiant"]):
            return False
        recu_le = a_douala(datetime.fromisoformat(c["constate_le"]))
        demande = _Demandeur(c)
        numero = _numero(c)
        parti = (notifier or service_de_notification()).envoyer(
            "versement.recu",
            destinataire=courriel,
            contexte={
                "prenom": demande.nom,
                "montant": _francs(c["montant"]),
                "objet_du_versement": _objet(c),
                "reference": numero,
                "recu_le": recu_le.strftime("%d/%m/%Y à %Hh%M"),
                "situation": _situation(c),
                "pieces_jointes": [
                    (f"Recu-{numero}.pdf", rendre_le_recu_du_versement(c), "application/pdf")
                ],
            },
        )
        if not parti:
            raise ReçuNonEnvoye(f"reçu {numero} non remis au poste ; le relais rejouera")
        boutique.journal.ajouter(
            horodatage=datetime.fromisoformat(c["constate_le"]),
            acteur="systeme",
            action=ACTION_RECU,
            objet_type=OBJET_RECU,
            objet_id=c["identifiant"],
            apres={"dossier": c["dossier"], "montant": c["montant"], "nature": c["nature"]},
        )
        return True


def abonne_recu_de_versement(unite: Any = None):
    return lambda evenement: envoyer_le_recu(evenement, unite=unite)
