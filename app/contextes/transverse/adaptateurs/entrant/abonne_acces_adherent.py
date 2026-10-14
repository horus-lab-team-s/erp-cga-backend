"""L'abonné qui ouvre l'accès de la fondatrice, une fois sa société immatriculée.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CET ABONNÉ EXISTE, ET POURQUOI ICI ET PAS À L'ENCAISSEMENT

Le cabinet a tranché le 27 septembre : une cliente qui achète une prestation est
une **adhérente du cabinet**, et non un locataire de la plateforme. Son compte
doit vivre là où vit son dossier.

Or la portée d'une adhérente est le dossier de **son** entreprise — « une
souscription ne peut pas fabriquer un réviseur ». Ouvrir son accès demande donc
un NIU, et au moment où elle paie sa création, **son entreprise n'existe pas**.
Son NIU n'arrive qu'à la conversion, au bout du tunnel des formalités.

Ce n'est pas une contrainte technique subie : avant l'immatriculation, il n'y a
rien à lui montrer. La promesse du produit est qu'on achète une société, et
l'accès vient avec elle.

⚠️ PAR UN ÉVÉNEMENT, PARCE QUE LE GRAPHE L'EXIGE

`creation_entreprise` n'a pas le droit de toucher à l'identité, et c'est voulu :
une création d'entreprise n'a pas à savoir comment on ouvre un compte.
`EntrepriseCréée` porte ce qu'il faut savoir, et cet abonné fait le reste.

⚠️ REJOUABLE, PARCE QUE LE RELAIS GARANTIT « AU MOINS UNE FOIS »

Une adresse déjà titulaire d'un compte n'en reçoit pas un second. Sans cette
garde, un événement remis deux fois enverrait deux liens d'activation, et le
premier serait invalidé par le second — la cliente cliquerait sur le mauvais.

⚠️ CE QUI MANQUE N'EST PAS INVENTÉ

Sans adresse électronique, aucun compte : on ne fabrique pas d'identifiant pour
quelqu'un qu'on ne peut pas prévenir. L'abonné le dit au journal d'exploitation
et laisse l'accès à ouvrir à la main, depuis la fiche de l'entreprise.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.orchestration.boite_d_envoi import EvenementSortant
from app.partage.horloge import maintenant

__all__ = ["abonne_d_acces_adherent", "ouvrir_l_acces_de_la_fondatrice"]

_journal = logging.getLogger(__name__)


def ouvrir_l_acces_de_la_fondatrice(
    evenement: EvenementSortant,
    *,
    unite_de_travail: Any = None,
    notifier: Any = None,
) -> None:
    """Reçoit un `EntrepriseCréée` et ouvre l'accès adhérent de sa fondatrice.

    ⚠️ **L'unité de travail et le notifieur s'injectent**, et c'est le motif
    retenu pour l'autre port d'ouverture, pour la même raison : cet abonné
    tourne hors requête, et un cas de recette doit pouvoir lui donner des dépôts
    en mémoire sans monter une base. À défaut, il prend ceux de la plateforme.
    """
    charge = evenement.charge
    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ DEUX CONDITIONS, ET IL LES FAUT TOUTES LES DEUX.
    #
    #   · son entreprise existe — sinon il n'y a rien à lui montrer ;
    #   · elle a pris le SUIVI — sinon on lui ouvrirait un espace de suivi
    #     qu'elle n'a pas acheté.
    #
    # Le cabinet peut immatriculer une société sans en tenir la comptabilité :
    # la cliente repart alors avec son RCCM et son NIU, et c'est complet. Lui
    # donner un accès lui ferait attendre un service qui ne viendra pas, et le
    # cabinet le découvrirait à la première échéance non traitée.
    #
    # ⚠️ Pendant la création, elle n'a besoin d'aucun COMPTE — ce qui ne veut plus
    # dire qu'elle n'a rien. Depuis le pas 140, l'ouverture de son dossier de
    # formalité lui envoie un **lien signé** : elle y suit l'avancement et y
    # dépose ses pièces, sans mot de passe.
    #
    # Ce commentaire disait « ses pièces arrivent par WhatsApp ». C'était le plan,
    # et il ne tenait pas : la fenêtre de service de la messagerie se referme
    # vingt-quatre heures après le dernier mot du client, et une formalité dure
    # des semaines. Voir `lien_de_suivi`.
    #
    # La raison d'origine, elle, tient toujours et commande encore : un formulaire
    # à identifiants pour envoyer une photo de sa carte d'identité ferait
    # abandonner la moitié des dossiers. Le compte arrive avec la société.
    #
    # ⚠️ Défaut à VRAI : les événements déposés avant cette distinction n'en
    # portent pas la clé, et la conversion y valait adhésion.
    # ─────────────────────────────────────────────────────────────────────────
    if not charge.get("adherente", True):
        _journal.info(
            "Entreprise %s créée sans suivi : aucun accès ouvert, et c'est ce "
            "qui a été vendu.",
            charge.get("niu") or evenement.cle,
        )
        return

    courriel = str(charge.get("courriel_fondateur") or "").strip()
    niu = str(charge.get("niu") or "").strip()
    if not courriel or not niu:
        _journal.warning(
            "Entreprise %s créée, accès NON ouvert : il manque %s. À ouvrir à la "
            "main depuis la fiche de l'entreprise.",
            niu or evenement.cle,
            "l'adresse de la fondatrice" if not courriel else "le NIU",
        )
        return

    # ⚠️ **UNE UNITÉ DE TRAVAIL À SOI, et non l'atelier de la requête.**
    #
    # Le relais publie hors requête : `atelier()` lève alors « aucune unité de
    # travail ouverte ». L'abonné ouvre donc la sienne, sur le locataire du
    # cabinet — celui où naît le compte, et où vit déjà son dossier.
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        service_de_notification,
    )
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        unite_de_travail as unite_de_la_plateforme,
    )
    from app.infrastructure.config import configuration
    from app.partage.locataire import etabli

    unite = unite_de_travail or unite_de_la_plateforme
    poste = notifier or service_de_notification()
    locataire = configuration().locataire_par_defaut
    instant = maintenant()
    with etabli(locataire), unite(locataire) as boutique:
        existant = boutique.comptes.par_courriel(courriel)
        if existant is None:
            _ouvrir(boutique, poste, charge, courriel, niu, locataire, instant)
            return
        # ⚠️ SON COMPTE EXISTE DEPUIS LE PAIEMENT (14 octobre 2026).
        #
        # Le compte client s'ouvre désormais au règlement de la création. Cette
        # branche s'arrêtait en silence sur toute adresse connue : la société de
        # la cliente entrait au portefeuille, et son espace adhérent ne
        # s'ouvrait jamais. On étend SON compte, s'il suit CETTE création ;
        # sinon c'est, comme avant, un rejeu ou un compte créé à la main.
        _etendre(boutique, poste, existant, charge, evenement.cle, niu, instant)


def _etendre(boutique, poste, compte, charge, reference: str, niu: str, instant) -> None:
    """Ajoute l'espace adhérent au compte qu'elle a depuis le paiement."""
    from app.contextes.transverse.api import ExtensionRefusee, etendre_a_l_adhesion
    from app.contextes.transverse.application.activation import emettre_jeton
    from app.contextes.transverse.application.administration import SYSTEME
    from app.contextes.transverse.domaine.identites import EtatCompte
    from app.contextes.transverse.domaine.jetons import TypeJeton
    from app.infrastructure.config import configuration

    deja = any(
        h.role.value == "ADHERENT" and h.portee is not None and niu in h.portee
        for h in boutique.habilitations.pour_compte(compte.identifiant)
    )
    try:
        etendre_a_l_adhesion(
            compte=compte,
            identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
            niu=niu,
            reference_creation=reference,
            depuis=instant.date(),
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=instant,
        )
    except ExtensionRefusee:
        # Déjà ouvert à la main, ou adresse d'un autre compte : on n'y touche pas.
        return
    if deja:
        # Événement rejoué : l'habilitation existait, le message est parti.
        return

    denomination = str(charge.get("denomination") or "")
    if compte.etat is EtatCompte.EN_ATTENTE_ACTIVATION:
        # ⚠️ Elle n'a jamais choisi son mot de passe : le lien du paiement a
        # pu expirer depuis des semaines. Un lien neuf, et le message qui va
        # avec.
        jeton, secret = emettre_jeton(
            compte,
            TypeJeton.ACTIVATION,
            identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=instant,
            emis_par=SYSTEME,
        )
        _envoyer_le_lien(poste, compte, secret, jeton, denomination, niu)
    else:
        base = configuration().adresse_publique_site.rstrip("/")
        poste.envoyer(
            "compte.adhesion_ajoutee",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.appellation,
                "denomination": denomination,
                "niu": niu,
                "lien": f"{base}/connexion",
            },
        )
    _journal.info(
        "Entreprise %s immatriculée : espace adhérent ajouté au compte %s.",
        niu,
        compte.identifiant,
    )


def _ouvrir(boutique, poste, charge, courriel: str, niu: str, locataire: str, instant) -> None:
    """Le compte, sa portée, son jeton, et le courriel qui porte le lien."""
    from app.contextes.transverse.api import ouvrir_acces_adherent

    compte, jeton, secret = ouvrir_acces_adherent(
        identifiant_compte=f"A-{uuid.uuid4().hex[:12]}",
        identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
        identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
        courriel=courriel,
        nom=str(charge.get("nom_fondateur") or courriel.split("@")[0]),
        # ⚠️ Pas de prénom fabriqué : le gabarit se rabat sur le nom, et
        # « Bonjour NKOA » vaut mieux qu'un prénom inventé.
        prenom=str(charge.get("prenom_fondateur") or ""),
        niu=niu,
        locataire=locataire,
        depuis=instant.date(),
        comptes=boutique.comptes,
        habilitations=boutique.habilitations,
        jetons=boutique.jetons,
        journal=boutique.journal,
        a_l_instant=instant,
        telephone=str(charge.get("telephone_fondateur") or "") or None,
    )

    _envoyer_le_lien(
        poste, compte, secret, jeton, str(charge.get("denomination") or ""), niu
    )
    _journal.info(
        "Entreprise %s immatriculée : accès adhérent ouvert pour %s.",
        niu,
        compte.identifiant,
    )


def _envoyer_le_lien(
    poste, compte, secret: str, jeton, denomination: str, niu: str
) -> None:
    """Le lien d'activation, sur l'adresse du **site**.

    ⚠️ `adresse_publique` est celle de l'API — celle que le prestataire de
    paiement rappelle. Le lien d'activation mène à une PAGE, et la configuration
    distingue les deux depuis toujours. Un lien posé sur l'API ne mène nulle
    part ; c'est le défaut constaté le 27 septembre sur le port d'ouverture de
    tenant, et il ne se reproduit pas ici.

    ⚠️ **Sur l'hôte du cabinet, et c'est juste** : le compte est créé dans le
    locataire du cabinet, et c'est le nom d'hôte qui désigne le locataire.
    """
    from app.infrastructure.config import configuration

    base = configuration().adresse_publique_site.rstrip("/")
    # ⚠️ `compte.activation_creation`, et non `compte.activation` : celui-ci
    # ouvre sur « votre souscription X est encaissée ». Elle n'a pas souscrit,
    # elle a fait immatriculer sa société — et la référence n'existant pas, la
    # phrase sortait avec une double espace.
    poste.envoyer(
        "compte.activation_creation",
        destinataire=compte.courriel,
        contexte={
            "prenom": compte.appellation,
            "denomination": denomination,
            "niu": niu,
            "lien": f"{base}/activation?jeton={secret}",
            "expire_le": jeton.expire_le.strftime("%d/%m/%Y à %Hh%M"),
        },
    )


def abonne_d_acces_adherent(unite_de_travail: Any = None, notifier: Any = None):
    """La fabrique inscrite au relais. Rend un abonné neuf sur la session du moment."""

    def ouvrir(evenement: EvenementSortant) -> None:
        ouvrir_l_acces_de_la_fondatrice(
            evenement, unite_de_travail=unite_de_travail, notifier=notifier
        )

    return ouvrir
