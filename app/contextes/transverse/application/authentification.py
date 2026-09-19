"""Ouvrir, vérifier et fermer une session.

─────────────────────────────────────────────────────────────────────────────────
UNE SEULE RÉPONSE POUR TOUS LES ÉCHECS

Compte inconnu, mot de passe faux, compte suspendu, compte jamais activé, compte
verrouillé : `IdentifiantsRefuses`, avec le même message.

Distinguer « cette adresse n'existe pas » de « mot de passe incorrect » offre un
**oracle d'énumération** : on essaie une liste d'adresses, on note lesquelles
répondent différemment, et l'on obtient la liste des collaborateurs et des
adhérents du cabinet. Chez un CGA, cette liste est elle-même une information
commerciale — savoir qui sont les clients d'un concurrent a une valeur.

La cause exacte part au journal d'audit, où elle sert au diagnostic sans rien
apprendre à celui qui essaie.

LE TEMPS DE RÉPONSE EST UN CANAL, LUI AUSSI

Si un compte inconnu renvoyait immédiatement et un mot de passe faux après les
deux cents millisecondes d'Argon2, la différence se mesurerait — et l'oracle
reviendrait par la porte de derrière. Une dérivation est donc exécutée même quand
le compte n'existe pas, contre une empreinte leurre.

LE VERROU EST UNE DATE, PAS UN DRAPEAU

Cinq échecs consécutifs verrouillent pour quinze minutes. Un booléen exigerait une
tâche de fond pour le lever, et le compte resterait bloqué si cette tâche tombait.
Une date se périme toute seule — voir `Compte.verrouille`.

CE QUE CE MODULE NE FAIT PAS

Il ne délivre aucun jeton de transport. La session est une entité ; la manière de
la présenter au navigateur — témoin de connexion, en-tête, jeton signé — est une
décision d'adaptateur, prise dans `adaptateurs/entrant/`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from functools import lru_cache

from app.contextes.transverse.domaine.identites import Compte, CompteInactif
from app.contextes.transverse.domaine.jetons import JetonInvalide, TypeJeton, empreinte_de
from app.contextes.transverse.domaine.ports import (
    DepotComptes,
    DepotHabilitations,
    DepotJetons,
    DepotSessions,
    JournalAudit,
    ServiceEmpreinte,
)
from app.contextes.transverse.domaine.second_facteur import (
    DUREE_RENFORCEMENT,
    CodeInvalide,
    verifier_code,
)
from app.contextes.transverse.domaine.sessions import (
    DUREE_SESSION,
    MotifRevocation,
    Session,
    SessionInvalide,
)

__all__ = [
    "DUREE_VERROU",
    "IdentifiantsRefuses",
    "SecondFacteurAbsent",
    "PreuveDeBoiteRequise",
    "SecondFacteurDejaActif",
    "confirmer_enrolement",
    "enroler_second_facteur",
    "renforcer_session",
    "fermer_session",
    "ouvrir_session",
    "revoquer_les_sessions",
    "verifier_session",
]

#: Quinze minutes. Assez pour rendre une attaque par dictionnaire inopérante —
#: cinq essais par quart d'heure —, assez peu pour qu'une comptable qui s'est
#: trompée aille prendre un café plutôt que d'appeler l'administrateur.
DUREE_VERROU = timedelta(minutes=15)

#: Message unique. Voir l'en-tête.
_REFUS = (
    "Identifiants refusés. Si le compte vient d'être créé, suivre d'abord le lien "
    "de définition du mot de passe reçu par courriel."
)


class IdentifiantsRefuses(RuntimeError):
    """La session ne s'ouvre pas. La cause n'est pas dite à l'appelant."""


class SecondFacteurAbsent(RuntimeError):
    """Le compte n'a pas encore enrôlé de second facteur.

    Distinct d'un code faux, et c'est ici légitime : la personne est déjà
    authentifiée, on ne lui apprend rien qu'elle ne sache sur son propre compte.
    Le message doit au contraire être précis, sinon elle cherchera un code
    qu'elle n'a nulle part.
    """


class PreuveDeBoiteRequise(RuntimeError):
    """Un premier enrôlement sans lien confirmé par courriel."""


class SecondFacteurDejaActif(RuntimeError):
    """Le compte a déjà un second facteur, et la session n'a pas prouvé le détenir."""


@lru_cache(maxsize=8)
def _empreinte_leurre(empreintes: ServiceEmpreinte) -> str:
    """Une empreinte sur laquelle perdre le même temps qu'une vraie.

    Calculée une fois par service, sur un secret jamais conservé : personne, pas
    même le code, ne connaît le mot de passe qu'elle scelle.
    """
    return empreintes.deriver(secrets.token_urlsafe(16))


def ouvrir_session(
    courriel: str,
    mot_de_passe: str,
    *,
    identifiant_session: str,
    comptes: DepotComptes,
    sessions: DepotSessions,
    empreintes: ServiceEmpreinte,
    journal: JournalAudit,
    a_l_instant: datetime,
    adresse_ip: str | None = None,
    agent: str | None = None,
) -> tuple[Session, Compte]:
    """Authentifie, puis ouvre une session révocable.

    Rend la session **et** le compte : l'appelant a besoin des deux, et une
    seconde lecture du dépôt rouvrirait une fenêtre où le compte aurait changé
    entre les deux appels.
    """
    compte = comptes.par_courriel(courriel.strip().lower())

    if compte is None:
        # Voir l'en-tête : on paie le prix d'une dérivation pour ne pas signer,
        # par la durée de la réponse, que l'adresse est inconnue.
        empreintes.verifier(mot_de_passe, _empreinte_leurre(empreintes))
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="anonyme",
            action="session.refusee",
            objet_type="courriel",
            objet_id=courriel.strip().lower(),
            apres={"cause": "compte inconnu"},
            adresse_ip=adresse_ip,
        )
        raise IdentifiantsRefuses(_REFUS)

    try:
        compte.exiger_ouverture(a_l_instant)
    except CompteInactif as empechement:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur=compte.identifiant,
            action="session.refusee",
            objet_type="compte",
            objet_id=compte.identifiant,
            apres={"cause": str(empechement)},
            adresse_ip=adresse_ip,
        )
        raise IdentifiantsRefuses(_REFUS) from empechement

    assert compte.empreinte_mot_de_passe is not None  # garanti par exiger_ouverture
    if not empreintes.verifier(mot_de_passe, compte.empreinte_mot_de_passe):
        echoue = compte.apres_echec(a_l_instant, DUREE_VERROU)
        comptes.enregistrer(echoue)
        journal.ajouter(
            horodatage=a_l_instant,
            acteur=compte.identifiant,
            action="session.refusee",
            objet_type="compte",
            objet_id=compte.identifiant,
            apres={
                "cause": "mot de passe incorrect",
                "tentatives": echoue.tentatives_echouees,
                "verrouille_jusqu_a": echoue.verrouille_jusqu_a,
            },
            adresse_ip=adresse_ip,
        )
        raise IdentifiantsRefuses(_REFUS)

    # Le mot de passe est bon : c'est le seul instant où il est connu en clair, et
    # donc le seul où l'on peut le redériver avec des paramètres durcis. Sans ce
    # rattrapage, un renforcement d'Argon2 ne profiterait qu'aux comptes créés
    # après le changement.
    if empreintes.a_rederiver(compte.empreinte_mot_de_passe):
        compte = compte.model_copy(
            update={"empreinte_mot_de_passe": empreintes.deriver(mot_de_passe)}
        )

    compte = compte.apres_succes(a_l_instant)
    comptes.enregistrer(compte)

    session = Session(
        identifiant=identifiant_session,
        compte=compte.identifiant,
        locataire=compte.locataire,
        ouverte_le=a_l_instant,
        expire_le=a_l_instant + DUREE_SESSION,
        adresse_ip=adresse_ip,
        agent=agent,
    )
    sessions.enregistrer(session)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=compte.identifiant,
        action="session.ouverte",
        objet_type="session",
        objet_id=session.identifiant,
        apres={"expire_le": session.expire_le},
        adresse_ip=adresse_ip,
    )
    return session, compte


def verifier_session(
    identifiant_session: str,
    *,
    comptes: DepotComptes,
    sessions: DepotSessions,
    habilitations: DepotHabilitations,
    a_l_instant: datetime,
) -> tuple[Session, Compte, list]:
    """Contrôle qu'une session vaut encore, et rend de quoi résoudre l'accès.

    Le compte est **relu à chaque fois**, et pas seulement la session : un compte
    suspendu à 10 h ne doit pas continuer à travailler jusqu'à l'expiration de sa
    session. C'est la contrepartie assumée d'une session côté serveur — une
    lecture de plus, et une porte qui se ferme réellement.
    """
    session = sessions.lire(identifiant_session)
    if session is None:
        raise SessionInvalide(f"session {identifiant_session} inconnue")
    session.exiger_active(a_l_instant)

    compte = comptes.lire(session.compte)
    try:
        compte.exiger_ouverture(a_l_instant)
    except CompteInactif as empechement:
        raise SessionInvalide(
            f"session {identifiant_session} valide mais compte indisponible : {empechement}"
        ) from empechement

    return session, compte, habilitations.pour_compte(compte.identifiant)


def fermer_session(
    identifiant_session: str,
    *,
    sessions: DepotSessions,
    journal: JournalAudit,
    a_l_instant: datetime,
    motif: MotifRevocation = MotifRevocation.DECONNEXION,
    par: str | None = None,
) -> None:
    """Révoque une session. Silencieux si elle n'existe pas ou est déjà fermée.

    Une déconnexion qui échoue parce que la session avait déjà expiré donnerait
    une erreur à quelqu'un qui a fait exactement ce qu'il fallait.
    """
    session = sessions.lire(identifiant_session)
    if session is None or session.revoquee:
        return
    fermee = session.revoquer(a_l_instant, motif)
    sessions.enregistrer(fermee)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par or session.compte,
        action="session.fermee",
        objet_type="session",
        objet_id=session.identifiant,
        apres={"motif": motif},
    )


def revoquer_les_sessions(
    compte: str,
    *,
    sessions: DepotSessions,
    journal: JournalAudit,
    a_l_instant: datetime,
    motif: MotifRevocation,
    par: str,
) -> int:
    """Ferme toutes les sessions actives d'un compte. Rend le nombre fermé.

    Appelé sur trois évènements, et ce sont les trois où l'oubli coûte cher : la
    suspension d'un compte, un changement de mot de passe, le départ d'un
    collaborateur. Un mot de passe changé qui laisserait vivre les sessions
    ouvertes ne servirait à rien contre celui qui les détient.
    """
    fermees = 0
    for session in sessions.pour_compte(compte):
        if session.active(a_l_instant):
            sessions.enregistrer(session.revoquer(a_l_instant, motif))
            fermees += 1
    if fermees:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur=par,
            action="session.revoquees",
            objet_type="compte",
            objet_id=compte,
            apres={"nombre": fermees, "motif": motif},
        )
    return fermees


def enroler_second_facteur(
    compte: Compte,
    *,
    secret: str,
    session_renforcee: bool,
    lien_confirme: bool = False,
    comptes: DepotComptes,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Rattache un secret TOTP au compte.

    ⚠️ Le secret n'entre **pas** au journal : `expurger` masque la clé `secret`,
    et on ne la lui donne pas non plus. Ce qui est enregistré est le fait de
    l'enrôlement, sa date et son auteur — c'est ce qu'un audit demande, et c'est
    tout ce qu'il peut demander sans devenir lui-même une fuite.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE RÉENRÔLEMENT EXIGE LE FACTEUR ACTUEL.** (pas 60)

    Ce commentaire disait : « le réenrôlement est permis et journalisé : ce qui
    protège, c'est que l'acte laisse une trace datée, pas qu'il soit impossible ».
    C'était une faille. Avec le seul mot de passe d'un réviseur, ou sa session
    volée, n'importe qui écrasait son secret, recevait le nouveau, renforçait la
    session et déposait des déclarations. **Le second facteur tombait devant le
    premier**, ce contre quoi il existe. La trace arrivait après les dégâts, et le
    titulaire perdait son facteur sans le savoir.

    Désormais :

        premier enrôlement   la session suffit ; le titulaire est prévenu par
                             courriel, pour qu'un enrôlement fait par un tiers se voie
        remplacement         la session doit être renforcée par un code de l'appareil
                             actuel : qui a le téléphone peut le remplacer
        téléphone perdu      un tiers habilité réinitialise, avec motif ; voir
                             `reinitialiser_second_facteur`
    ─────────────────────────────────────────────────────────────────────────────
    """
    # ⚠️ **Le premier enrôlement exige la boîte aux lettres** (pas 62). Le pas 60 l'avait
    # laissé à la session seule, et un mot de passe volé suffisait donc à enrôler le
    # compte d'un réviseur qui ne l'avait pas encore fait. Le défaut est le refus :
    # seul `confirmer_enrolement` passe `lien_confirme=True`.
    if not compte.second_facteur_actif and not lien_confirme:
        raise PreuveDeBoiteRequise(
            "le premier enrôlement se confirme par le lien envoyé à l'adresse du compte."
        )
    if compte.second_facteur_actif and not session_renforcee:
        raise SecondFacteurDejaActif(
            "un second facteur est déjà enrôlé sur ce compte. Le remplacer exige un code "
            "de l'appareil actuel : renforcer d'abord la session. Appareil perdu : "
            "demander au cabinet la réinitialisation du second facteur."
        )
    enrole = compte.avec_second_facteur(secret)
    comptes.enregistrer(enrole)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=compte.identifiant,
        action="compte.second_facteur_enrole",
        objet_type="compte",
        objet_id=compte.identifiant,
        avant={"second_facteur_actif": compte.second_facteur_actif},
        apres={"second_facteur_actif": True, "remplacement": compte.second_facteur_actif},
    )
    return enrole


def confirmer_enrolement(
    secret_du_lien: str,
    *,
    compte: Compte,
    secret_totp: str,
    comptes: DepotComptes,
    jetons: DepotJetons,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Compte:
    """Consomme le lien d'enrôlement et rattache le second facteur.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI UN LIEN (pas 62)

    Un premier enrôlement ne peut pas se prouver par l'appareil : il n'y en a pas
    encore. Il se prouvait par la session, donc par le mot de passe, et le pas 60 a
    écrit la limite : qui volait le mot de passe d'un réviseur jamais enrôlé enrôlait
    à sa place. La preuve devient **la boîte aux lettres du compte**. Un mot de passe
    seul ne suffit plus ; il faut aussi la messagerie.

    ⚠️ LE LIEN APPARTIENT AU COMPTE DE LA SESSION, ET À LUI SEUL

    Le jeton d'un autre compte est refusé avec le message commun des liens : sans
    cela, un lien intercepté servirait à qui est connecté, et le refus dirait à qui
    il appartenait.

    ⚠️ LE JETON EST CONSOMMÉ AVANT L'ENRÔLEMENT

    Un second clic sur le même lien ne rend pas une seconde clé.
    ─────────────────────────────────────────────────────────────────────────────
    """
    jeton = jetons.par_empreinte(empreinte_de(secret_du_lien))
    if jeton is None:
        raise JetonInvalide("empreinte inconnue")
    if jeton.type is not TypeJeton.ENROLEMENT:
        raise JetonInvalide(f"jeton {jeton.identifiant} de type {jeton.type} : pas un enrôlement")
    if jeton.compte != compte.identifiant:
        raise JetonInvalide(
            f"jeton {jeton.identifiant} du compte {jeton.compte} présenté par {compte.identifiant}"
        )
    jetons.enregistrer(jeton.consommer(a_l_instant))
    return enroler_second_facteur(
        compte,
        secret=secret_totp,
        session_renforcee=False,
        lien_confirme=True,
        comptes=comptes,
        journal=journal,
        a_l_instant=a_l_instant,
    )


def renforcer_session(
    session: Session,
    compte: Compte,
    code: str,
    *,
    sessions: DepotSessions,
    journal: JournalAudit,
    a_l_instant: datetime,
    duree: timedelta = DUREE_RENFORCEMENT,
) -> Session:
    """Élève la session après présentation d'un code valide.

    Lève `SecondFacteurAbsent` si rien n'est enrôlé, `CodeInvalide` si le code ne
    vaut pas. Les deux échecs sont journalisés : une série de codes refusés sur
    un compte est le signal d'un second facteur compromis, et c'est exactement ce
    qu'un journal d'audit sert à voir.
    """
    if compte.secret_totp is None:
        raise SecondFacteurAbsent(
            f"le compte {compte.identifiant} n'a pas de second facteur. L'enrôler "
            "depuis son profil avant de tenter une action sensible."
        )
    if not verifier_code(compte.secret_totp, code, a_l_instant):
        journal.ajouter(
            horodatage=a_l_instant,
            acteur=compte.identifiant,
            action="session.renforcement_refuse",
            objet_type="session",
            objet_id=session.identifiant,
        )
        raise CodeInvalide(
            "Code refusé. Vérifier que l'heure du téléphone est à l'heure : un "
            "décalage de plus d'une minute suffit à invalider tous les codes."
        )

    renforcee = session.renforcer(a_l_instant, duree)
    sessions.enregistrer(renforcee)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=compte.identifiant,
        action="session.renforcee",
        objet_type="session",
        objet_id=session.identifiant,
        apres={"renforcee_jusqu_a": renforcee.renforcee_jusqu_a},
    )
    return renforcee
