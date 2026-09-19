"""Le filet, jeté tout seul.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

`reconciliation.py` se décrit comme *« le filet : repêcher les encaissements dont
la notification ne nous est jamais parvenue »*, et nomme le scénario qu'il évite :

    C'est le scénario qui coûte le plus cher : l'argent est parti, le service
    n'est pas ouvert, le client attend. Au bout d'un moment il repaie — et c'est
    là que le double-encaissement apparaît, non pas par un défaut de notre code,
    mais par **notre silence**.

⚠️ **Ce filet n'était jamais jeté.** `reconcilier` n'avait qu'un appelant : une
route qu'un exploitant devait penser à cliquer, sous `LIRE_PILOTAGE`. Le module
écrit contre notre silence était lui-même silencieux.

La conséquence a grandi au pas 30. Tant que le parcours d'acquisition faisait
confirmer l'encaissement à la main, une notification perdue se rattrapait par le
geste humain qui suivait. Depuis qu'il s'appuie sur la notification, **une
notification perdue est un client qui a payé et dont l'espace ne s'ouvre pas.**

⚠️ **LA PÉREMPTION VIT DEDANS.** Rien d'autre ne fait expirer un paiement : sans
ce travail, une opération abandonnée reste « en attente » indéfiniment, et
encombre le rapprochement de candidats morts.

⚠️ **Adaptateur entrant**, comme les trois autres travaux : l'ordonnanceur appelle
ce code, et ce qui entre dans le contexte est un adaptateur entrant, quel que soit
le protocole — ici, un appel de fonction déclenché par une horloge.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta

from app.contextes.souscription.application.reconciliation import reconcilier
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant

__all__ = ["NOM_TRAVAIL", "reconcilier_les_paiements", "travail_de_reconciliation"]

NOM_TRAVAIL = "reconciliation-des-paiements"


def travail_de_reconciliation() -> Travail:
    """Le travail tel qu'il est déclaré à l'ordonnanceur.

    ⚠️ **Cinq minutes par défaut**, et le choix se justifie par ce que le passage
    coûte et par ce qu'il évite. Il coûte un appel sortant par paiement à
    interroger ; il évite qu'un client ayant payé attende son espace.

    La cadence ne commande pas *combien* d'appels sont faits : cela vient de
    l'espacement porté par chaque paiement, qui double à chaque tentative et
    plafonne à une heure. Elle commande la finesse avec laquelle un règlement
    perdu est rattrapé — au pire cinq minutes après qu'il devient rattrapable.
    """
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=configuration().cadence_reconciliation_minutes),
        objet=(
            "interroge le prestataire sur les encaissements restés en attente, "
            "fait expirer ceux que l'abonné a abandonnés, et encaisse ceux dont "
            "la notification s'est perdue"
        ),
    )


def reconcilier_les_paiements(_boite) -> str:
    """Un passage de réconciliation, dans la transaction du tour.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA BOÎTE D'ENVOI N'EST PAS EMPLOYÉE ICI**, et l'argument est accepté pour
    que ce travail se déclare comme les autres. Ce n'est pas ce module qui dépose :
    c'est la suite du paiement, appelée par `appliquer_evenement`, exactement comme
    pour une notification entrante. Déposer ici en plus produirait deux événements
    pour un règlement.

    ⚠️ **CE PASSAGE APPELLE LE RÉSEAU**, contrairement aux trois autres travaux. Un
    prestataire lent retarde donc le tour entier, et c'est assumé : l'ordonnanceur
    compte les échecs, recule, et abandonne au bout de vingt tentatives. Un travail
    qui traiterait le réseau à part demanderait un second mécanisme d'exécution
    pour un seul cas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    boutique = comptoir()
    rapport = reconcilier(
        fournisseur=boutique.fournisseur,
        paiements=boutique.paiements,
        suites=boutique.suites,
        journal=boutique.journal,
        a_l_instant=maintenant(),
    )
    return rapport.resume
