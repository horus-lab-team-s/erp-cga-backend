"""Engager une souscription : transformer un devis en demande de débit.

─────────────────────────────────────────────────────────────────────────────────
L'ORDRE DES ÉCRITURES N'EST PAS INDIFFÉRENT

La souscription et le paiement sont enregistrés **avant** l'appel au prestataire.
C'est contre-intuitif — on préférerait n'écrire qu'en cas de succès — et c'est la
seule façon de ne pas perdre d'argent.

Si l'on appelait d'abord et n'écrivait qu'ensuite, une panne entre les deux
laisserait une opération vivante chez l'opérateur, dont nous n'aurions aucune
trace. L'abonné serait débité, la notification arriverait, le rapprochement ne
trouverait rien, et la somme resterait non affectée. C'est exactement le scénario
qui conduit le client à repayer.

Écrire d'abord produit le défaut inverse, qui est bénin : un paiement en attente
qui ne correspond à aucune opération réelle. La réconciliation l'interroge, ne le
trouve pas, et il se périme au bout de vingt-quatre heures. Personne n'a rien
perdu.

**Entre un enregistrement de trop et un encaissement perdu, on choisit
l'enregistrement de trop.**

LE DEVIS EST FERMÉ AU MOMENT DE L'ENGAGEMENT

Il passe à `ENGAGE`, et un devis engagé ne s'engage plus. Sans cette fermeture,
deux clics sur le bouton de paiement produiraient deux souscriptions et deux
débits pour la même prestation.

⚠️ Cette protection vaut dans un processus. En base, elle réclamera un verrou sur
la ligne du devis — c'est écrit dans le port `DepotDevis`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.contextes.souscription.domaine.devis import Devis, EtatDevis
from app.contextes.souscription.domaine.offre import NatureService, service_par_code
from app.contextes.souscription.domaine.paiements import Paiement
from app.contextes.souscription.domaine.ports import (
    DepotDevis,
    DepotPaiements,
    DepotServices,
    DepotSouscriptions,
    FournisseurPaiement,
)
from app.contextes.souscription.domaine.souscriptions import Souscription
from app.contextes.transverse.api import JournalAudit

__all__ = ["EngagementRefuse", "engager", "nouvelle_cle_idempotence"]


class EngagementRefuse(ValueError):
    """Le devis ne peut pas être transformé en souscription."""


def nouvelle_cle_idempotence() -> str:
    """Un UUID, transmis au prestataire comme identifiant de produit.

    Tiré ici et non par le prestataire : c'est **notre** clé, et c'est ce qui
    permet de retrouver l'opération même quand il ne nous rend pas la sienne.
    """
    return str(uuid.uuid4())


def engager(
    reference_devis: str,
    *,
    reference_souscription: str,
    identifiant_paiement: str,
    cle_idempotence: str,
    niu: str | None = None,
    devis: DepotDevis,
    souscriptions: DepotSouscriptions,
    paiements: DepotPaiements,
    services: DepotServices,
    fournisseur: FournisseurPaiement,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> tuple[Souscription, Paiement]:
    """Crée la souscription, le paiement, et demande le débit.

    Rend les deux : l'écran affiche l'un et surveille l'autre.
    """
    propose = devis.lire(reference_devis)
    aujourd_hui = a_l_instant.date()

    if not propose.payable(aujourd_hui):
        raise EngagementRefuse(_pourquoi_pas_payable(propose, aujourd_hui))

    principale = _ligne_principale(propose)
    service = service_par_code(services.charger(aujourd_hui), principale.service)

    dossier = niu or propose.prospect.niu
    if service.ouvre_un_dossier and not dossier:
        raise EngagementRefuse(
            f"le service {service.code} ouvre un accès à un dossier : le NIU de "
            "l'entreprise est nécessaire. Une entreprise sans NIU doit d'abord être "
            "immatriculée."
        )

    souscription = Souscription(
        reference=reference_souscription,
        devis=propose.reference,
        prospect=propose.prospect,
        service=service.code,
        libelle=principale.libelle,
        nature=principale.nature,
        periodicite=principale.periodicite,
        formule=principale.formule,
        montant=propose.montant_a_regler,
        abonnement_mensuel=propose.abonnement_mensuel,
        engagee_le=a_l_instant,
        niu=dossier if service.ouvre_un_dossier else None,
    )
    paiement = Paiement(
        identifiant=identifiant_paiement,
        reference_reglee=souscription.reference,
        cle_idempotence=cle_idempotence,
        montant=souscription.montant,
        telephone=propose.prospect.telephone,
        initie_le=a_l_instant,
    )

    # Voir l'en-tête : on écrit avant d'appeler.
    souscriptions.enregistrer(souscription)
    paiements.enregistrer(paiement)
    devis.enregistrer(propose.engager(souscription.reference))
    journal.ajouter(
        horodatage=a_l_instant,
        acteur="systeme",
        action="souscription.engagee",
        objet_type="souscription",
        objet_id=souscription.reference,
        apres={
            "devis": propose.reference,
            "service": service.code,
            "montant": str(souscription.montant),
            "niu": souscription.niu,
        },
    )

    initiation = fournisseur.initier(
        montant=paiement.montant,
        telephone=paiement.telephone,
        cle_idempotence=paiement.cle_idempotence,
        libelle=f"{principale.libelle} — {souscription.reference}",
    )

    if not initiation.accepte:
        motif = initiation.message or "le prestataire a refusé l'initiation"
        paiement = paiement.rejeter(a_l_instant, motif)
        paiements.enregistrer(paiement)
        souscription = souscription.abandonner(a_l_instant, motif)
        souscriptions.enregistrer(souscription)
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.refuse_a_l_initiation",
            objet_type="paiement",
            objet_id=paiement.identifiant,
            apres={"motif": motif},
        )
        return souscription, paiement

    if initiation.reference_externe:
        paiement = paiement.avec_reference(initiation.reference_externe)
        paiements.enregistrer(paiement)

    return souscription, paiement


def _ligne_principale(devis: Devis):
    """La ligne qui donne son identité à la souscription.

    Un devis peut comporter plusieurs prestations — adhésion et domiciliation, par
    exemple. La souscription porte le nom de la **première ligne chiffrée qui n'est
    pas un abonnement**, à défaut la première chiffrée : c'est celle qui déclenche
    l'encaissement immédiat, et donc celle que le client reconnaîtra sur son
    relevé.
    """
    chiffrees = [ligne for ligne in devis.lignes if ligne.chiffree]
    if not chiffrees:
        raise EngagementRefuse(
            f"devis {devis.reference} : aucune ligne chiffrée, il n'y a rien à encaisser."
        )
    immediates = [ligne for ligne in chiffrees if ligne.nature != NatureService.ABONNEMENT]
    return immediates[0] if immediates else chiffrees[0]


def _pourquoi_pas_payable(devis: Devis, aujourd_hui) -> str:
    """Un message qui dit ce qui manque, pas seulement que quelque chose manque.

    Le prospect est ici légitime : il a demandé un devis et veut le régler. Un
    refus opaque le conduit à recommencer au hasard, ou à appeler le cabinet.
    """
    if devis.etat == EtatDevis.ENGAGE:
        return (
            f"le devis {devis.reference} a déjà donné lieu à la souscription "
            f"{devis.souscription}. Le régler de nouveau produirait un second débit "
            "pour la même prestation."
        )
    if devis.etat != EtatDevis.EMIS:
        return f"le devis {devis.reference} est à l'état {devis.etat} : il ne se règle pas."
    if devis.caduc(aujourd_hui):
        return (
            f"le devis {devis.reference} a expiré le {devis.valide_jusqu_au}. En "
            "demander un nouveau : le barème a pu changer, et l'honorer au prix "
            "d'alors n'engagerait personne."
        )
    if not devis.complet:
        return (
            f"le devis {devis.reference} comporte des prestations non chiffrées, qui "
            "supposent un examen du dossier. Le cabinet vous confirme le devis définitif "
            "sans frais."
        )
    return (
        f"le devis {devis.reference} ne comporte rien à encaisser immédiatement — "
        "seulement un abonnement, dont la première échéance se règle à la mise en place."
    )
