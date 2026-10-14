"""Le compte de la cliente qui vient de payer la création de sa société.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Ouverture automatique du compte client au paiement, avec définition du mot de
passe. » Jusque-là, la cliente d'une création suivait son dossier par un lien
signé, sans compte, et son compte n'arrivait qu'avec sa société, des semaines
plus tard. Elle n'avait aucun espace à elle pendant la période où elle attend
le plus.

⚠️ LE LIEN SANS MOT DE PASSE RESTE, ET LE COMPTE S'Y AJOUTE.

La raison qui l'avait fait choisir tient toujours : un formulaire à identifiants
pour envoyer une photo de sa carte d'identité fait abandonner des dossiers. Le
courriel d'ouverture porte donc les deux : choisir son mot de passe, ou suivre
tout de suite sans compte.

⚠️ POURQUOI CE MODULE VIT ICI, ET NON DANS LA CRÉATION

L'identité est au Transverse, qui fait partie du socle : la création a le droit
de l'appeler, par sa surface publique. Les dépôts de comptes, l'unité de travail
et le lien vers la console sont des affaires du Transverse ; la création ne sait
que « ce dossier est ouvert, voici qui l'a payé ».

⚠️ UNE UNITÉ DE TRAVAIL À SOI

L'abonné qui appelle tourne hors requête, comme celui de l'accès adhérent : on
ouvre la sienne, sur le locataire du cabinet.

⚠️ CE QUE CE MODULE NE FAIT JAMAIS : LEVER

Le dossier de formalité est ouvert quand on arrive ici. Un compte qui ne s'ouvre
pas ne doit pas défaire un dossier payé : on rend `None`, on le dit au journal,
et le courriel part avec le seul lien de suivi.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.partage.horloge import maintenant

__all__ = ["PARCOURS_CREATION", "ouvrir_le_compte_de_la_creation"]

_journal = logging.getLogger(__name__)

#: Ce que la page d'activation lit pour parler de SA création, et non d'une
#: souscription. Un mot dans l'adresse, et non un secret : il ne change que le
#: texte de la page, jamais ce que le lien permet.
PARCOURS_CREATION = "creation"


def ouvrir_le_compte_de_la_creation(
    *,
    reference: str,
    courriel: str,
    nom: str,
    telephone: str | None = None,
    unite_de_travail: Any = None,
) -> str | None:
    """Ouvre son compte et rend le lien pour choisir son mot de passe, ou `None`.

    ─────────────────────────────────────────────────────────────────────────────
    TROIS CAS, ET UN SEUL OUVRE UN COMPTE

    * l'adresse est libre : le compte naît, rôle `CLIENT_CREATION`, portée ce
      seul dossier ;
    * l'adresse porte déjà SON compte pour CE dossier, jamais activé : un
      encaissement rejoué, ou un premier courriel perdu. Un lien neuf est émis ;
    * l'adresse porte un autre compte : on n'y touche pas. Un collaborateur ou
      une autre cliente peut porter l'adresse déclarée ; lui ajouter ce dossier
      donnerait une société en création à qui a saisi la même adresse.
    ─────────────────────────────────────────────────────────────────────────────
    """
    courriel = (courriel or "").strip().lower()
    if not courriel:
        return None
    try:
        return _ouvrir(reference, courriel, nom, telephone, unite_de_travail)
    except Exception:  # noqa: BLE001 — voir l'en-tête : jamais au prix du dossier
        _journal.exception(
            "Création %s : compte client NON ouvert. Le dossier EST ouvert, et le "
            "lien de suivi part seul.",
            reference,
        )
        return None


def _ouvrir(
    reference: str, courriel: str, nom: str, telephone: str | None, unite_de_travail: Any
) -> str | None:
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        unite_de_travail as unite_de_la_plateforme,
    )
    from app.contextes.transverse.application.activation import emettre_jeton
    from app.contextes.transverse.application.administration import (
        SYSTEME,
        ouvrir_acces_client_creation,
    )
    from app.contextes.transverse.domaine.identites import EtatCompte
    from app.contextes.transverse.domaine.jetons import TypeJeton
    from app.contextes.transverse.domaine.roles import Role
    from app.infrastructure.config import configuration
    from app.partage.locataire import etabli

    unite = unite_de_travail or unite_de_la_plateforme
    locataire = configuration().locataire_par_defaut
    instant = maintenant()
    with etabli(locataire), unite(locataire) as boutique:
        existant = boutique.comptes.par_courriel(courriel)
        if existant is None:
            _, _, secret = ouvrir_acces_client_creation(
                identifiant_compte=f"A-{uuid.uuid4().hex[:12]}",
                identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
                identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
                courriel=courriel,
                # ⚠️ Le nom entier, tel que la demande le porte : on ne fabrique
                # pas de prénom en le coupant. Voir `abonne_acces_adherent`.
                nom=nom or courriel.split("@")[0],
                prenom="",
                reference_creation=reference,
                locataire=locataire,
                depuis=instant.date(),
                comptes=boutique.comptes,
                habilitations=boutique.habilitations,
                jetons=boutique.jetons,
                journal=boutique.journal,
                a_l_instant=instant,
                telephone=telephone or None,
            )
            _journal.info("Création %s : compte client ouvert.", reference)
            return _lien(secret)

        suit_ce_dossier = any(
            h.role is Role.CLIENT_CREATION and h.portee is not None and reference in h.portee
            for h in boutique.habilitations.pour_compte(existant.identifiant)
        )
        if suit_ce_dossier and existant.etat is EtatCompte.EN_ATTENTE_ACTIVATION:
            _, secret = emettre_jeton(
                existant,
                TypeJeton.ACTIVATION,
                identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
                jetons=boutique.jetons,
                journal=boutique.journal,
                a_l_instant=instant,
                emis_par=SYSTEME,
            )
            return _lien(secret)

        _journal.info(
            "Création %s : l'adresse porte déjà un compte, aucun compte ouvert. Le "
            "lien de suivi part seul.",
            reference,
        )
        return None


def _lien(secret: str) -> str:
    """Le lien d'activation, sur la console du cabinet.

    ⚠️ La même adresse que celle de l'accès adhérent (`abonne_acces_adherent`) :
    le compte est créé dans le locataire du cabinet, que désigne cet hôte.
    """
    from app.infrastructure.config import configuration

    base = configuration().adresse_publique_site.rstrip("/")
    return f"{base}/activation?jeton={secret}&parcours={PARCOURS_CREATION}"
