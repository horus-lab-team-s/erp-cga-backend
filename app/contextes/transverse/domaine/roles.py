"""Les rôles du cabinet et ce que chacun a le droit de faire.

─────────────────────────────────────────────────────────────────────────────────
LES RÔLES SONT CEUX DU CABINET, PAS CEUX DU LOGICIEL

Ni « user », ni « manager », ni « super-admin ». Un chargé de clientèle, un
comptable, un réviseur, un fiscaliste existent réellement chez CGA Broad Range,
occupent des postes distincts et n'ont pas les mêmes responsabilités devant
l'ordre professionnel. Le logiciel nomme ce qui existe déjà.

La conséquence pratique est immédiate : quand la direction dit « le réviseur seul
dépose », il n'y a rien à traduire. Une hiérarchie de niveaux numériques aurait
exigé de décider si un fiscaliste est « au-dessus » d'un comptable — question qui
n'a aucun sens dans un cabinet, où ce sont deux métiers et non deux grades.

L'ADMINISTRATEUR N'EST PAS TOUT-PUISSANT, ET C'EST DÉLIBÉRÉ

Il crée les comptes, distribue les rôles, affecte les dossiers, lit le journal
d'audit. Il ne valide **aucune** écriture, ne dépose **aucune** déclaration,
n'écarte **aucun** constat.

C'est la séparation des tâches, et elle est ici défendable devant un vérificateur :
la personne qui peut s'octroyer un droit ne doit pas être celle qui l'exerce. Un
administrateur qui voudrait valider une écriture doit d'abord s'attribuer le rôle
de comptable — et cette attribution laisse une trace datée que le journal d'audit
conserve. Le contournement reste possible ; ce qui compte, c'est qu'il soit
**visible**.

TROIS RÔLES NE SONT PAS DES SALARIÉS DU CABINET

L'**adhérent** consulte son propre dossier et y dépose ses pièces. L'**inspecteur
assistant** lit et émet un avis, sans jamais écrire. Le **chargé de formalités**
suit les créations d'entreprise et n'a rien à voir avec la comptabilité.

Le **client en création** (14 octobre 2026) a payé la création de sa société et
la suit, avant qu'elle existe : sa portée est son dossier de formalité.

Ces quatre-là doivent obligatoirement porter une portée explicite : un adhérent
dont la portée serait « tous les dossiers » lirait la comptabilité de ses
concurrents. Voir `habilitations.py`, où la contrainte est vérifiée.

LA MATRICE SE RÈGLE PAR LE CABINET (18 octobre 2026)

Le gérant a demandé « un RBAC dynamique, qui ressorte la matrice des rôles et cache
les vues dont un rôle n'a pas le droit ». `PERMISSIONS_PAR_ROLE` reste la matrice
**par défaut**, livrée ; le cabinet la règle par-dessus, rôle par rôle, et crée ses
propres rôles (« ressources humaines »). Ce qui est réglé vit en base, par locataire ;
`matrice_en_vigueur()` rend la matrice du cabinet servi, et `permissions_de` la lit.

Celui qui règle est un rôle à part, le **super administrateur**, sur décision du
gérant. Il ne tient aucun acte métier : la séparation des tâches de l'en-tête tient
toujours, elle passe seulement d'une constante à une décision tracée. Trois bornes ne
se règlent pas, parce qu'elles protègent le cabinet de lui-même :

* `GERER_ROLES` et `ACCORDER_FAVEURS` n'appartiennent qu'au super administrateur, et
  il ne peut pas les perdre : une matrice qui ne laisse plus personne la régler ne se
  répare qu'en base ;
* un rôle externe (adhérent, client, inspecteur) ne reçoit que des droits externes :
  un adhérent qui validerait des écritures validerait les siennes ;
* les droits du client (régler ses honoraires, confier ses fonds…) ne vont qu'aux
  rôles externes : chez un collaborateur, sur tout le portefeuille, ils feraient payer
  le cabinet à la place de tous ses adhérents.

Un rôle créé par le cabinet est toujours **interne** : un rôle externe exige une
portée, et cette mécanique ne s'invente pas depuis un écran.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "EXIGE_MFA",
    "EXIGE_MOTIF",
    "FAMILLES_DE_PERMISSIONS",
    "LIBELLES_PERMISSION",
    "LIBELLES_ROLE",
    "PERMISSIONS_DU_CLIENT",
    "PERMISSIONS_EXTERNES",
    "PERMISSIONS_DE_TRAITEMENT",
    "PERMISSIONS_PAR_ROLE",
    "PERMISSIONS_RESERVEES",
    "ROLES_A_PORTEE_OBLIGATOIRE",
    "ROLES_EXTERNES",
    "ROLES_INTERNES",
    "CodeRole",
    "FicheDeRole",
    "Permission",
    "Role",
    "est_interne",
    "installer_la_source_des_roles",
    "libelle_role",
    "matrice_en_vigueur",
    "matrice_par_defaut",
    "permissions_de",
    "role_de",
]


class Role(StrEnum):
    """Les neuf rôles de `Docs/architecture/05-securite-multitenant.md` § 2,
    et deux autres ajoutés le 14 octobre 2026 sur décision du cabinet : la caisse,
    et le client en création."""

    CHARGE_CLIENTELE = "CHARGE_CLIENTELE"
    COMPTABLE = "COMPTABLE"
    REVISEUR = "REVISEUR"
    FISCALISTE = "FISCALISTE"
    CHARGE_FORMALITES = "CHARGE_FORMALITES"
    DIRECTION = "DIRECTION"
    ADMINISTRATEUR = "ADMINISTRATEUR"
    #: La responsable de caisse : elle constate les règlements, et rien d'autre.
    #: Voir `Permission.ENCAISSER`.
    CAISSE = "CAISSE"
    #: Externe — le dirigeant de l'entreprise suivie.
    ADHERENT = "ADHERENT"
    #: Externe — la cliente qui a payé la création de sa société, avant que
    #: celle-ci existe. Sa portée est son dossier de formalité (`CRE-…`), et
    #: rien d'autre. Voir `Permission.SUIVRE_MA_CREATION`.
    CLIENT_CREATION = "CLIENT_CREATION"
    #: Externe — lecture et avis seulement, jamais d'écriture.
    INSPECTEUR = "INSPECTEUR"
    #: 18 octobre 2026, décision du gérant : celui qui règle la matrice des rôles et
    #: accorde les faveurs (mandat « le cabinet paie pour moi », grâce de règlement).
    #: Aucun acte métier. Voir l'en-tête.
    SUPER_ADMIN = "SUPER_ADMIN"


#: Le nom de chaque rôle en clair, pour ce qui est lu par un humain.
#:
#: ⚠️ La valeur de l'énumération n'est pas un libellé. `CHARGE_CLIENTELE` dans un
#: courriel d'invitation se lit comme une erreur de logiciel ; « chargé de
#: clientèle » se lit comme une fonction. Le tableau vit ici plutôt que dans
#: l'adaptateur qui l'affiche, parce que trois surfaces l'emploient déjà —
#: courriel, écran des comptes, journal d'audit — et que trois copies
#: divergeraient.
LIBELLES_ROLE: dict[Role, str] = {
    Role.CHARGE_CLIENTELE: "chargé de clientèle",
    Role.COMPTABLE: "comptable",
    Role.REVISEUR: "réviseur",
    Role.FISCALISTE: "fiscaliste",
    Role.CHARGE_FORMALITES: "chargé de formalités",
    Role.DIRECTION: "direction",
    Role.ADMINISTRATEUR: "administrateur",
    Role.CAISSE: "responsable de caisse",
    Role.ADHERENT: "adhérent",
    Role.CLIENT_CREATION: "client en création",
    Role.INSPECTEUR: "inspecteur",
    Role.SUPER_ADMIN: "super administrateur",
}


def libelle_role(role: Role | str) -> str:
    """Le rôle en clair, ou sa valeur brute s'il est inconnu.

    Ne lève pas : un libellé manquant ne doit pas empêcher un courriel de partir
    ni un écran de s'afficher. Le pire est alors une valeur technique visible,
    qui se corrige ; une exception, elle, coûterait l'invitation.

    18 octobre 2026 : un rôle créé par le cabinet se lit à la matrice en vigueur.
    """
    try:
        return LIBELLES_ROLE[Role(role)]
    except ValueError:
        fiche = matrice_en_vigueur().get(str(role))
        return fiche.libelle if fiche else str(role)


class Permission(StrEnum):
    """Une action, nommée du point de vue métier.

    Pas de `read` / `write` / `delete` : ce vocabulaire technique obligerait à
    décider si « écarter un constat » est une écriture ou une suppression, alors
    que c'est **un acte professionnel engageant celui qui le pose**. La permission
    porte donc le nom de l'acte, et le contrôle devient lisible dans le code
    appelant comme dans le journal d'audit.
    """

    # ── Lecture ──────────────────────────────────────────────────────────────
    LIRE_DOSSIER = "LIRE_DOSSIER"
    LIRE_PIECE = "LIRE_PIECE"
    LIRE_COMPTABILITE = "LIRE_COMPTABILITE"
    LIRE_AUDIT = "LIRE_AUDIT"
    LIRE_PILOTAGE = "LIRE_PILOTAGE"
    #: Pas 100 : décider une mesure sur un dossier à risque (« Exiger une régularisation
    #: datée », « Envisager la fin d'adhésion »), ou la clore. Distincte de `LIRE_PILOTAGE`
    #: parce qu'une lecture n'engage personne et qu'une décision engage le cabinet envers
    #: l'adhérent : un rôle de lecture du pilotage, s'il en naît un, ne doit pas décider.
    DECIDER_SUR_DOSSIER = "DECIDER_SUR_DOSSIER"

    # ── Collecte ─────────────────────────────────────────────────────────────
    DEPOSER_PIECE = "DEPOSER_PIECE"
    IDENTIFIER_PIECE = "IDENTIFIER_PIECE"
    ARBITRER_DOUBLON = "ARBITRER_DOUBLON"
    RELANCER_ADHERENT = "RELANCER_ADHERENT"
    #: Pas 111 : composer et envoyer à un adhérent la relance des pièces qui manquent à son mois
    #: (maquette « Parcours comptable », vue F). Distincte de `RELANCER_ADHERENT`, qui ouvre aussi
    #: la relance des honoraires impayés : le comptable relance ses pièces, pas la facture du
    #: cabinet.
    RELANCER_PIECES = "RELANCER_PIECES"
    #: Pas 112 : répondre à une demande du cabinet (« Je l'aurai la semaine prochaine », « Je n'ai
    #: pas ce document », un message). **L'adhérent seul** : un collaborateur qui répondrait à sa
    #: place écrirait au dossier une parole que l'adhérent n'a pas dite, et la relance suivante
    #: s'appuierait dessus.
    REPONDRE_AU_CABINET = "REPONDRE_AU_CABINET"
    #: Pas 114 : signaler un changement de son entreprise (adresse, gérant, activité, téléphone).
    #: **L'adhérent seul**, comme la réponse au cabinet : un collaborateur qui apprend un changement
    #: l'instruit directement au dossier, il ne se l'annonce pas à lui-même.
    SIGNALER_UN_CHANGEMENT = "SIGNALER_UN_CHANGEMENT"
    #: Pas 115 : régler ses propres rappels d'échéance. L'adhérent seul : un collaborateur qui
    #: couperait les rappels d'un adhérent le priverait d'un avertissement qu'il a choisi.
    REGLER_SES_RAPPELS = "REGLER_SES_RAPPELS"
    #: Tenir la paie de SA PROPRE entreprise : inscrire ses salariés, ouvrir et clore leurs
    #: contrats, lire les bulletins calculés et la déclaration mensuelle.
    #:
    #: ─────────────────────────────────────────────────────────────────────────────
    #: ⚠️ POURQUOI UNE PERMISSION NOUVELLE, ET NON `LIRE_COMPTABILITE` ÉLARGIE
    #:
    #: Les routes de paie exigeaient `LIRE_COMPTABILITE`, que l'adhérent ne détient
    #: pas, et ce refus était délibéré : un bulletin porte des rémunérations
    #: nominatives, et ouvrir la comptabilité d'un dossier à son titulaire pour qu'il
    #: voie sa paie lui ouvrirait au passage la balance, le grand livre et le journal.
    #:
    #: Une permission propre dit exactement ce qu'elle autorise, et rien de plus. Elle
    #: est **toujours cadrée sur un dossier** par `exiger_dossier` : le périmètre d'un
    #: adhérent est l'entreprise dont il est titulaire, et une paie demandée sur un
    #: autre NIU rend 404 — pas 403, qui apprendrait que ce dossier existe.
    #:
    #: ⚠️ Elle N'EST PAS donnée aux rôles du cabinet, et ce n'est pas un oubli. Le
    #: collaborateur tient la paie de ses dossiers par `LIRE_COMPTABILITE`, qui lui
    #: ouvre aussi les livres dont il a besoin pour la contrôler. Lui ajouter
    #: celle-ci ne lui donnerait rien et brouillerait la lecture de la matrice.
    #: ─────────────────────────────────────────────────────────────────────────────
    TENIR_SA_PAIE = "TENIR_SA_PAIE"
    #: Suivre la création de SA société, y déposer ses pièces : la cliente qui a
    #: payé, entre son règlement et son immatriculation.
    #:
    #: ─────────────────────────────────────────────────────────────────────────────
    #: ⚠️ POURQUOI UN RÔLE ET UNE PERMISSION À EUX (décision du 14 octobre 2026)
    #:
    #: Le cabinet a demandé « un compte client ouvert automatiquement au paiement,
    #: avec définition du mot de passe ». `ADHERENT` ne convient pas : sa portée est
    #: un NIU, et au moment du paiement l'entreprise n'existe pas. Lui donner
    #: `LIRE_DOSSIER` sur une référence de formalité ouvrirait les écrans de
    #: l'espace adhérent sur un dossier qui n'est pas au portefeuille.
    #:
    #: La permission ne vaut que sur les dossiers de sa portée, comme celles de
    #: l'adhérent : une référence demandée hors de sa portée rend 404.
    #:
    #: ⚠️ Le lien signé sans mot de passe reste valable en parallèle : le compte
    #: s'ajoute au lien, il ne le remplace pas.
    #: ─────────────────────────────────────────────────────────────────────────────
    SUIVRE_MA_CREATION = "SUIVRE_MA_CREATION"
    #: Régler SES honoraires depuis son espace : demander le débit de son mobile
    #: money pour une période due (14 octobre 2026). L'adhérent seul, cadré sur
    #: son dossier ; le cabinet constate un règlement par `ENCAISSER`.
    REGLER_SES_HONORAIRES = "REGLER_SES_HONORAIRES"
    #: Vérifier la quittance qu'un adhérent a envoyée pour une échéance fiscale ou sociale, et
    #: dire si elle règle l'échéance (14 octobre 2026). Le comptable et le réviseur : ce sont eux
    #: qui lisent les quittances pour comptabiliser l'impôt payé. Un verdict engage le cabinet
    #: envers l'adhérent (« payée »), il n'est donc pas donné à qui ne tient pas le dossier.
    VERIFIER_UN_REGLEMENT = "VERIFIER_UN_REGLEMENT"
    #: Remettre au cabinet les fonds d'une échéance qu'il paie pour soi (14 octobre 2026) :
    #: l'adhérent seul, cadré sur son dossier, et seulement sous mandat de paiement.
    CONFIER_SES_FONDS = "CONFIER_SES_FONDS"

    # ── Conformité ───────────────────────────────────────────────────────────
    CONTROLER_CONFORMITE = "CONTROLER_CONFORMITE"
    ECARTER_CONSTAT = "ECARTER_CONSTAT"
    EMETTRE_AVIS = "EMETTRE_AVIS"

    # ── Comptabilité ─────────────────────────────────────────────────────────
    SAISIR_ECRITURE = "SAISIR_ECRITURE"
    VALIDER_ECRITURE = "VALIDER_ECRITURE"
    CONTRE_PASSER = "CONTRE_PASSER"
    #: Pas 102 : poser des remarques sur un mois transmis, le renvoyer, le valider. Le
    #: réviseur seul : la revue est le second regard sur ce que le comptable a tenu, et un
    #: comptable qui la détiendrait pourrait réviser le mois de son collègue sans en avoir
    #: le mandat. Celui qui a transmis ne valide jamais sa propre revue (voir `RevueDeDossier`).
    REVISER_DOSSIER = "REVISER_DOSSIER"

    # ── Obligations et clôture ───────────────────────────────────────────────
    DEPOSER_DECLARATION = "DEPOSER_DECLARATION"
    CLOTURER_EXERCICE = "CLOTURER_EXERCICE"
    #: Figer la liasse d'un exercice clos en une version de DSF (14 octobre 2026). Le
    #: comptable et le réviseur : préparer n'engage rien, la validation (REVISER_DOSSIER,
    #: par un autre que le préparateur) et le dépôt (DEPOSER_DECLARATION) engagent.
    PREPARER_DSF = "PREPARER_DSF"
    #: Renseigner la fiche signalétique de la DSF (fiches R1 à R3) : l'adhérent dans son
    #: espace, et le cabinet pour lui (15 octobre 2026).
    RENSEIGNER_FICHE_DSF = "RENSEIGNER_FICHE_DSF"
    #: La valider, ou la renvoyer au client avec un motif : le réviseur.
    VALIDER_FICHE_DSF = "VALIDER_FICHE_DSF"
    #: Instruire une mission de prestation ponctuelle (15 octobre 2026) : ouvrir, faire
    #: lire par l'agent, décider de chaque point, soumettre. Le juriste (chargé des
    #: formalités) et le réviseur.
    TRAITER_MISSION = "TRAITER_MISSION"
    #: Valider une mission soumise : le second regard, jamais celui qui a soumis. Le
    #: réviseur et la direction.
    VALIDER_MISSION = "VALIDER_MISSION"

    # ── Portefeuille ─────────────────────────────────────────────────────────
    #
    # ⚠️ **Inscrire un statut n'est pas modifier un dossier.** Un changement de
    # régime ou de rattachement change ce que l'entreprise doit à
    # l'administration, à compter d'une date. C'est un acte qui engage le cabinet
    # vis-à-vis de l'administration, au même titre que clore un exercice : il
    # appartient donc aux mêmes personnes, et il exige le même motif écrit.
    INSCRIRE_STATUT = "INSCRIRE_STATUT"

    # ── Référentiel ──────────────────────────────────────────────────────────
    MODIFIER_PARAMETRE = "MODIFIER_PARAMETRE"
    MODIFIER_REGLE = "MODIFIER_REGLE"
    #: Pas 95 : arrêter une valeur que **aucun texte ne fixe** (paramètre de nature
    #: POLITIQUE_CABINET : poids du score de risque, délais internes). Distincte de
    #: `MODIFIER_PARAMETRE`, qui engage un professionnel sur un texte : la direction
    #: n'a pas qualité pour attester un taux légal, le fiscaliste n'a pas qualité pour
    #: décider du poids d'un retard dans une note interne. Voir `NatureParametre`.
    ARRETER_POLITIQUE = "ARRETER_POLITIQUE"

    # ── Commerce, avant qu'il y ait un dossier ───────────────────────────────
    #
    # ⚠️ **Distinctes de `LIRE_DOSSIER` à dessein.** Un prospect n'est pas un
    # adhérent : il n'a ni dossier, ni comptabilité, ni pièces. Réutiliser les
    # permissions du portefeuille donnerait au commercial l'accès à la
    # comptabilité de tous les adhérents, et à l'inverse priverait un chargé de
    # clientèle de la file des demandes.
    #
    # La séparation coûte deux membres d'énumération. La confusion coûterait une
    # fuite dont personne ne verrait la cause.
    LIRE_PROSPECT = "LIRE_PROSPECT"
    QUALIFIER_PROSPECT = "QUALIFIER_PROSPECT"
    #: Arrêter le prix d'une prestation du catalogue, à partir d'une date.
    #: **La direction seule** : un prix engage le cabinet envers chaque client
    #: qui le paie. Le moteur de chiffrage « rend un intervalle, jamais un prix »
    #: (référentiel de tarification) ; c'est cette permission qui porte la
    #: décision humaine.
    FIXER_LES_TARIFS = "FIXER_LES_TARIFS"

    # ── Formalités et administration ─────────────────────────────────────────
    SUIVRE_FORMALITE = "SUIVRE_FORMALITE"
    GERER_COMPTES = "GERER_COMPTES"
    #: Constater un règlement : demander un paiement par téléphone, ou confirmer
    #: un versement reçu au guichet ou par virement.
    #:
    #: ─────────────────────────────────────────────────────────────────────────
    #: ⚠️ POURQUOI UNE PERMISSION À ELLE, ET UN RÔLE POUR LA PORTER
    #:
    #: Le cabinet l'a décidé le 14 octobre 2026 : « c'est une personne,
    #: généralement la responsable de caisse, qui doit encaisser les versements ».
    #: Jusque-là le geste exigeait `GERER_COMPTES`, donc l'administrateur : celle
    #: qui tient la caisse n'avait pas le droit de dire qu'un client avait payé,
    #: et celui qui l'avait pouvait aussi créer des comptes et lire le journal.
    #:
    #: Encaisser n'est ni administrer, ni comptabiliser, ni chiffrer. La
    #: permission dit exactement ce geste. Le rôle `CAISSE` ne porte qu'elle : la
    #: caissière voit ce qui est dû et ce qui est réglé, et aucun livre.
    #:
    #: ⚠️ L'administrateur la garde. La lui retirer aurait laissé sans caissière
    #: tout cabinet qui n'a nommé personne à ce rôle, et ses clients sans moyen
    #: de voir leur règlement constaté.
    #:
    #: ⚠️ La séparation tient toujours : `QUALIFIER_PROSPECT`, qui arrête le prix,
    #: n'est donnée ni à la caisse ni à l'administrateur. Celle qui chiffre
    #: n'encaisse pas.
    #: ─────────────────────────────────────────────────────────────────────────
    ENCAISSER = "ENCAISSER"
    AFFECTER_DOSSIER = "AFFECTER_DOSSIER"
    EDITER_VITRINE = "EDITER_VITRINE"

    # ── La matrice et les faveurs (18 octobre 2026) ──────────────────────────
    #: Régler la matrice : cocher les droits d'un rôle, créer un rôle du cabinet.
    GERER_ROLES = "GERER_ROLES"
    #: Accorder une faveur à un adhérent nommé : le mandat « le cabinet paie pour
    #: moi », une grâce de règlement. Un écart à la règle commune, donc une décision.
    ACCORDER_FAVEURS = "ACCORDER_FAVEURS"

    # ── Ce que la caisse voit (18 octobre 2026) ──────────────────────────────
    #: « L'administrateur peut vouloir retirer la vue sur certains versements, de façon
    #: explicite. » Trois familles, une case chacune dans la matrice : les versements
    #: commerciaux (inscription, création, prestations, abonnements en ligne), les
    #: honoraires, les fonds de tiers (TVA, CNPS, IRPP), qui ne sont pas au cabinet.
    VOIR_VERSEMENTS_COMMERCE = "VOIR_VERSEMENTS_COMMERCE"
    VOIR_VERSEMENTS_HONORAIRES = "VOIR_VERSEMENTS_HONORAIRES"
    VOIR_VERSEMENTS_FONDS_DE_TIERS = "VOIR_VERSEMENTS_FONDS_DE_TIERS"

    #: 18 octobre 2026 (point 25) : lire les candidatures et leurs CV, décider, répondre.
    #: Distincte de `EDITER_VITRINE` : un CV est une donnée personnelle, et qui édite le
    #: site n'a pas à lire les dossiers des candidats. Un rôle « ressources humaines » se
    #: crée dans la matrice et reçoit ce droit.
    GERER_RECRUTEMENT = "GERER_RECRUTEMENT"


#: Chaque droit en clair, rangé par famille, pour la matrice que lit le gérant
#: (18 octobre 2026). ⚠️ Un droit ajouté sans libellé fait échouer `test_matrice_des_roles` :
#: une case sans nom dans la grille se coche au hasard.
FAMILLES_DE_PERMISSIONS: tuple[tuple[str, tuple[tuple[Permission, str], ...]], ...] = (
    (
        "Lecture et pilotage",
        (
            (Permission.LIRE_DOSSIER, "Lire les dossiers"),
            (Permission.LIRE_PIECE, "Lire les pièces"),
            (Permission.LIRE_COMPTABILITE, "Lire la comptabilité"),
            (Permission.LIRE_AUDIT, "Lire le journal d'audit"),
            (Permission.LIRE_PILOTAGE, "Lire le pilotage"),
            (Permission.DECIDER_SUR_DOSSIER, "Décider une mesure sur un dossier à risque"),
        ),
    ),
    (
        "Pièces et relances",
        (
            (Permission.DEPOSER_PIECE, "Déposer une pièce"),
            (Permission.IDENTIFIER_PIECE, "Identifier une pièce"),
            (Permission.ARBITRER_DOUBLON, "Arbitrer un doublon"),
            (Permission.RELANCER_ADHERENT, "Relancer un adhérent"),
            (Permission.RELANCER_PIECES, "Relancer les pièces manquantes"),
            (Permission.VERIFIER_UN_REGLEMENT, "Vérifier une quittance"),
        ),
    ),
    (
        "Gestes du client",
        (
            (Permission.REPONDRE_AU_CABINET, "Répondre au cabinet"),
            (Permission.SIGNALER_UN_CHANGEMENT, "Signaler un changement"),
            (Permission.REGLER_SES_RAPPELS, "Régler ses rappels"),
            (Permission.TENIR_SA_PAIE, "Tenir sa paie"),
            (Permission.SUIVRE_MA_CREATION, "Suivre sa création"),
            (Permission.REGLER_SES_HONORAIRES, "Régler ses honoraires"),
            (Permission.CONFIER_SES_FONDS, "Confier ses fonds (TVA, CNPS)"),
        ),
    ),
    (
        "Conformité",
        (
            (Permission.CONTROLER_CONFORMITE, "Contrôler la conformité"),
            (Permission.ECARTER_CONSTAT, "Écarter un constat"),
            (Permission.EMETTRE_AVIS, "Émettre un avis"),
        ),
    ),
    (
        "Comptabilité",
        (
            (Permission.SAISIR_ECRITURE, "Saisir une écriture"),
            (Permission.VALIDER_ECRITURE, "Valider une écriture"),
            (Permission.CONTRE_PASSER, "Contre-passer une écriture"),
            (Permission.REVISER_DOSSIER, "Réviser un dossier"),
        ),
    ),
    (
        "Déclarations, clôture et missions",
        (
            (Permission.DEPOSER_DECLARATION, "Déposer une déclaration"),
            (Permission.CLOTURER_EXERCICE, "Clôturer un exercice"),
            (Permission.PREPARER_DSF, "Préparer la DSF"),
            (Permission.RENSEIGNER_FICHE_DSF, "Renseigner la fiche de la DSF"),
            (Permission.VALIDER_FICHE_DSF, "Valider la fiche de la DSF"),
            (Permission.TRAITER_MISSION, "Traiter une mission ponctuelle"),
            (Permission.VALIDER_MISSION, "Valider une mission ponctuelle"),
            (Permission.INSCRIRE_STATUT, "Inscrire un statut (régime, NIU, RCCM)"),
        ),
    ),
    (
        "Référentiel",
        (
            (Permission.MODIFIER_PARAMETRE, "Modifier un paramètre"),
            (Permission.MODIFIER_REGLE, "Modifier une règle"),
            (Permission.ARRETER_POLITIQUE, "Arrêter une politique du cabinet"),
        ),
    ),
    (
        "Commerce, formalités et caisse",
        (
            (Permission.LIRE_PROSPECT, "Lire les demandes"),
            (Permission.QUALIFIER_PROSPECT, "Qualifier une demande"),
            (Permission.FIXER_LES_TARIFS, "Fixer les tarifs et honoraires"),
            (Permission.SUIVRE_FORMALITE, "Suivre une création d'entreprise"),
            (Permission.ENCAISSER, "Encaisser"),
            (Permission.AFFECTER_DOSSIER, "Affecter un dossier"),
            (Permission.EDITER_VITRINE, "Éditer le site et les offres de poste"),
            (Permission.GERER_RECRUTEMENT, "Traiter les candidatures (CV, réponses)"),
        ),
    ),
    (
        "Caisse : versements visibles",
        (
            (Permission.VOIR_VERSEMENTS_COMMERCE, "Voir inscriptions, créations, prestations"),
            (Permission.VOIR_VERSEMENTS_HONORAIRES, "Voir les honoraires"),
            (Permission.VOIR_VERSEMENTS_FONDS_DE_TIERS, "Voir les fonds de tiers (TVA, CNPS…)"),
        ),
    ),
    (
        "Administration",
        (
            (Permission.GERER_COMPTES, "Gérer les comptes"),
            (Permission.GERER_ROLES, "Régler la matrice des rôles"),
            (Permission.ACCORDER_FAVEURS, "Accorder une faveur à un adhérent"),
        ),
    ),
)

LIBELLES_PERMISSION: dict[Permission, str] = {
    permission: libelle for _, droits in FAMILLES_DE_PERMISSIONS for permission, libelle in droits
}


#: Les actions du tableau « actions sensibles » de 05-securite-multitenant.md qui
#: réclament une authentification forte. Le domaine se contente de le déclarer :
#: c'est la couche d'autorisation qui refuse, et l'adaptateur qui sait comment la
#: seconde preuve a été obtenue.
#:
#: ⚠️ Le second facteur n'est pas encore implémenté. La déclaration existe pour
#: que le jour où il l'est, il n'y ait rien à retrouver — et pour que
#: `autoriser()` puisse déjà refuser une session non renforcée.
#:
#: 18 octobre 2026 : régler la matrice ou accorder une faveur change ce que d'autres
#: peuvent faire, ou ce que le cabinet doit à un adhérent. Même exigence.
EXIGE_MFA: frozenset[Permission] = frozenset(
    {Permission.DEPOSER_DECLARATION, Permission.GERER_ROLES, Permission.ACCORDER_FAVEURS}
)


#: Les actions qui ne s'exécutent pas sans motif écrit.
#:
#: Écarter un constat de non-conformité, c'est décider qu'une anomalie détectée
#: par le moteur n'en est pas une. Contre-passer, c'est annuler une écriture déjà
#: validée. Dans les deux cas, un vérificateur demandera « pourquoi », et
#: « le réviseur l'a jugé ainsi » n'est pas une réponse. Le motif est donc exigé
#: au moment de l'acte, pas reconstitué après coup.
EXIGE_MOTIF: frozenset[Permission] = frozenset(
    {
        Permission.ECARTER_CONSTAT,
        Permission.CONTRE_PASSER,
        Permission.CLOTURER_EXERCICE,
        # Un vérificateur demandera pourquoi ce dossier est passé au réel le
        # 1er janvier et pas le 1er juillet. La lettre de l'administration ou la
        # liasse qui le fonde doit être nommée au moment de l'acte.
        Permission.INSCRIRE_STATUT,
        # Une mesure de direction se relit en comité et peut finir en fin d'adhésion :
        # « pourquoi » doit être écrit au moment où elle est décidée, puis close.
        Permission.DECIDER_SUR_DOSSIER,
        # 18 octobre 2026 : un droit donné ou retiré, une faveur accordée à un adhérent
        # nommé. Dans trois ans, « pourquoi lui » doit avoir une réponse écrite.
        Permission.GERER_ROLES,
        Permission.ACCORDER_FAVEURS,
    }
)


_LECTURE_DOSSIER: frozenset[Permission] = frozenset(
    {Permission.LIRE_DOSSIER, Permission.LIRE_PIECE}
)

_VOIR_LES_VERSEMENTS: frozenset[Permission] = frozenset(
    {
        Permission.VOIR_VERSEMENTS_COMMERCE,
        Permission.VOIR_VERSEMENTS_HONORAIRES,
        Permission.VOIR_VERSEMENTS_FONDS_DE_TIERS,
    }
)

PERMISSIONS_PAR_ROLE: dict[Role, frozenset[Permission]] = {
    # L'adhérent voit son dossier et y dépose. Il ne voit pas la comptabilité
    # tant qu'elle est en cours d'établissement : un solde intermédiaire lu comme
    # définitif conduit à des décisions de trésorerie fondées sur un brouillon.
    Role.ADHERENT: _LECTURE_DOSSIER
    | {
        Permission.DEPOSER_PIECE,
        Permission.REPONDRE_AU_CABINET,
        Permission.SIGNALER_UN_CHANGEMENT,
        Permission.RENSEIGNER_FICHE_DSF,
        Permission.REGLER_SES_RAPPELS,
        Permission.TENIR_SA_PAIE,
        Permission.REGLER_SES_HONORAIRES,
        Permission.CONFIER_SES_FONDS,
    },
    # Le chargé de clientèle est l'interlocuteur de l'adhérent. Il voit tout du
    # dossier, il relance, et il saisit ce qu'on lui apporte — voir la note sur
    # DEPOSER_PIECE plus bas. Il ne touche à rien d'autre.
    Role.CHARGE_CLIENTELE: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
        Permission.RELANCER_ADHERENT,
        Permission.RELANCER_PIECES,
        Permission.DEPOSER_PIECE,
    },
    Role.COMPTABLE: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
        Permission.VERIFIER_UN_REGLEMENT,
        # ⚠️ Ajoutée le jour du branchement des routes, et le manque était réel :
        # `CanalDepot.DEPOT_CABINET` existe, ce qui veut dire qu'un adhérent
        # apporte ses pièces en main propre et qu'un collaborateur les saisit.
        # Réserver DEPOSER_PIECE aux seuls adhérents aurait rendu ce canal
        # inutilisable — et le canal, lui, décrit ce qui se passe réellement.
        Permission.DEPOSER_PIECE,
        Permission.IDENTIFIER_PIECE,
        Permission.ARBITRER_DOUBLON,
        Permission.CONTROLER_CONFORMITE,
        Permission.SAISIR_ECRITURE,
        Permission.VALIDER_ECRITURE,
        Permission.CONTRE_PASSER,
        Permission.RELANCER_PIECES,
        Permission.PREPARER_DSF,
        Permission.RENSEIGNER_FICHE_DSF,
    },
    # Le réviseur reprend tout ce que fait le comptable, et détient seul les
    # trois actes qui engagent le cabinet vis-à-vis de l'administration.
    Role.REVISEUR: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
        Permission.VERIFIER_UN_REGLEMENT,
        Permission.LIRE_AUDIT,
        Permission.REVISER_DOSSIER,
        Permission.RELANCER_PIECES,
        Permission.DEPOSER_PIECE,
        Permission.IDENTIFIER_PIECE,
        Permission.ARBITRER_DOUBLON,
        Permission.CONTROLER_CONFORMITE,
        Permission.SAISIR_ECRITURE,
        Permission.VALIDER_ECRITURE,
        Permission.CONTRE_PASSER,
        Permission.ECARTER_CONSTAT,
        Permission.DEPOSER_DECLARATION,
        Permission.CLOTURER_EXERCICE,
        Permission.INSCRIRE_STATUT,
        Permission.PREPARER_DSF,
        Permission.RENSEIGNER_FICHE_DSF,
        Permission.VALIDER_FICHE_DSF,
        Permission.TRAITER_MISSION,
        Permission.VALIDER_MISSION,
    },
    # Le fiscaliste ne tient aucune comptabilité : il fixe la norme que les
    # autres appliquent. C'est le seul à pouvoir toucher au référentiel, et cette
    # exclusivité est ce qui donne un sens au statut A_VALIDER.
    Role.FISCALISTE: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
        Permission.MODIFIER_PARAMETRE,
        Permission.MODIFIER_REGLE,
        Permission.CONTROLER_CONFORMITE,
    },
    Role.CHARGE_FORMALITES: {
        Permission.LIRE_DOSSIER,
        Permission.SUIVRE_FORMALITE,
        # Il reçoit les demandes de création : c'est lui qui les qualifie.
        Permission.LIRE_PROSPECT,
        Permission.QUALIFIER_PROSPECT,
        # 15 octobre 2026 : le juriste du cabinet instruit les prestations ponctuelles.
        Permission.TRAITER_MISSION,
    },
    Role.DIRECTION: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
        Permission.LIRE_PILOTAGE,
        Permission.DECIDER_SUR_DOSSIER,
        Permission.LIRE_AUDIT,
        Permission.AFFECTER_DOSSIER,
        Permission.CLOTURER_EXERCICE,
        Permission.INSCRIRE_STATUT,
        Permission.ARRETER_POLITIQUE,
        Permission.VALIDER_MISSION,
        Permission.EDITER_VITRINE,
        Permission.LIRE_PROSPECT,
        Permission.QUALIFIER_PROSPECT,
        Permission.FIXER_LES_TARIFS,
        Permission.GERER_RECRUTEMENT,
    }
    # 18 octobre 2026 : le suivi financier global, par axe.
    | _VOIR_LES_VERSEMENTS,
    # Voir l'en-tête : aucune permission comptable ou déclarative. 18 octobre 2026 : elle
    # voit les trois familles de versements ; la matrice peut lui en retirer une.
    Role.CAISSE: {Permission.ENCAISSER} | _VOIR_LES_VERSEMENTS,
    # Son dossier de formalité, et rien d'autre : ni dossier du portefeuille, ni
    # pièce comptable. Le jour où sa société est immatriculée et qu'elle prend le
    # suivi, son compte reçoit en plus le rôle `ADHERENT`.
    Role.CLIENT_CREATION: {Permission.SUIVRE_MA_CREATION},
    Role.ADMINISTRATEUR: {
        Permission.GERER_COMPTES,
        Permission.ENCAISSER,
        Permission.AFFECTER_DOSSIER,
        Permission.LIRE_AUDIT,
        Permission.EDITER_VITRINE,
        # Il affecte les demandes, donc il les voit. Il ne les qualifie pas :
        # remplir un questionnaire suppose d'avoir parlé au client.
        Permission.LIRE_PROSPECT,
    }
    | _VOIR_LES_VERSEMENTS,
    Role.INSPECTEUR: _LECTURE_DOSSIER
    | {Permission.LIRE_COMPTABILITE, Permission.LIRE_AUDIT, Permission.EMETTRE_AVIS},
    # 18 octobre 2026 : il règle et il accorde, il lit le journal qui en garde la trace.
    # Il ne tient aucun dossier ; il les lit, pour savoir à qui il accorde une grâce.
    Role.SUPER_ADMIN: {
        Permission.LIRE_DOSSIER,
        Permission.GERER_ROLES,
        Permission.ACCORDER_FAVEURS,
        Permission.GERER_COMPTES,
        Permission.LIRE_AUDIT,
    },
}


#: Les rôles qui ne peuvent jamais porter sur l'ensemble du portefeuille.
ROLES_A_PORTEE_OBLIGATOIRE: frozenset[Role] = frozenset(
    {Role.ADHERENT, Role.INSPECTEUR, Role.CLIENT_CREATION}
)

#: Les rôles qui ne sont pas des salariés du cabinet.
ROLES_EXTERNES: frozenset[Role] = frozenset({Role.ADHERENT, Role.INSPECTEUR, Role.CLIENT_CREATION})

#: Les rôles tenus par des salariés du cabinet. Sert à distinguer, à l'ouverture
#: de session, l'espace de travail de l'espace adhérent — le routage se fait
#: après authentification, jamais par un choix offert avant. Un rôle créé par le
#: cabinet n'y figure pas, mais il est interne : voir `est_interne`.
ROLES_INTERNES: frozenset[Role] = frozenset(Role) - ROLES_EXTERNES


# ── La matrice réglable (18 octobre 2026) ────────────────────────────────────────

#: Les droits qu'un rôle externe peut recevoir : ceux que la matrice livrée donne déjà
#: aux trois rôles externes, et aucun autre.
PERMISSIONS_EXTERNES: frozenset[Permission] = frozenset().union(
    *(PERMISSIONS_PAR_ROLE[r] for r in ROLES_EXTERNES)
)

#: Les gestes du client sur SON dossier. Un rôle interne ne les reçoit pas : voir l'en-tête.
PERMISSIONS_DU_CLIENT: frozenset[Permission] = frozenset(
    {
        Permission.REPONDRE_AU_CABINET,
        Permission.SIGNALER_UN_CHANGEMENT,
        Permission.REGLER_SES_RAPPELS,
        Permission.TENIR_SA_PAIE,
        Permission.SUIVRE_MA_CREATION,
        Permission.REGLER_SES_HONORAIRES,
        Permission.CONFIER_SES_FONDS,
    }
)

#: Le travail du cabinet sur un dossier (18 octobre 2026) : ce qui s'arrête quand
#: l'adhérent n'est pas en règle. Ni la lecture, ni le dépôt de pièces, ni la relance,
#: ni l'encaissement : couper ces portes empêcherait de régulariser.
PERMISSIONS_DE_TRAITEMENT: frozenset[Permission] = frozenset(
    {
        Permission.IDENTIFIER_PIECE,
        Permission.ARBITRER_DOUBLON,
        Permission.CONTROLER_CONFORMITE,
        Permission.ECARTER_CONSTAT,
        Permission.SAISIR_ECRITURE,
        Permission.VALIDER_ECRITURE,
        Permission.CONTRE_PASSER,
        Permission.REVISER_DOSSIER,
        Permission.DEPOSER_DECLARATION,
        Permission.CLOTURER_EXERCICE,
        Permission.PREPARER_DSF,
        Permission.VALIDER_FICHE_DSF,
        Permission.TRAITER_MISSION,
    }
)

#: Au super administrateur seul, et il les garde.
PERMISSIONS_RESERVEES: frozenset[Permission] = frozenset(
    {Permission.GERER_ROLES, Permission.ACCORDER_FAVEURS}
)

_FORME_DU_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")


def _code_bien_forme(code: str) -> str:
    if not _FORME_DU_CODE.match(code):
        raise ValueError(
            f"« {code} » n'est pas un code de rôle : des majuscules, des chiffres et des "
            "soulignés, 3 à 40 caractères, une lettre en tête."
        )
    return code


def role_de(code: Role | str) -> Role | str:
    """Le rôle livré s'il en est un, sinon le code d'un rôle du cabinet."""
    try:
        return Role(code)
    except ValueError:
        return _code_bien_forme(str(code))


#: Un rôle porté par une habilitation : livré (`Role`) ou créé par le cabinet (son code).
#: ⚠️ `role_de` passe toujours : « COMPTABLE » doit devenir `Role.COMPTABLE`, jamais rester
#: une chaîne, sans quoi `h.role is Role.COMPTABLE` serait faux sur une ligne relue.
CodeRole = Annotated[Role | str, Field(union_mode="left_to_right"), AfterValidator(role_de)]


class FicheDeRole(BaseModel):
    """Un rôle tel que le cabinet l'a réglé : ses droits, et qui les a fixés."""

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str = Field(min_length=2, max_length=80)
    permissions: frozenset[Permission]
    #: Livré avec la plateforme : il se règle, il ne se supprime pas.
    systeme: bool
    externe: bool
    reglee_par: str | None = None
    reglee_le: datetime | None = None

    @model_validator(mode="after")
    def _bornes(self) -> FicheDeRole:
        _code_bien_forme(self.code)
        livre = self.code in Role.__members__
        if livre != self.systeme:
            raise ValueError(
                f"« {self.code} » est le code d'un rôle livré : un rôle du cabinet en prend "
                "un autre."
                if livre
                else f"« {self.code} » n'est pas un rôle livré."
            )
        if livre and self.externe != (Role(self.code) in ROLES_EXTERNES):
            raise ValueError(f"le rôle {self.code} ne change pas de nature (interne, externe).")
        if not livre and self.externe:
            raise ValueError("un rôle créé par le cabinet est un rôle de collaborateur.")
        if self.externe and not self.permissions <= PERMISSIONS_EXTERNES:
            interdits = ", ".join(sorted(self.permissions - PERMISSIONS_EXTERNES))
            raise ValueError(
                f"un rôle externe ne reçoit pas {interdits} : ce sont des droits du cabinet."
            )
        if not self.externe and self.permissions & PERMISSIONS_DU_CLIENT:
            interdits = ", ".join(sorted(self.permissions & PERMISSIONS_DU_CLIENT))
            raise ValueError(
                f"{interdits} : ce sont les gestes du client sur son dossier ; un "
                "collaborateur les exercerait sur tout le portefeuille."
            )
        reservees = self.permissions & PERMISSIONS_RESERVEES
        if self.code == Role.SUPER_ADMIN:
            if reservees != PERMISSIONS_RESERVEES:
                raise ValueError(
                    "le super administrateur garde GERER_ROLES et ACCORDER_FAVEURS : sans "
                    "eux, plus personne ne pourrait régler la matrice."
                )
        elif reservees:
            raise ValueError(
                f"{', '.join(sorted(reservees))} n'appartiennent qu'au super administrateur."
            )
        return self


def matrice_par_defaut() -> dict[str, FicheDeRole]:
    """La matrice livrée, en fiches : le point de départ de tout cabinet."""
    return {
        role.value: FicheDeRole(
            code=role.value,
            libelle=LIBELLES_ROLE[role],
            permissions=permissions,
            systeme=True,
            externe=role in ROLES_EXTERNES,
        )
        for role, permissions in PERMISSIONS_PAR_ROLE.items()
    }


_MATRICE_LIVREE = matrice_par_defaut()
#: Rend les fiches réglées par le cabinet servi (rôles livrés retouchés, rôles créés).
#: Posée une fois, au démarrage, par l'adaptateur : le domaine ne lit pas la base.
_source_des_roles: Callable[[], Iterable[FicheDeRole]] | None = None


def installer_la_source_des_roles(source: Callable[[], Iterable[FicheDeRole]] | None) -> None:
    global _source_des_roles
    _source_des_roles = source


def matrice_en_vigueur() -> Mapping[str, FicheDeRole]:
    """La matrice du cabinet servi : la livrée, recouverte de ce qu'il a réglé.

    ⚠️ Sans source posée (un script, un test de domaine), c'est la matrice livrée : la
    seule qui soit connue sans base, et le seul cas où elle s'emploie est celui où
    aucune requête n'est servie.
    """
    if _source_des_roles is None:
        return _MATRICE_LIVREE
    matrice = dict(_MATRICE_LIVREE)
    for fiche in _source_des_roles():
        matrice[fiche.code] = fiche
    return matrice


def est_interne(role: Role | str) -> bool:
    """Un collaborateur du cabinet. Un rôle créé par le cabinet l'est toujours."""
    return role not in ROLES_EXTERNES


def permissions_de(roles: Iterable[Role | str]) -> frozenset[Permission]:
    """L'union des permissions de plusieurs rôles, à la matrice en vigueur.

    Union et non intersection : une personne qui est à la fois comptable et
    chargée de clientèle cumule les deux métiers, elle ne les additionne pas pour
    n'en garder que la partie commune.

    18 octobre 2026 : lue à la matrice du cabinet. Un rôle qu'elle ne connaît plus (un
    rôle du cabinet retiré) ne donne rien.
    """
    matrice = matrice_en_vigueur()
    resultat: frozenset[Permission] = frozenset()
    for role in roles:
        fiche = matrice.get(str(role))
        if fiche is not None:
            resultat |= fiche.permissions
    return resultat
