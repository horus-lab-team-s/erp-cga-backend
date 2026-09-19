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

Ces trois-là doivent obligatoirement porter une portée explicite : un adhérent
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
    """Les neuf rôles de `Docs/architecture/05-securite-multitenant.md` § 2."""

    CHARGE_CLIENTELE = "CHARGE_CLIENTELE"
    COMPTABLE = "COMPTABLE"
    REVISEUR = "REVISEUR"
    FISCALISTE = "FISCALISTE"
    CHARGE_FORMALITES = "CHARGE_FORMALITES"
    DIRECTION = "DIRECTION"
    ADMINISTRATEUR = "ADMINISTRATEUR"
    #: Externe — le dirigeant de l'entreprise suivie.
    ADHERENT = "ADHERENT"
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
    Role.ADHERENT: "adhérent",
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

    # ── Formalités et administration ─────────────────────────────────────────
    SUIVRE_FORMALITE = "SUIVRE_FORMALITE"
    GERER_COMPTES = "GERER_COMPTES"
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
        Permission.REGLER_SES_RAPPELS,
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
    },
    # Le réviseur reprend tout ce que fait le comptable, et détient seul les
    # trois actes qui engagent le cabinet vis-à-vis de l'administration.
    Role.REVISEUR: _LECTURE_DOSSIER
    | {
        Permission.LIRE_COMPTABILITE,
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
        Permission.EDITER_VITRINE,
        Permission.LIRE_PROSPECT,
        Permission.QUALIFIER_PROSPECT,
    },
    # Voir l'en-tête : aucune permission comptable ou déclarative.
    Role.ADMINISTRATEUR: {
        Permission.GERER_COMPTES,
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
ROLES_A_PORTEE_OBLIGATOIRE: frozenset[Role] = frozenset({Role.ADHERENT, Role.INSPECTEUR})

#: Les rôles tenus par des salariés du cabinet. Sert à distinguer, à l'ouverture
#: de session, l'espace de travail de l'espace adhérent — le routage se fait
#: après authentification, jamais par un choix offert avant.
ROLES_INTERNES: frozenset[Role] = frozenset(Role) - frozenset(
    {Role.ADHERENT, Role.INSPECTEUR}
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
