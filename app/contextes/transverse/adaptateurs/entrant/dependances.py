"""Le point de passage : transformer une requête HTTP en `Acces` résolu.

─────────────────────────────────────────────────────────────────────────────────
LE JETON DE SESSION VOYAGE DANS UN TÉMOIN, PAS DANS `localStorage`

Un identifiant de session rangé dans `localStorage` est lisible par tout script
qui s'exécute dans la page. Une seule faille d'injection — une dépendance
compromise, un contenu éditorial mal échappé — et le jeton part. Un témoin
`HttpOnly` n'est pas lisible par JavaScript : la même faille ne permet plus de
l'exfiltrer.

Le témoin est également `SameSite=Lax`, ce qui écarte la falsification de requête
inter-site sur les méthodes qui écrivent, sans casser la navigation ordinaire.
L'API et le site partagent le même domaine enregistrable en production comme en
développement — `api.cga-brcg.cm` et `www.cga-brcg.cm`, `localhost:8000` et
`localhost:3000` —, le témoin circule donc normalement.

L'en-tête `Authorization: Bearer` est accepté **en second**, pour les tests et
l'outillage en ligne de commande. Il ne dispense de rien : c'est le même
identifiant de session, vérifié de la même manière.

CE QUI EST RÉSOLU À CHAQUE REQUÊTE

La session est relue, le compte est relu, les habilitations sont relues et
résolues à la date du jour. Trois lectures par requête, et c'est le prix assumé
d'un système où suspendre un compte prend effet immédiatement — voir l'en-tête de
`sessions.py`.

CE QUE LE REFUS DIT, ET CE QU'IL TAIT

`401` quand il n'y a pas de session valide, `403` quand il y en a une mais que
l'acte n'est pas permis. Le détail interne de `AccesRefuse` — quelle permission
manque, quel dossier n'est pas couvert — reste au journal : le dire à l'appelant
lui apprendrait l'existence de dossiers qu'il n'a pas à connaître.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Annotated, TypeVar

from fastapi import Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as SessionSql
from starlette.middleware.base import BaseHTTPMiddleware

from app.contextes.tenants.api import (
    DepotTenantsSql,
    RepertoireEnMemoire,
    Verdict,
    slug_depuis_hote,
    verdict_pour,
)
from app.contextes.transverse.adaptateurs.entrant.jeton import lire as lire_le_jeton
from app.contextes.transverse.adaptateurs.sortant.coffre import Coffre, coffre_depuis
from app.contextes.transverse.adaptateurs.sortant.depots_memoire import (
    CompteIntrouvable,
    DepotMandatsMemoire,
)
from app.contextes.transverse.adaptateurs.sortant.depots_sql import (
    DepotComptesSql,
    DepotHabilitationsSql,
    DepotJetonsSql,
    DepotMandatsSql,
    DepotSessionsSql,
    JournalAuditSql,
    PortailSql,
)
from app.contextes.transverse.adaptateurs.sortant.donnees_demo import depots_demo
from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
    ServiceEmpreinteArgon2,
)
from app.contextes.transverse.adaptateurs.sortant.notifications_memoire import (
    ServiceNotificationMemoire,
)
from app.contextes.transverse.adaptateurs.sortant.notifications_smtp import (
    ServiceNotificationSmtp,
    TransportSmtp,
)
from app.contextes.transverse.adaptateurs.sortant.portail_manuel import PortailManuel
from app.contextes.transverse.application.authentification import verifier_session
from app.contextes.transverse.application.autorisation import (
    Acces,
    AccesRefuse,
    resoudre_acces,
)
from app.contextes.transverse.domaine.mandats import mandat_applicable
from app.contextes.transverse.domaine.ports import (
    DepotComptes,
    DepotHabilitations,
    DepotJetons,
    DepotMandats,
    DepotSessions,
    JournalAudit,
    ServiceNotification,
)
from app.contextes.transverse.domaine.roles import Permission, Role, permissions_de
from app.contextes.transverse.domaine.sessions import SessionInvalide
from app.infrastructure.base_de_donnees import session_du_locataire
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant
from app.partage.locataire import courant, etabli, poser_le_mandat

__all__ = [
    "NOM_TEMOIN",
    "AccesRequis",
    "Atelier",
    "AtelierMemoire",
    "AtelierSql",
    "IntergicielUniteDeTravail",
    "acces_courant",
    "acces_optionnel",
    "atelier",
    "exiger",
    "exiger_dossier",
    "reinitialiser_atelier",
    "restreindre",
    "session_de_travail",
    "unite_de_travail",
]

NOM_TEMOIN = "cga_session"


@lru_cache
def coffre() -> Coffre:
    """Le coffre du processus, construit une fois sur la clé configurée.

    Mis en cache parce que la dérivation de clé d'AES-GCM n'est pas gratuite et
    qu'il n'y a rien à isoler par requête : la clé est celle de l'installation,
    pas celle d'un locataire.

    ⚠️ Une clé **par locataire** serait plus solide — la fuite de l'une
    n'exposerait pas les autres. Elle imposerait un service de gestion de clés,
    leur rotation et leur sauvegarde, pour un cabinet qui n'a aujourd'hui qu'un
    seul locataire. Le jour où il y en aura plusieurs, ce choix se rediscute.
    """
    return coffre_depuis(configuration().cle_chiffrement)


@lru_cache
def service_de_notification() -> ServiceNotification:
    """Le service de courriel, choisi sur la configuration.

    ─────────────────────────────────────────────────────────────────────────
    LE CHOIX SE FAIT SUR `CGA_SMTP_HOTE`, ET NON SUR L'ENVIRONNEMENT

    Un drapeau `environnement == "production"` obligerait à mentir sur
    l'environnement pour essayer un envoi réel en recette. L'adresse du relais
    est déjà la réponse : il y en a une, on poste ; il n'y en a pas, on retient.
    Rien à croiser, rien à désynchroniser.

    ⚠️ La production ne peut pas démarrer sans relais — `Configuration`
    l'interdit. Ce défaut silencieux ne peut donc pas y survivre.

    MIS EN CACHE PARCE QUE C'EST UN CLIENT, PAS UN DÉPÔT

    Il ne porte aucun état de transaction. En mode mémoire, le cache est même
    nécessaire : les messages retenus **sont** ce que les tests inspectent, et
    deux instances en perdraient la moitié.
    ─────────────────────────────────────────────────────────────────────────
    """
    reglages = configuration()
    if not reglages.smtp_hote:
        return ServiceNotificationMemoire()
    return ServiceNotificationSmtp(
        transport=TransportSmtp(
            hote=reglages.smtp_hote,
            port=reglages.smtp_port,
            utilisateur=reglages.smtp_utilisateur,
            mot_de_passe=reglages.smtp_mot_de_passe,
            chiffrement=reglages.smtp_chiffrement,
        ),
        expediteur=reglages.courriel_expediteur,
        repondre_a=reglages.courriel_repondre_a,
        adresse_site=reglages.adresse_publique_site,
    )


class Atelier:
    """Les dépôts et services du contexte, réunis.

    ─────────────────────────────────────────────────────────────────────────
    UNE SEULE DÉPENDANCE PLUTÔT QUE SEPT

    Ces objets ont exactement la même durée de vie et sont remplacés ensemble
    quand la persistance change. Les injecter un par un dans chaque route
    ferait sept paramètres par signature, et l'un d'eux finirait par être
    oublié.

    DEUX RÉALISATIONS, ET LEUR DURÉE DE VIE DIFFÈRE

    `AtelierMemoire` vit le temps du processus : les dépôts *sont* la
    persistance, en construire un second reviendrait à ouvrir une seconde base.

    `AtelierSql` vit le temps d'une **requête** : il porte une session, et la
    transaction se ferme avec la requête. C'est une différence de nature, pas
    de réglage — voir `unite_de_travail`.
    ─────────────────────────────────────────────────────────────────────────
    """

    comptes: DepotComptes
    habilitations: DepotHabilitations
    jetons: DepotJetons
    sessions: DepotSessions
    #: Les mandats **accordés** par ce locataire. Voir `DepotMandats` : ils sont
    #: rangés chez le mandant, parce que c'est dans son périmètre qu'ils se vérifient.
    mandats: DepotMandats
    journal: JournalAudit
    empreintes: ServiceEmpreinteArgon2
    #: ⚠️ Annoté par le **port**, non par une réalisation : c'est le seul membre
    #: de l'atelier qui change avec la configuration plutôt qu'avec le mode de
    #: persistance. En développement il retient, en production il poste.
    notifications: ServiceNotification
    portail: PortailManuel


class AtelierMemoire(Atelier):
    """Tout en mémoire, garni du jeu de démonstration."""

    def __init__(self) -> None:
        (
            self.comptes,
            self.habilitations,
            self.jetons,
            self.sessions,
            self.journal,
        ) = depots_demo()
        self.mandats = DepotMandatsMemoire()
        self.empreintes = ServiceEmpreinteArgon2()
        self.notifications = service_de_notification()
        self.portail = PortailManuel()


class AtelierSql(Atelier):
    """Les dépôts adossés à une session PostgreSQL.

    ⚠️ Ni les notifications ni la dérivation d'empreinte ne sont des dépôts :
    elles ne suivent pas le mode de persistance. Le service de courriel se
    choisit sur la configuration — voir `service_de_notification` —, et Argon2
    n'a rien à persister.
    """

    def __init__(self, session: SessionSql, locataire: str) -> None:
        self.session = session
        self.locataire = locataire
        self.comptes = DepotComptesSql(session, locataire, coffre())
        self.habilitations = DepotHabilitationsSql(session, locataire)
        self.jetons = DepotJetonsSql(session, locataire)
        self.sessions = DepotSessionsSql(session, locataire)
        self.mandats = DepotMandatsSql(session, locataire)
        self.journal = JournalAuditSql(session, locataire)
        self.portail = PortailSql(session, locataire)
        self.empreintes = ServiceEmpreinteArgon2()
        self.notifications = service_de_notification()


#: L'atelier de la requête en cours, en mode SQL.
#:
#: ⚠️ Une variable de contexte est de l'état ambiant, et l'état ambiant se
#: défend mal en général. Il se défend **ici** pour une raison précise : la
#: transaction est la requête. Faire traverser une session à dix-huit signatures
#: de route ne donnerait toujours pas la validation en fin de requête — chaque
#: route devrait y penser, et l'une d'elles oublierait. Le point d'ouverture et
#: de fermeture est unique, et c'est l'intergiciel.
_ATELIER_COURANT: ContextVar[Atelier | None] = ContextVar("atelier", default=None)


@lru_cache
def _atelier_du_processus() -> AtelierMemoire:
    """L'atelier unique du mode mémoire.

    ⚠️ En mémoire, ceci **est** la persistance : deux instances seraient deux
    bases sans lien.
    """
    return AtelierMemoire()


def atelier() -> Atelier:
    """L'atelier en vigueur — de la requête en SQL, du processus en mémoire.

    En mode `postgresql`, hors d'une requête, **lève**. Un atelier fabriqué à la
    volée ouvrirait une transaction que personne ne fermerait, et les verrous
    qu'elle tiendrait sur la table du journal d'audit bloqueraient toutes les
    écritures suivantes. Mieux vaut une erreur immédiate qu'une application qui
    se fige au bout d'une heure.
    """
    courant = _ATELIER_COURANT.get()
    if courant is not None:
        return courant
    if not configuration().en_base:
        return _atelier_du_processus()
    raise RuntimeError(
        "aucune unité de travail ouverte. En persistance PostgreSQL, l'atelier vit "
        "le temps d'une requête : l'ouvrir depuis une tâche de fond ou un script "
        "suppose d'employer `unite_de_travail()` explicitement."
    )


def session_de_travail() -> SessionSql | None:
    """La session de l'unité de travail en cours, ou `None` en mémoire.

    C'est par elle que les autres contextes construisent leurs dépôts SQL. Ils
    n'ont ainsi ni moteur à ouvrir, ni transaction à gérer : **la transaction est
    la requête**, et elle est tenue par l'intergiciel.

    Rendre `None` plutôt que lever est ici correct : le mode mémoire est un mode
    de fonctionnement, pas une panne. L'appelant choisit ses dépôts en
    conséquence.
    """
    courant = _ATELIER_COURANT.get()
    return getattr(courant, "session", None)


def reinitialiser_atelier() -> None:
    """Repart d'un atelier mémoire vierge.

    Destinée aux **tests**, et nommée pour cela : `cache_clear()` sur une
    fonction mémoïsée disait comment plutôt que quoi, et cassait le jour où la
    mémoïsation changeait de place — ce qui vient d'arriver.

    En mémoire, l'atelier *est* la persistance : sans remise à zéro, un test qui
    suspend un compte le suspend pour les suivants. Sans effet en SQL, où chaque
    requête ouvre le sien.

    ⚠️ Le service de notification se vide **aussi**, et il le faut même en SQL :
    en mode retenu, les messages s'accumulent dans le processus, et un test qui
    vérifie « un lien d'activation est parti » lirait celui du test précédent.
    Un test vert sur la trace d'un autre est pire qu'un test rouge.
    """
    _atelier_du_processus.cache_clear()
    service_de_notification.cache_clear()
    coffre.cache_clear()


@contextmanager
def unite_de_travail(locataire: str | None = None) -> Iterator[Atelier]:
    """Ouvre un atelier pour la durée du bloc, et le valide en sortant.

    En mémoire, rend l'atelier du processus sans rien ouvrir : il n'y a pas de
    transaction à tenir.

    En SQL, ouvre une session **déjà cloisonnée** sur le locataire, la valide à
    la sortie normale et l'annule sur exception. La validation est ici et nulle
    part ailleurs : dix-huit routes qui valideraient chacune produiraient dix-
    huit transactions par requête, et une écriture partielle à la première
    erreur.
    """
    if not configuration().en_base:
        jeton = _ATELIER_COURANT.set(_atelier_du_processus())
        try:
            yield _atelier_du_processus()
        finally:
            _ATELIER_COURANT.reset(jeton)
        return

    # Lu ici et non reçu en constante : le locataire est établi au bord par
    # l'intergiciel, et une valeur en dur ici servirait le même client à tout le
    # monde sans que rien ne le signale.
    locataire = locataire or courant()
    with session_du_locataire(locataire) as session:
        boutique = AtelierSql(session, locataire)
        jeton = _ATELIER_COURANT.set(boutique)
        try:
            yield boutique
        finally:
            _ATELIER_COURANT.reset(jeton)


#: Ce que lit un client à qui l'on refuse l'entrée. Le message compte autant que le code :
#: un client suspendu doit comprendre pourquoi il n'entre plus et savoir quoi faire, là où
#: un 404 le laisserait croire à une panne.
_MESSAGES: dict[Verdict, str] = {
    Verdict.REGULARISER: (
        "Cet espace est suspendu. Régularisez votre situation pour le retrouver : "
        "vos données sont intactes."
    ),
    Verdict.DISPARU: "Cet espace a été fermé.",
    Verdict.INTROUVABLE: "Cet espace n'existe pas.",
}


logger = logging.getLogger(__name__)


@lru_cache
def repertoire_des_tenants() -> RepertoireEnMemoire:
    """Le répertoire, un par processus, garni au démarrage par `garnir_le_repertoire`.

    En mémoire, et ce n'est pas un pis-aller : la table des tenants tient entièrement
    dans le processus — quelques centaines d'entrées de moins de cent octets —, et la
    résolution d'un nom d'hôte doit rester une lecture de dictionnaire. La lire en base à
    chaque requête ajouterait un aller-retour au chemin critique de **tout** le trafic.
    """
    return RepertoireEnMemoire()


#: Quand la table des tenants a été lue pour la dernière fois dans ce processus.
#: `None` tant qu'elle ne l'a jamais été.
_DERNIER_GARNISSAGE: datetime | None = None


def garnir_le_repertoire() -> int:
    """Verse le contenu de la table des tenants dans le répertoire. Rend le nombre versé.

    Appelé au démarrage, puis par `repertoire_a_jour` quand la fenêtre est passée.

    ⚠️ **Il n'échoue pas si la base est injoignable.** Un répertoire vide rend 404 sur
    tout sous-domaine, ce qui est désagréable mais franc ; refuser de démarrer priverait
    aussi la vitrine et les sondes de santé, qui n'ont besoin d'aucun tenant. La panne se
    voit au journal et à la sonde, pas par un processus qui ne se lève pas.

    ⚠️ **L'instant du dernier essai est noté même quand l'essai échoue.** Sans cela, une
    base en difficulté serait interrogée par chaque requête qui arrive, c'est-à-dire
    martelée exactement au moment où elle a besoin qu'on la laisse respirer. C'est la même
    discipline que `INTERVALLE_APRES_PANNE` dans la boucle de fond.
    """
    global _DERNIER_GARNISSAGE

    if not configuration().en_base:
        return 0

    repertoire = repertoire_des_tenants()
    _DERNIER_GARNISSAGE = maintenant()
    try:
        with session_du_locataire(configuration().locataire_par_defaut) as session:
            tenants = list(DepotTenantsSql(session).tous())
    except Exception:  # noqa: BLE001 — voir l'en-tête : on démarre quand même
        logger.exception("répertoire des tenants non garni : la base est injoignable")
        return 0

    # ⚠️ Remplacé et non versé par-dessus : voir `RepertoireEnMemoire.remplacer`. Un
    # tenant retiré de la table doit disparaître du répertoire, sans quoi il continuerait
    # d'être servi.
    combien = repertoire.remplacer(tenants)
    logger.info("répertoire des tenants garni : %d tenant(s)", combien)
    return combien


def oublier_le_garnissage() -> None:
    """Fait comme si la table n'avait jamais été lue. Destiné aux tests."""
    global _DERNIER_GARNISSAGE

    _DERNIER_GARNISSAGE = None


def repertoire_a_jour() -> RepertoireEnMemoire:
    """Le répertoire, rechargé si la fenêtre de fraîcheur est passée.

    ─────────────────────────────────────────────────────────────────────────────────
    LE DÉFAUT QUE CETTE FONCTION CORRIGE

    Le répertoire était garni **une fois**, au démarrage. Le chantier multi-tenant le
    savait et l'a écrit dans ses restes : « un redémarrage suffit tant qu'il y a une
    souscription par jour, et ne suffira plus à dix ». Les deux moitiés du défaut sont à
    prendre au sérieux :

    * un tenant **ouvert** après le démarrage n'est pas résolu : le client qui vient de
      payer reçoit son lien d'activation, clique, et tombe sur un `404` ;
    * un tenant **suspendu ou résilié** reste résolu : on continue de servir les données
      d'un client qu'on a cessé de servir, ce qui est le plus grave des deux.

    POURQUOI UNE FENÊTRE, ET NON UN ÉVÉNEMENT

    Le répertoire vit **dans chaque processus**. Un abonnement en mémoire ne préviendrait
    que la réplique qui a ouvert le tenant, et les deux autres continueraient de rendre
    `404` : une panne intermittente, donc la pire à diagnostiquer. Un travail périodique
    n'irait pas mieux, puisque l'ordonnanceur tourne dans **une** réplique et ne connaît
    pas la mémoire des autres.

    Une fenêtre, elle, n'a rien à propager : chaque processus se remet à jour tout seul,
    et le fait quand on lui parle. Le jour où un bus d'événements traversera les
    processus, il rendra la mise à jour immédiate ; la fenêtre restera comme le filet, et
    c'est très bien ainsi.

    ⚠️ **Le coût est borné par la fenêtre, pas par le trafic.** Trois répliques et mille
    requêtes par minute font six lectures de la table par minute à elles trois. Un
    rechargement par requête, lui, rendrait le répertoire inutile : il existe pour que la
    résolution reste une lecture de dictionnaire.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    repertoire = repertoire_des_tenants()
    if not configuration().en_base:
        return repertoire

    fenetre = timedelta(seconds=configuration().fenetre_repertoire_tenants_secondes)
    if _DERNIER_GARNISSAGE is None or maintenant() - _DERNIER_GARNISSAGE >= fenetre:
        garnir_le_repertoire()
    return repertoire


class IntergicielUniteDeTravail(BaseHTTPMiddleware):
    """Ouvre une unité de travail par requête, et la ferme avec elle.

    **C'est ici que le locataire s'établit**, et nulle part ailleurs. Tout ce qui
    en a besoin le lit ensuite par `partage.locataire.courant()`, qui lève s'il
    n'a pas été établi — une lecture de données sans locataire servirait des
    lignes arbitraires sans que rien ne le signale.

    ⚠️ Il vient aujourd'hui de la configuration. Le jour où la passerelle résoudra
    le sous-domaine, elle posera un en-tête interne signé et cette méthode le lira
    à la place : **ce sera le seul endroit à changer**, ce qui est exactement la
    raison pour laquelle il est établi ici plutôt que dans chaque dépôt.
    """

    async def dispatch(self, request, call_next):  # noqa: ANN001 - signature imposée
        config = configuration()
        slug = slug_depuis_hote(request.headers.get("host"), config.domaine_racine)

        if slug is None:
            # Le nom d'hôte ne désigne aucun tenant : `localhost`, `testserver`, le
            # domaine nu. On sert le locataire configuré. C'est l'affordance de
            # développement, et elle disparaîtra le jour où la vitrine aura son propre
            # service — un domaine nu n'aura alors plus rien à faire ici.
            locataire = config.locataire_par_defaut
        else:
            tenant = repertoire_a_jour().par_slug(slug)
            verdict = verdict_pour(tenant)
            if verdict is not Verdict.SERVIR:
                # Le verdict **est** le code de statut. Voir `domaine/resolution.py` :
                # un 403 apprendrait au demandeur que le tenant existe, et énumérer les
                # sous-domaines deviendrait un moyen de découvrir le portefeuille.
                return JSONResponse(
                    status_code=int(verdict),
                    content={"detail": _MESSAGES[verdict]},
                )
            locataire = tenant.slug

        with etabli(locataire), unite_de_travail():
            return await call_next(request)


async def acces_optionnel(
    request: Request,
    cga_session: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Acces | None:
    """L'accès courant, ou `None`. Ne lève jamais.

    ⚠️ **ASYNCHRONE, ET CE N'EST PAS UN DÉTAIL DE STYLE.** Une dépendance synchrone est
    exécutée par le cadre dans un fil de la réserve, avec une **copie** du contexte : une
    variable de contexte posée là est perdue dès le retour, et le mandat découvert ici
    n'atteindrait jamais le journal. Déclarée asynchrone, elle s'exécute dans la tâche de
    la requête, et ce qu'elle pose est vu par tout ce qui suit.

    Le défaut a existé : le mandat était bien résolu, l'accès bien rendu, et les entrées
    du journal ne portaient rien. Un test l'a montré, pas une relecture.

    Sert aux routes qui se comportent différemment selon qu'on est connecté ou
    non, sans exiger de l'être.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE LOCATAIRE DU JETON EST CONFRONTÉ À CELUI DU DOMAINE, ICI ET NULLE PART
    AILLEURS.**

    C'est le seul endroit où les deux se rencontrent : le domaine a établi le
    locataire au bord, le jeton dit où il a été ouvert. Mesuré avant la correction,
    en mémoire, une session ouverte sur un sous-domaine était acceptée sur un autre,
    et la requête était servie dans le périmètre du second avec les permissions d'un
    compte du premier.

    En base, le filtre de cloisonnement fermait la porte **par accident** : la ligne
    de session n'était pas trouvée ailleurs. Un bon résultat obtenu par un mécanisme
    qui ne visait pas cela disparaît le jour où l'on change ce mécanisme.

    ⚠️ **Le refus est silencieux pour l'appelant** : il reçoit exactement ce qu'il
    recevrait avec un jeton expiré. Lui dire que son jeton appartient à un autre
    locataire confirmerait que ce locataire existe, ce que la règle du projet
    interdit. La trace, elle, part au journal du serveur.
    ─────────────────────────────────────────────────────────────────────────────
    """
    valeur = cga_session
    if valeur is None and authorization and authorization.startswith("Bearer "):
        valeur = authorization.removeprefix("Bearer ").strip()
    if not valeur:
        return None

    locataire_du_jeton, identifiant = lire_le_jeton(valeur)
    if not identifiant:
        return None
    if locataire_du_jeton is not None and locataire_du_jeton != courant():
        return _acces_sous_mandat(request, locataire_du_jeton, identifiant)

    boutique = atelier()
    instant = maintenant()
    try:
        session, compte, habilitations = verifier_session(
            identifiant,
            comptes=boutique.comptes,
            sessions=boutique.sessions,
            habilitations=boutique.habilitations,
            a_l_instant=instant,
        )
    except (SessionInvalide, CompteIntrouvable):
        return None
    request.state.session = session
    return resoudre_acces(compte, session, habilitations, instant.date())


def acces_courant(
    acces: Annotated[Acces | None, Depends(acces_optionnel)],
) -> Acces:
    """L'accès courant, ou `401`."""
    if acces is None:
        raise HTTPException(
            status_code=401,
            detail="Session absente, expirée ou révoquée. Se reconnecter.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return acces


def exiger(
    acces: Acces,
    permission: Permission,
    *,
    dossier: str | None = None,
    motif: str | None = None,
) -> None:
    """Traduit un refus métier en `403`, sans en dire la raison précise.

    La raison part au journal côté serveur — voir l'en-tête. L'appelant reçoit le
    nom de la permission manquante, ce qui suffit à corriger une erreur
    d'intégration sans révéler quels dossiers existent.
    """
    try:
        acces.exiger(permission, dossier=dossier, motif=motif)
    except AccesRefuse as refus:
        raise HTTPException(
            status_code=403,
            detail=f"Action non permise : {permission} est requise. ({type(refus).__name__})",
        ) from refus


AccesRequis = Annotated[Acces, Depends(acces_courant)]


T = TypeVar("T")


def restreindre(
    acces: Acces, elements: Iterable[T], niu: Callable[[T], str | None]
) -> list[T]:
    """Ne garde d'une liste que ce que le périmètre couvre.

    ─────────────────────────────────────────────────────────────────────────
    UNE LISTE SE RESTREINT, UNE LECTURE UNITAIRE SE REFUSE

    Refuser toute la liste parce qu'un élément sort du périmètre rendrait
    l'écran inutilisable : la boîte de réception d'un comptable contient les
    pièces de tout le cabinet avant filtrage, et il n'y aurait jamais rien à
    afficher.

    À l'inverse, restreindre une lecture unitaire n'a pas de sens : on ne rend
    pas « une version amputée » d'un dossier. Voir `exiger_dossier`.

    Un élément sans NIU — une pièce non encore rattachée, une ligne de
    catalogue — est **conservé** : il n'appartient à personne, donc à personne
    en particulier. L'écarter ferait disparaître des écrans les pièces qui
    attendent précisément qu'on leur trouve un dossier.
    ─────────────────────────────────────────────────────────────────────────
    """
    if acces.dossiers is None:
        return list(elements)
    couverts = set(acces.dossiers)

    def garde(element: T) -> bool:
        dossier = niu(element)
        return dossier is None or dossier in couverts

    return [element for element in elements if garde(element)]


def exiger_dossier(
    acces: Acces,
    permission: Permission,
    niu: str,
    *,
    motif: str | None = None,
    introuvable: str | None = None,
) -> None:
    """Contrôle le droit **et** le périmètre sur un dossier nommé.

    ─────────────────────────────────────────────────────────────────────────
    HORS PÉRIMÈTRE REND 404, PAS 403

    Un `403` dit « ce dossier existe, et il ne vous regarde pas ». C'est une
    information, et elle a de la valeur : un adhérent apprendrait par essais
    successifs quels NIU le cabinet suit, c'est-à-dire la liste de ses clients
    — laquelle intéresse un concurrent.

    Du point de vue de qui n'y a pas accès, un dossier hors périmètre est un
    dossier qui n'existe pas. Le `404` dit exactement cela, et il ne ment pas :
    il n'existe pas **pour lui**.

    Le `403` reste employé quand c'est la **permission** qui manque, sans
    dossier en jeu : là, rien n'est révélé qu'on ne sache déjà — la table des
    rôles est publiée sur `/transverse/roles`.

    LE MOTIF SE TRANSMET, IL NE SE CONTOURNE PAS

    Trois permissions figurent dans `EXIGE_MOTIF` : écarter un constat,
    contre-passer, clore un exercice. Sans le paramètre `motif`, une route qui
    porte sur un dossier ne pouvait pas les exiger correctement — elle recevait
    un `403 MotifRequis` même en ayant recueilli le motif. Le remède n'était
    surtout pas d'appeler `exiger` puis de contrôler le périmètre à la main :
    c'est ainsi qu'un jour l'un des deux contrôles se perd.

    ⚠️ QUAND LE NIU VIENT D'UNE RESSOURCE, LE REFUS NE LE NOMME PAS (pas 88)

    Le message « aucun dossier {niu} accessible » est juste quand l'appelant a lui-même
    écrit le NIU dans l'adresse : il ne lui apprend rien. Il fuit quand le NIU est **lu
    sur la ressource** demandée (une pièce, une demande, une facture). Essai : un
    adhérent de LA COLOMBE demandait le document d'une pièce d'un autre dossier et
    recevait « aucun dossier M081234567890P accessible », soit le NIU du client à qui
    la pièce appartient, et une réponse différente de celle d'une pièce inexistante.
    Les identifiants de démonstration étant séquentiels, on énumérait.

    Ces routes passent `introuvable`, **le même texte** que celui qu'elles rendent pour
    une ressource inconnue : un hors-périmètre et une absence ne se distinguent plus.
    ─────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, permission, motif=motif)
    if not acces.voit(niu):
        raise HTTPException(
            status_code=404,
            detail=introuvable
            or (
                f"aucun dossier {niu} accessible. Si ce dossier existe, il n'est pas "
                "dans votre portefeuille : en demander l'affectation."
            ),
        )


def _acces_sous_mandat(
    request: Request, mandataire: str, identifiant: str
) -> Acces | None:
    """L'accès d'un compte d'ailleurs, s'il porte un mandat du locataire servi.

    ─────────────────────────────────────────────────────────────────────────────────
    C'EST LE CAS LE PLUS COURANT DE LA PLATEFORME, ET LE PLUS DÉLICAT

    Le centre tient la comptabilité d'une entreprise qui dispose aussi de son propre
    accès. Deux locataires, deux jeux de données, et pourtant les mêmes écritures.

    TROIS LECTURES, DANS DEUX PÉRIMÈTRES, ET L'ORDRE COMPTE

    1. La session se lit **chez le mandataire** : c'est là que vit le compte du centre.
       C'est le seul endroit de l'application qui lit hors du périmètre servi, et il est
       borné à cette lecture.
    2. Les mandats se lisent **chez le mandant**, c'est-à-dire dans le périmètre servi :
       c'est lui qui les accorde et lui qui les retire.
    3. Les rôles ne sont gardés que si un mandat les couvre.

    ⚠️ **UN MANDAT N'AJOUTE AUCUN RÔLE.** Il autorise l'exercice, ailleurs, de rôles déjà
    tenus. Un comptable mandaté reste comptable et ne devient pas réviseur en franchissant
    la frontière. Confondre les deux ferait du mandat une porte d'élévation de privilèges,
    ce qu'il doit précisément empêcher.

    ⚠️ **Le refus rend `None`, comme un jeton expiré.** Dire « aucun mandat » apprendrait
    que le locataire visé existe, ce que la règle du projet interdit. Le motif exact, que
    le domaine sait produire, part au journal du serveur pour l'exploitation.

    ⚠️ **Le périmètre de dossiers n'est pas repris du mandataire.** Les habilitations du
    compte désignent des dossiers **de son propre locataire** ; les transporter ici
    désignerait des dossiers qui n'existent pas, ou pire, des dossiers homonymes. Un accès
    sous mandat est donc transversal **dans le périmètre du mandant**, et c'est le mandat
    qui borne, pas la liste.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    mandant = courant()
    instant = maintenant()

    try:
        with unite_de_travail(mandataire) as chez_le_mandataire:
            session, compte, habilitations = verifier_session(
                identifiant,
                comptes=chez_le_mandataire.comptes,
                sessions=chez_le_mandataire.sessions,
                habilitations=chez_le_mandataire.habilitations,
                a_l_instant=instant,
            )
            acces = resoudre_acces(compte, session, habilitations, instant.date(), instant)
    except (SessionInvalide, CompteIntrouvable):
        return None

    mandats = atelier().mandats.tous()
    couverts: list[Role] = []
    motifs: set[str] = set()
    for role in acces.roles:
        mandat, refus = mandat_applicable(
            mandats,
            mandataire=mandataire,
            mandant=mandant,
            compte=acces.compte,
            role=role,
            a_la_date=instant.date(),
        )
        if mandat is not None:
            couverts.append(role)
            retenu = mandat
        elif refus is not None:
            motifs.add(refus.value)

    if not couverts:
        logger.warning(
            "compte %s de %s sur %s : aucun mandat applicable (%s)",
            acces.compte,
            mandataire,
            mandant,
            ", ".join(sorted(motifs)) or "AUCUN",
        )
        return None

    poser_le_mandat(retenu.identifiant)
    request.state.session = session
    logger.info(
        "compte %s de %s agit sur %s sous le mandat %s",
        acces.compte,
        mandataire,
        mandant,
        retenu.identifiant,
    )
    return acces.model_copy(
        update={
            "roles": sorted(couverts, key=lambda r: r.value),
            "permissions": sorted(permissions_de(frozenset(couverts)), key=lambda p: p.value),
            # ⚠️ Voir l'en-tête : les dossiers du mandataire ne désignent rien chez le
            # mandant. Le mandat est ce qui borne.
            "dossiers": None,
        }
    )
