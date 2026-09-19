"""Créer les comptes, distribuer les rôles, affecter les dossiers.

─────────────────────────────────────────────────────────────────────────────────
L'AUTORISATION EST DANS LE CAS D'USAGE, PAS DANS LA ROUTE

Chaque fonction de ce module réclame un `Acces` et vérifie elle-même la permission
qu'elle exige. Placer ce contrôle dans le décorateur d'une route HTTP serait plus
court, et faux : le jour où l'on ajoute une commande en ligne pour reprendre un
portefeuille, un traitement de reprise de données, ou une seconde interface, tout
passerait à côté. Une règle d'accès posée à la porte ne protège que cette porte.

Le coût est un paramètre de plus dans chaque signature. Le bénéfice est qu'aucun
appelant, présent ou futur, ne peut créer un compte sans droit.

DEUX PORTES D'ENTRÉE, DEUX INTENTIONS

**Inviter un collaborateur** est un acte d'administrateur : il connaît la personne,
choisit son rôle, et le lien vit quatorze jours.

**Ouvrir l'accès d'un adhérent** est déclenché par le système, à la validation d'un
paiement. L'acteur est `systeme`, le rôle est imposé, la portée est le dossier
souscrit — jamais autre chose — et le lien vit sept jours.

Les confondre en une fonction « créer un utilisateur avec un rôle » ferait
disparaître cette différence, et rendrait possible la création par voie de
souscription d'un compte au rôle de réviseur.

CE QUI N'EXISTE PAS ICI

Ni `supprimer_compte`, ni `retirer_role`. Un compte se suspend, une habilitation se
ferme. Voir `ports.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.contextes.transverse.application.activation import emettre_jeton
from app.contextes.transverse.application.autorisation import Acces, AccesRefuse
from app.contextes.transverse.domaine.habilitations import Habilitation, MotifHabilitation
from app.contextes.transverse.domaine.identites import Compte, EtatCompte
from app.contextes.transverse.domaine.jetons import Jeton, TypeJeton
from app.contextes.transverse.domaine.ports import (
    DepotComptes,
    DepotHabilitations,
    DepotJetons,
    DepotSessions,
    JournalAudit,
)
from app.contextes.transverse.domaine.roles import Permission, Role
from app.contextes.transverse.domaine.sessions import MotifRevocation

__all__ = [
    "CourrielDejaPris",
    "ReinitialisationRefusee",
    "RetablissementRefuse",
    "SuspensionRefusee",
    "reinitialiser_second_facteur",
    "FermetureRefusee",
    "ReaffectationRefusee",
    "affecter_dossier",
    "reaffecter_dossier",
    "fermer_habilitation",
    "inviter_collaborateur",
    "ouvrir_acces_adherent",
    "retablir_compte",
    "suspendre_compte",
]

#: L'acteur des actes déclenchés sans humain : webhook de paiement, cron,
#: reprise de données. Nommé plutôt que laissé vide — une action sans auteur est
#: une action que personne n'assume, et le journal d'audit refuse un acteur vide.
SYSTEME = "systeme"


class CourrielDejaPris(ValueError):
    """Une adresse ne peut servir qu'à un compte.

    C'est l'identifiant de connexion : deux porteurs rendraient l'un des deux
    inaccessible, et le second créé « volerait » l'accès du premier.
    """


def _verifier_adresse_libre(courriel: str, comptes: DepotComptes) -> None:
    if comptes.par_courriel(courriel.strip().lower()) is not None:
        raise CourrielDejaPris(
            f"l'adresse {courriel} est déjà celle d'un compte. Si la personne a perdu "
            "son mot de passe, lui envoyer un lien de réinitialisation plutôt que de "
            "créer un second compte."
        )


def inviter_collaborateur(
    *,
    par: Acces,
    identifiant_compte: str,
    identifiant_habilitation: str,
    identifiant_jeton: str,
    courriel: str,
    nom: str,
    prenom: str,
    role: Role,
    portee: frozenset[str] | None,
    depuis: date,
    comptes: DepotComptes,
    habilitations: DepotHabilitations,
    jetons: DepotJetons,
    journal: JournalAudit,
    a_l_instant: datetime,
    telephone: str | None = None,
    precision: str | None = None,
) -> tuple[Compte, Jeton, str]:
    """Crée un compte de cabinet, son habilitation, et son lien d'invitation.

    Rend le secret en clair en troisième position : c'est la seule fois où il
    existe, et l'appelant doit le placer dans le courriel sans le conserver.
    """
    par.exiger(Permission.GERER_COMPTES)
    _verifier_adresse_libre(courriel, comptes)

    compte = Compte(
        identifiant=identifiant_compte,
        courriel=courriel,
        nom=nom,
        prenom=prenom,
        locataire=par.locataire,
        telephone=telephone,
        etat=EtatCompte.EN_ATTENTE_ACTIVATION,
        cree_le=a_l_instant,
    )
    comptes.enregistrer(compte)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="compte.cree",
        objet_type="compte",
        objet_id=compte.identifiant,
        apres={"courriel": compte.courriel, "role_initial": role},
    )

    habilitation = Habilitation(
        identifiant=identifiant_habilitation,
        compte=compte.identifiant,
        role=role,
        portee=portee,
        debut=depuis,
        motif=MotifHabilitation.RECRUTEMENT,
        accordee_par=par.compte,
        precision=precision,
    )
    _enregistrer_habilitation(habilitation, habilitations, journal, par.compte, a_l_instant)

    jeton, secret = emettre_jeton(
        compte,
        TypeJeton.INVITATION,
        identifiant_jeton=identifiant_jeton,
        jetons=jetons,
        journal=journal,
        a_l_instant=a_l_instant,
        emis_par=par.compte,
    )
    return compte, jeton, secret


def ouvrir_acces_adherent(
    *,
    identifiant_compte: str,
    identifiant_habilitation: str,
    identifiant_jeton: str,
    courriel: str,
    nom: str,
    prenom: str,
    niu: str,
    locataire: str,
    depuis: date,
    comptes: DepotComptes,
    habilitations: DepotHabilitations,
    jetons: DepotJetons,
    journal: JournalAudit,
    a_l_instant: datetime,
    telephone: str | None = None,
    reference_souscription: str | None = None,
    duree_lien: timedelta | None = None,
) -> tuple[Compte, Jeton, str]:
    """Ouvre l'accès d'un adhérent à son dossier, à la suite d'une souscription.

    Ne prend **aucun** `Acces` : l'acte est déclenché par le système à la
    validation d'un paiement, il n'y a pas d'humain à autoriser. Ce qui le rend
    sûr est qu'il ne comporte aucun degré de liberté — le rôle est `ADHERENT`, la
    portée est le seul NIU souscrit, et rien de tout cela n'est paramétrable.

    ⚠️ L'appelant est responsable de n'invoquer cette fonction que sur un paiement
    réellement validé. C'est le contexte de souscription qui porte cette garantie,
    pas celui-ci.
    """
    _verifier_adresse_libre(courriel, comptes)

    compte = Compte(
        identifiant=identifiant_compte,
        courriel=courriel,
        nom=nom,
        prenom=prenom,
        locataire=locataire,
        telephone=telephone,
        etat=EtatCompte.EN_ATTENTE_ACTIVATION,
        cree_le=a_l_instant,
    )
    comptes.enregistrer(compte)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=SYSTEME,
        action="compte.cree",
        objet_type="compte",
        objet_id=compte.identifiant,
        apres={
            "courriel": compte.courriel,
            "niu": niu,
            "souscription": reference_souscription,
        },
    )

    habilitation = Habilitation(
        identifiant=identifiant_habilitation,
        compte=compte.identifiant,
        # Le rôle et la portée sont imposés — voir la docstring.
        role=Role.ADHERENT,
        portee=frozenset({niu}),
        debut=depuis,
        motif=MotifHabilitation.SOUSCRIPTION,
        accordee_par=SYSTEME,
        precision=reference_souscription,
    )
    _enregistrer_habilitation(habilitation, habilitations, journal, SYSTEME, a_l_instant)

    jeton, secret = emettre_jeton(
        compte,
        TypeJeton.ACTIVATION,
        identifiant_jeton=identifiant_jeton,
        jetons=jetons,
        journal=journal,
        a_l_instant=a_l_instant,
        emis_par=SYSTEME,
        duree=duree_lien,
    )
    return compte, jeton, secret


def accorder(
    *,
    par: Acces,
    identifiant: str,
    compte: str,
    role: Role,
    portee: frozenset[str] | None,
    depuis: date,
    motif: MotifHabilitation,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    a_l_instant: datetime,
    precision: str | None = None,
) -> Habilitation:
    """Accorde un rôle à un compte existant.

    Un administrateur ne peut accorder que dans les limites de son propre
    périmètre : celui qui ne voit que trois dossiers ne peut pas nommer un
    comptable sur l'ensemble du cabinet. Sans cette borne, la permission
    `GERER_COMPTES` deviendrait un chemin d'élévation universel.
    """
    par.exiger(Permission.GERER_COMPTES)

    if portee is None and not par.transverse:
        raise AccesRefuse(
            f"{par.compte} ne couvre que {len(par.dossiers or [])} dossier(s) et ne peut "
            "pas accorder une habilitation transverse."
        )
    if portee is not None:
        hors = sorted(niu for niu in portee if not par.voit(niu))
        if hors:
            raise AccesRefuse(
                f"{par.compte} ne couvre pas {', '.join(hors)} et ne peut pas les "
                "accorder à un autre."
            )

    habilitation = Habilitation(
        identifiant=identifiant,
        compte=compte,
        role=role,
        portee=portee,
        debut=depuis,
        motif=motif,
        accordee_par=par.compte,
        precision=precision,
    )
    _enregistrer_habilitation(habilitation, habilitations, journal, par.compte, a_l_instant)
    return habilitation


def fermer_habilitation(
    *,
    par: Acces,
    identifiant: str,
    le: date,
    motif: MotifHabilitation,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Habilitation:
    """Ferme une habilitation à une date. Ne la supprime pas."""
    par.exiger(Permission.GERER_COMPTES)
    avant = habilitations.lire(identifiant)
    # ⚠️ Pas 70 : comme la suspension (pas 69), on ne ferme pas sa propre habilitation.
    # Un administrateur qui fermerait la sienne perdrait GERER_COMPTES à la date dite,
    # et un administrateur seul laisserait le cabinet sans personne pour les comptes.
    if avant.compte == par.compte:
        raise FermetureRefusee(
            "on ne ferme pas sa propre habilitation : demander à un autre administrateur."
        )
    apres = avant.fermer(le, motif=motif, par=par.compte)
    habilitations.enregistrer(apres)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="habilitation.fermee",
        objet_type="habilitation",
        objet_id=identifiant,
        avant={"role": avant.role, "fin": avant.fin},
        apres={"role": apres.role, "fin": apres.fin, "motif": motif},
    )
    return apres


def affecter_dossier(
    *,
    par: Acces,
    identifiant_habilitation: str,
    identifiant_successeur: str,
    niu: str,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Habilitation:
    """Confie un dossier à une habilitation, à compter d'aujourd'hui.

    C'est l'allocation de portefeuille : un dossier confié à un comptable. La
    permission est `AFFECTER_DOSSIER`, distincte de `GERER_COMPTES` : la direction
    répartit les dossiers, l'administrateur crée les comptes, et ce ne sont pas
    les mêmes personnes.

    ⚠️ **Le passé n'est pas réécrit** (pas 70) : voir `Habilitation.affecter`. Une
    habilitation qui a déjà couru est relayée par une successeur ouverte aujourd'hui,
    dont on rend l'identifiant. `identifiant_successeur` est donc exigé à chaque
    appel, sans défaut, même quand il ne sert pas : l'appelant ne peut pas savoir
    d'avance lequel des deux cas s'applique.

    On rend **l'habilitation active après l'affectation** : la successeur, ou
    l'habilitation étendue sur place.
    """
    par.exiger(Permission.AFFECTER_DOSSIER, dossier=niu)
    avant = habilitations.lire(identifiant_habilitation)
    enregistrees = avant.affecter(
        niu,
        le=a_l_instant.date(),
        identifiant_successeur=identifiant_successeur,
        par=par.compte,
    )
    for habilitation in enregistrees:
        habilitations.enregistrer(habilitation)
    active = enregistrees[-1]
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="dossier.affecte",
        objet_type="habilitation",
        objet_id=identifiant_habilitation,
        avant={"portee": sorted(avant.portee or []), "fin": avant.fin},
        apres={
            "portee": sorted(active.portee or []),
            "niu": niu,
            # Pas 94 : à qui le dossier est confié. L'identifiant d'habilitation ne le
            # disait qu'à qui relisait l'habilitation ; la notification le lit ici.
            "compte": active.compte,
            "habilitation_active": active.identifiant,
            "depuis": active.debut if len(enregistrees) == 2 else None,
        },
    )
    return active


class ReaffectationRefusee(ValueError):
    """La réaffectation demandée n'a pas de sens (pas 105). Le message dit pourquoi."""


def reaffecter_dossier(
    *,
    par: Acces,
    niu: str,
    de: str,
    vers: str,
    successeur_de: str,
    successeur_vers: str,
    motif: str,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> tuple[Habilitation, Habilitation]:
    """Fait passer un dossier d'une habilitation à une autre, à compter d'aujourd'hui (pas 105).

    ─────────────────────────────────────────────────────────────────────────
    UN SEUL GESTE, TROIS FAITS AU JOURNAL

    Réaffecter, c'est **retirer** le dossier à l'un et le **confier** à l'autre, le même jour.
    Deux gestes séparés laisseraient, entre les deux, un dossier suivi par personne ou par
    deux. Le journal porte trois entrées, parce que trois personnes doivent l'apprendre et
    qu'une entrée n'est annoncée qu'à un seul public :

        dossier.retire     → le collaborateur qui cède le dossier
        dossier.affecte    → celui qui le reçoit (l'abonnement du pas 94)
        dossier.reaffecte  → le chargé de clientèle du dossier, avec le motif de la direction

    L'adhérent n'est pas prévenu : la maquette le précise, c'est une organisation interne.

    ⚠️ LE MÊME RÔLE, DEUX PERSONNES DIFFÉRENTES

    Confier le dossier d'un comptable à un chargé de clientèle ne réaffecte rien : cela retire
    la tenue du dossier à tout le monde. Et « réaffecter » à soi-même ne change rien.
    ─────────────────────────────────────────────────────────────────────────
    """
    par.exiger(Permission.AFFECTER_DOSSIER, dossier=niu)
    if len(motif.strip()) < 10:
        raise ReaffectationRefusee(
            "le motif compte au moins 10 caractères : il dit au chargé de clientèle pourquoi "
            "l'interlocuteur change."
        )
    source = habilitations.lire(de)
    cible = habilitations.lire(vers)
    if source.compte == cible.compte:
        raise ReaffectationRefusee(
            "le dossier passerait d'une habilitation à une autre du même collaborateur."
        )
    if source.role is not cible.role:
        raise ReaffectationRefusee(
            f"{source.role.value} vers {cible.role.value} : une réaffectation se fait entre "
            "collaborateurs du même rôle, sinon personne ne tient plus le dossier."
        )
    le = a_l_instant.date()
    try:
        retirees = source.retirer(niu, le=le, identifiant_successeur=successeur_de, par=par.compte)
        confiees = cible.affecter(
            niu, le=le, identifiant_successeur=successeur_vers, par=par.compte
        )
    except ValueError as erreur:
        raise ReaffectationRefusee(str(erreur)) from erreur
    for habilitation in (*retirees, *confiees):
        habilitations.enregistrer(habilitation)
    source_active, cible_active = retirees[-1], confiees[-1]
    commun = {"niu": niu, "motif_de_direction": motif.strip()}
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="dossier.retire",
        objet_type="habilitation",
        objet_id=de,
        avant={"portee": sorted(source.portee or [])},
        apres={
            **commun,
            "portee": sorted(source_active.portee or []),
            "compte": source_active.compte,
            "compte_suivant": cible_active.compte,
            "habilitation_active": source_active.identifiant,
        },
        motif=motif.strip(),
    )
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="dossier.affecte",
        objet_type="habilitation",
        objet_id=vers,
        avant={"portee": sorted(cible.portee or []), "fin": cible.fin},
        apres={
            "portee": sorted(cible_active.portee or []),
            "niu": niu,
            "compte": cible_active.compte,
            "habilitation_active": cible_active.identifiant,
            "depuis": cible_active.debut if len(confiees) == 2 else None,
        },
        motif=motif.strip(),
    )
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="dossier.reaffecte",
        objet_type="dossier",
        objet_id=niu,
        apres={
            "dossier": niu,
            "compte_precedent": source_active.compte,
            "compte": cible_active.compte,
            "role": cible_active.role.value,
        },
        motif=motif.strip(),
    )
    return source_active, cible_active


def suspendre_compte(
    *,
    par: Acces,
    identifiant: str,
    motif: str,
    comptes: DepotComptes,
    sessions: DepotSessions,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Coupe l'accès immédiatement, sessions ouvertes comprises.

    C'est la raison pour laquelle les sessions vivent côté serveur : sans dépôt à
    révoquer, la suspension ne prendrait effet qu'à l'expiration des jetons déjà
    remis, et « couper l'accès » voudrait dire « d'ici ce soir ».
    """
    par.exiger(Permission.GERER_COMPTES)
    # ⚠️ **On ne se suspend pas soi-même** (pas 69). Le geste ferme toutes les sessions
    # du compte : un administrateur seul qui se suspendait laissait le cabinet sans
    # personne pour gérer les comptes, et rien ne l'en empêchait. La réinitialisation
    # du second facteur portait déjà la même garde.
    if identifiant == par.compte:
        raise SuspensionRefusee(
            "on ne suspend pas son propre compte : le geste ferme vos sessions, et un "
            "administrateur seul laisserait le cabinet sans personne pour gérer les comptes. "
            "Demander à un autre administrateur."
        )
    avant = comptes.lire(identifiant)
    apres = avant.suspendre()
    comptes.enregistrer(apres)

    fermees = 0
    for session in sessions.pour_compte(identifiant):
        if session.active(a_l_instant):
            sessions.enregistrer(
                session.revoquer(a_l_instant, MotifRevocation.SUSPENSION_DU_COMPTE)
            )
            fermees += 1

    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="compte.suspendu",
        objet_type="compte",
        objet_id=identifiant,
        avant={"etat": avant.etat},
        apres={"etat": apres.etat, "sessions_fermees": fermees},
        motif=motif,
    )
    return apres


def retablir_compte(
    *,
    par: Acces,
    identifiant: str,
    motif: str,
    comptes: DepotComptes,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Lève la suspension. Le compte retrouve l'état où son mot de passe le place :
    actif s'il en a un, en attente d'activation sinon.

    ⚠️ PAS 91 : SEUL UN COMPTE SUSPENDU SE RÉTABLIT

    Rétablir un compte actif était accepté, et écrivait « compte.retabli » au journal
    d'audit : une levée de suspension qui n'avait levé aucune suspension. Le journal est
    la pièce qu'on produit à un contrôle ; une ligne fausse y vaut pire qu'une absence.

    Ce qui n'est **pas** rétabli, et c'est voulu : les habilitations. Un collaborateur
    parti a les siennes fermées à sa date de départ ; rétablir son compte ne lui rouvre
    aucun dossier. Il faut les lui accorder de nouveau, datées.
    """
    par.exiger(Permission.GERER_COMPTES)
    avant = comptes.lire(identifiant)
    if avant.etat is not EtatCompte.SUSPENDU:
        raise RetablissementRefuse(
            f"le compte {identifiant} n'est pas suspendu (état {avant.etat}) : "
            "il n'y a rien à rétablir."
        )
    apres = avant.retablir()
    comptes.enregistrer(apres)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="compte.retabli",
        objet_type="compte",
        objet_id=identifiant,
        avant={"etat": avant.etat},
        apres={"etat": apres.etat},
        motif=motif,
    )
    return apres


def _enregistrer_habilitation(
    habilitation: Habilitation,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    acteur: str,
    a_l_instant: datetime,
) -> None:
    habilitations.enregistrer(habilitation)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=acteur,
        action="habilitation.accordee",
        objet_type="habilitation",
        objet_id=habilitation.identifiant,
        apres={
            "compte": habilitation.compte,
            "role": habilitation.role,
            "portee": None if habilitation.portee is None else sorted(habilitation.portee),
            "debut": habilitation.debut,
        },
    )


class FermetureRefusee(RuntimeError):
    """Une habilitation que son auteur ne peut pas fermer : la sienne (pas 70)."""


class RetablissementRefuse(RuntimeError):
    """On ne rétablit qu'un compte suspendu (pas 91)."""


class SuspensionRefusee(RuntimeError):
    """La suspension demandée ne peut pas se faire."""


class ReinitialisationRefusee(RuntimeError):
    """La réinitialisation du second facteur ne peut pas se faire."""


def reinitialiser_second_facteur(
    *,
    par: Acces,
    identifiant: str,
    motif: str,
    comptes: DepotComptes,
    sessions: DepotSessions,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Retire le second facteur d'un compte dont le titulaire a perdu l'appareil.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI UN TIERS, ET POURQUOI PAS SOI-MÊME (pas 60)

    Le remplacement par le titulaire exige désormais un code de l'appareil actuel.
    Un téléphone perdu ne peut donc plus se remplacer seul, et c'est voulu : sinon,
    le mot de passe suffirait de nouveau. La réinitialisation passe par une autre
    personne, qui engage son nom et un motif.

    ⚠️ **Jamais sur son propre compte.** Un administrateur dont le mot de passe a fui
    retirerait sinon son propre facteur, puis en enrôlerait un nouveau.

    ⚠️ **Toutes les sessions du titulaire sont fermées.** Un appareil perdu peut être
    entre d'autres mains, et une session renforcée ouverte dessus déposerait encore
    pendant quinze minutes. Le titulaire se reconnecte, enrôle un nouvel appareil, et
    il est prévenu par courriel, par la route.

    Ce que ce geste ne fait pas : enrôler à la place du titulaire. Qui détiendrait le
    secret d'un autre détiendrait son second facteur.
    ─────────────────────────────────────────────────────────────────────────────
    """
    par.exiger(Permission.GERER_COMPTES)
    if identifiant == par.compte:
        raise ReinitialisationRefusee(
            "on ne réinitialise pas son propre second facteur : un mot de passe qui a fui "
            "suffirait alors à le retirer. Demander à un autre administrateur."
        )
    avant = comptes.lire(identifiant)
    if not avant.second_facteur_actif:
        raise ReinitialisationRefusee(
            f"le compte {identifiant} n'a aucun second facteur enrôlé : rien à réinitialiser."
        )
    apres = avant.sans_second_facteur()
    comptes.enregistrer(apres)

    fermees = 0
    for session in sessions.pour_compte(identifiant):
        if session.active(a_l_instant):
            sessions.enregistrer(
                session.revoquer(a_l_instant, MotifRevocation.REVOCATION_ADMINISTRATIVE)
            )
            fermees += 1

    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="compte.second_facteur_reinitialise",
        objet_type="compte",
        objet_id=identifiant,
        avant={"second_facteur_actif": True},
        apres={"second_facteur_actif": False, "sessions_fermees": fermees},
        motif=motif,
    )
    return apres
