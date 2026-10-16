"""L'abonné qui accuse réception d'une demande, sur l'adresse de la cliente.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CET ABONNÉ EXISTE

Le formulaire du site affiche « un responsable vous recontacte », puis la page se
referme. Jusqu'au 28 septembre, c'était tout : entre son dépôt et le premier
appel, la cliente n'avait **aucune trace écrite** de son geste. Pas de référence,
pas de confirmation, rien à relire. Elle ne pouvait que se demander si le
formulaire avait marché — et redéposer, ce que l'anti-doublon rattrape, ou
renoncer, ce que rien ne rattrape.

Elle recevait deux messages dans tout le parcours : sa proposition commerciale,
puis son accès. Le premier arrive après l'échange avec l'expert, c'est-à-dire
des jours plus tard. Le silence du début était le plus long, et c'est celui où
elle doute le plus.

⚠️ DEUX ABONNÉS SUR `DemandeDéposée`, ET C'EST PRÉVU

L'affectation désigne un responsable ; celui-ci écrit à la cliente. Ce sont deux
réactions distinctes au même fait, et elles ne se commandent pas : l'accusé ne
nomme personne, précisément pour ne pas dépendre de l'issue de l'affectation. Un
dossier que personne n'a pu prendre reçoit quand même son accusé, et c'est la
veille qui remonte l'absence de responsable.

⚠️ REJOUABLE, PARCE QUE LE RELAIS GARANTIT « AU MOINS UNE FOIS »

`accuse_de_depot_le` porte la garde. Sans elle, une remise rejouée après une
coupure enverrait un second message identique au premier, pour un dépôt unique.

⚠️ IL NE LÈVE PAS SUR UN ENVOI IMPOSSIBLE

Sans adresse, sans consentement, il n'y a pas d'incident : il y a une cliente
qu'on rappellera au téléphone, ce qu'elle a demandé. Lever ferait retenter
l'événement puis le mettre en quarantaine, pour un fait métier — et emporterait
l'affectation avec lui si les deux partageaient une transaction.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant

__all__ = ["abonne_d_accuse_de_depot", "accuser_le_depot"]

_journal = logging.getLogger(__name__)


def accuser_le_depot(evenement: EvenementSortant, *, notifier: Any = None) -> None:
    """Reçoit un `DemandeDéposée` et confirme la réception par courriel.

    ⚠️ **Le notifieur s'injecte**, comme pour l'accès adhérent et pour la même
    raison : un cas de recette doit pouvoir lire ce qui est parti sans monter un
    serveur de messagerie. À défaut, il prend celui de la plateforme.
    """
    # Importé à l'appel : les dépôts vivent avec les routes, et ce module est
    # chargé à la composition de l'application.
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        depot_dossiers,
    )

    # ⚠️ Par `transverse.api`, et non par le module de dépendances. L'abonné
    # d'accès adhérent, lui, lit l'interne — il vit DANS transverse. Celui-ci
    # vit dans souscription : on n'entre chez l'autre que par sa surface, et
    # `tests/test_architecture.py` le vérifie.
    from app.contextes.transverse.api import service_de_notification

    depot = depot_dossiers()
    dossier = depot.lire(evenement.charge.get("dossier", ""))

    if dossier.accuse_de_depot_le is not None:
        # Événement rejoué. Le premier accusé est parti ; le second n'a rien à
        # dire de plus, et ferait douter du nombre de demandes déposées.
        return

    demande = dossier.demande
    courriel = (demande.courriel or "").strip()
    if not courriel:
        # ⚠️ PAS UNE PANNE. L'adresse est facultative au formulaire, et c'est
        # voulu : on ne bloque pas un dépôt sur une adresse électronique dans un
        # pays où le téléphone est le canal principal. Elle sera rappelée.
        _journal.info(
            "Dossier %s : aucun accusé de dépôt, la demande ne porte pas "
            "d'adresse. Le rappel au %s reste le canal convenu.",
            dossier.reference,
            demande.telephone,
        )
        return

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ ON NE REGARDE **PAS** `demande.consentement` ICI, ET C'EST RÉFLÉCHI.
    #
    # La tentation est forte : il y a un objet `Consentement` sur la demande, il
    # porte `vaut_maintenant`, et le brancher ferait sérieux. Ce serait faux.
    #
    # Ce consentement est celui de **WhatsApp**, et rien d'autre. Son champ
    # d'origine s'appelle `consentement_whatsapp`, sa version est
    # `consentement-whatsapp-v1`, et `canal_a_employer` s'en sert pour choisir
    # entre la messagerie et l'appel. Le domaine le dit d'ailleurs en toutes
    # lettres : « elle interdit WhatsApp, elle n'interdit pas de rappeler au
    # téléphone. »
    #
    # Il est **décoché par défaut**, ce qui est juste pour de la messagerie. S'y
    # adosser ici priverait d'accusé la grande majorité des clientes — celles
    # qui ont pourtant écrit leur adresse dans le formulaire.
    #
    # Ce qui autorise ce message, c'est qu'elle a donné cette adresse, dans ce
    # formulaire, à l'instant. L'accusé répond à son geste ; il ne sollicite
    # rien, ne propose rien, et le dire une fois est la contrepartie de l'avoir
    # reçue.
    #
    # ⚠️ Le jour où le formulaire portera une case propre au courriel, c'est ICI
    # qu'elle se lira — pas celle de WhatsApp par commodité.
    # ─────────────────────────────────────────────────────────────────────────
    poste = notifier or service_de_notification()
    poste.envoyer(
        "demande.accuse_de_depot",
        destinataire=courriel,
        contexte={
            "prenom": demande.nom,
            "reference": dossier.reference,
            "service": _libelle_du_service(demande.service_souhaite),
            "telephone": demande.telephone,
        },
    )

    # ⚠️ **APRÈS L'ENVOI, ET NON AVANT.** Marquer d'abord ferait qu'un poste
    # indisponible laisserait un dossier « accusé » sans que rien ne soit parti,
    # et le rejeu — le seul filet — se tairait. Dans l'autre ordre, un incident
    # entre les deux renvoie un accusé en double : c'est le moindre des maux, et
    # c'est le régime « au moins une fois » assumé partout ailleurs.
    depot.enregistrer(
        dossier.model_copy(update={"accuse_de_depot_le": maintenant()})
    )
    _journal.info(
        "Dossier %s : accusé de dépôt envoyé à %s.", dossier.reference, courriel
    )


def _libelle_du_service(code: str) -> str:
    """Le libellé du catalogue, ou le code tel quel.

    ⚠️ **Le code brut plutôt qu'un libellé fabriqué.** « ADHESION » dans un
    courriel est disgracieux ; « Adhésion » déduit par une mise en forme
    automatique serait faux le jour où le catalogue nommera autrement. Un code
    inconnu au catalogue est de toute façon un défaut à corriger au catalogue,
    et le voir dans le message le signale.
    """
    from app.contextes.souscription.api import CATALOGUE

    for service in CATALOGUE:
        if service.code == code:
            return service.libelle
    return code


def abonne_d_accuse_de_depot(notifier: Any = None):
    """La fabrique inscrite au relais. Rend un abonné neuf sur la session du moment."""

    def accuser(evenement: EvenementSortant) -> None:
        accuser_le_depot(evenement, notifier=notifier)

    return accuser
