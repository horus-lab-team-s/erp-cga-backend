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
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "EXIGE_MFA",
    "EXIGE_MOTIF",
    "LIBELLES_ROLE",
    "PERMISSIONS_PAR_ROLE",
    "ROLES_A_PORTEE_OBLIGATOIRE",
    "ROLES_INTERNES",
    "Permission",
    "Role",
    "libelle_role",
    "permissions_de",
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
}


def libelle_role(role: Role | str) -> str:
    """Le rôle en clair, ou sa valeur brute s'il est inconnu.

    Ne lève pas : un libellé manquant ne doit pas empêcher un courriel de partir
    ni un écran de s'afficher. Le pire est alors une valeur technique visible,
    qui se corrige ; une exception, elle, coûterait l'invitation.
    """
    try:
        return LIBELLES_ROLE[Role(role)]
    except ValueError:
        return str(role)


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


#: Les actions du tableau « actions sensibles » de 05-securite-multitenant.md qui
#: réclament une authentification forte. Le domaine se contente de le déclarer :
#: c'est la couche d'autorisation qui refuse, et l'adaptateur qui sait comment la
#: seconde preuve a été obtenue.
#:
#: ⚠️ Le second facteur n'est pas encore implémenté. La déclaration existe pour
#: que le jour où il l'est, il n'y ait rien à retrouver — et pour que
#: `autoriser()` puisse déjà refuser une session non renforcée.
EXIGE_MFA: frozenset[Permission] = frozenset({Permission.DEPOSER_DECLARATION})


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
    }
)


_LECTURE_DOSSIER: frozenset[Permission] = frozenset(
    {Permission.LIRE_DOSSIER, Permission.LIRE_PIECE}
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
    },
    # Voir l'en-tête : aucune permission comptable ou déclarative.
    Role.CAISSE: {Permission.ENCAISSER},
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
    },
    Role.INSPECTEUR: _LECTURE_DOSSIER
    | {Permission.LIRE_COMPTABILITE, Permission.LIRE_AUDIT, Permission.EMETTRE_AVIS},
}


#: Les rôles qui ne peuvent jamais porter sur l'ensemble du portefeuille.
ROLES_A_PORTEE_OBLIGATOIRE: frozenset[Role] = frozenset(
    {Role.ADHERENT, Role.INSPECTEUR, Role.CLIENT_CREATION}
)

#: Les rôles tenus par des salariés du cabinet. Sert à distinguer, à l'ouverture
#: de session, l'espace de travail de l'espace adhérent — le routage se fait
#: après authentification, jamais par un choix offert avant.
ROLES_INTERNES: frozenset[Role] = frozenset(Role) - frozenset(
    {Role.ADHERENT, Role.INSPECTEUR, Role.CLIENT_CREATION}
)


def permissions_de(roles: frozenset[Role]) -> frozenset[Permission]:
    """L'union des permissions de plusieurs rôles.

    Union et non intersection : une personne qui est à la fois comptable et
    chargée de clientèle cumule les deux métiers, elle ne les additionne pas pour
    n'en garder que la partie commune.
    """
    resultat: frozenset[Permission] = frozenset()
    for role in roles:
        resultat |= PERMISSIONS_PAR_ROLE[role]
    return resultat
