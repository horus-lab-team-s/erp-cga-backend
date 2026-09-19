"""API du socle d'orchestration : faire tourner le relais, l'ordonnanceur, et voir où ils en sont.

─────────────────────────────────────────────────────────────────────────────────
LA ROUTE D'ABORD, LA BOUCLE ENSUITE : L'ANNONCE A ÉTÉ TENUE

Cet en-tête disait, au pas 8 : « Le jour où l'ordonnanceur existera, il appellera
cette route ou la fonction qu'elle enveloppe, et rien du socle ne changera. »

C'est ce qui s'est passé. `POST /orchestration/ordonnancement` fait un tour
complet, et la boucle de fond appelle exactement la même fonction. Aucune des trois
objections qui repoussaient la boucle n'est restée sans réponse, et il vaut la
peine de dire comment, parce que chacune se serait payée en exploitation.

Elle **tournerait dans chaque réplique** : c'est toujours vrai, et c'est le verrou
consultatif de `app/infrastructure/verrou.py` qui l'arbitre. Deux répliques
demandent, une seule obtient, l'autre passe son tour sans lever.

Elle serait **invisible aux tests** : la décision « qu'est-ce qui est dû » a été
sortie de la boucle et vit dans `app/orchestration/ordonnanceur.py`, où l'instant
est un argument. Vérifier qu'un travail quotidien passe coûte une soustraction.

Elle **masquerait ses échecs** : le tour rend un rapport, chaque échec est compté,
le recul s'allonge, et l'abandon au delà du seuil se lit sur la route
`GET /orchestration/ordonnancement` sans ouvrir un journal.

⚠️ ELLE N'EST PAS PUBLIQUE, ET ELLE NE DOIT PAS L'ÊTRE

Déclencher une publication est une opération d'exploitation : elle remet des
événements à des consommateurs qui ouvrent des tenants et envoient des messages.
Elle demande `GERER_COMPTES`, la permission d'administration, et le geste est
journalisé comme tout le reste.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.tenants.api import (
    DepotTenantsSql,
    ProvisionneurLocal,
    RegistreDurable,
    RegistreEnMemoire,
    abonner_l_ouverture,
)
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
    repertoire_des_tenants,
    session_de_travail,
)
from app.contextes.transverse.domaine.roles import Permission
from app.infrastructure.config import configuration
from app.infrastructure.depots_orchestration import (
    BoiteDEnvoiMemoire,
    BoiteDEnvoiSql,
    DepotExecutionsMemoire,
    DepotExecutionsSql,
    DepotPassagesMemoire,
    DepotPassagesSql,
)
from app.infrastructure.verrou import verrou_exclusif
from app.orchestration.boite_d_envoi import EvenementSortant, RemiseRefusee
from app.orchestration.inscription import (
    abonnes_inscrits,
    executants_inscrits,
    travaux_inscrits,
)
from app.orchestration.ordonnanceur import Passage as PassageOrdonnance
from app.orchestration.ordonnanceur import RepriseRefusee, Travail
from app.orchestration.relais import Abonnements, publier_un_lot
from app.orchestration.tour import faire_un_tour
from app.partage.horloge import maintenant
from app.partage.locataire import courant

_journal = logging.getLogger("cga.ordonnanceur")

routeur = APIRouter(prefix="/orchestration", tags=["Orchestration"])


class Atelier:
    """La boîte, les exécutions et les abonnements, réunis pour une requête.

    ⚠️ Les dépôts SQL sont reconstruits à chaque appel parce qu'ils portent la
    session de la requête. Les mémoïser conserverait une session déjà fermée,
    panne qui n'apparaîtrait qu'à la seconde requête.
    """

    def __init__(self) -> None:
        session = session_de_travail()
        if session is None:
            self.boite = _boite_memoire()
            self.executions = _executions_memoire()
            registre = _registre_memoire()
        else:
            locataire = courant()
            self.boite = BoiteDEnvoiSql(session, locataire)
            self.executions = DepotExecutionsSql(session, locataire)
            # ⚠️ **Le registre durable, et le défaut qu'il corrige était grave.**
            #
            # Cette ligne rendait `_registre_memoire()` même en persistance
            # PostgreSQL. Mesuré : un paiement encaissé, la saga rendait
            # `TERMINEE`, et la table `tenant` contenait zéro ligne. Le système
            # **annonçait le succès de ce qu'il n'avait pas fait**, et le
            # sous-domaine d'un abonnement payé rendait 404 au redémarrage suivant.
            #
            # Le registre écrit désormais en base **et** inscrit au répertoire de la
            # passerelle : sans le second, le sous-domaine d'un client qui vient de
            # payer n'aurait répondu qu'au prochain redéploiement.
            registre = RegistreDurable(
                DepotTenantsSql(session), repertoire_des_tenants(), session
            )

        self.abonnements = Abonnements()
        abonner_l_ouverture(
            self.abonnements,
            provisionneur=ProvisionneurLocal(registre, a_la_date=maintenant().date()),
            executions=self.executions,
            horloge=maintenant,
        )
        # ⚠️ Les abonnés des autres services sont **inscrits**, jamais importés. Le
        # graphe n'autorise au Transverse que le Référentiel et les Tenants : c'est
        # pourquoi l'ouverture ci-dessus s'écrit ici et pourquoi la remise des
        # relances, qui appartient à la Souscription, passe par le registre.
        #
        # Les fabriques sont appelées **maintenant** et non au démarrage : un abonné
        # construit ses dépôts sur la session de cet atelier, et un abonné mémorisé
        # au démarrage porterait une session fermée.
        for nom, fabriques in abonnes_inscrits().items():
            for fabrique in fabriques:
                self.abonnements.abonner(nom, fabrique())


def boite_d_envoi():
    """La boîte d'envoi de l'unité de travail en cours.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Exposée sur la surface publique du Transverse**, et c'est un choix.

    La boîte d'envoi n'est le métier de personne : elle vit avec l'infrastructure au
    même titre que le journal d'audit, et **tout contexte a vocation à y déposer**
    un fait qu'il vient d'établir. La Souscription y dépose l'encaissement qui
    ouvrira un tenant ; d'autres suivront.

    Elle est exposée ici plutôt que reconstruite par chaque appelant, et le motif
    n'est pas la commodité : en persistance mémoire, deux fabriques seraient **deux
    boîtes**. L'un déposerait, l'autre publierait, et rien ne partirait jamais sans
    qu'aucune erreur ne se produise.
    ─────────────────────────────────────────────────────────────────────────────
    """
    session = session_de_travail()
    if session is None:
        return _boite_memoire()
    return BoiteDEnvoiSql(session, courant())


@lru_cache
def _boite_memoire() -> BoiteDEnvoiMemoire:
    return BoiteDEnvoiMemoire()


@lru_cache
def _executions_memoire() -> DepotExecutionsMemoire:
    return DepotExecutionsMemoire()


@lru_cache
def _registre_memoire() -> RegistreEnMemoire:
    return RegistreEnMemoire()


def reinitialiser_orchestration() -> None:
    """Repart d'un socle vierge. Destiné aux tests."""
    _boite_memoire.cache_clear()
    _executions_memoire.cache_clear()
    _registre_memoire.cache_clear()
    # ⚠️ Pas 84 : les passages de l'ordonnanceur n'étaient pas remis à zéro. Un test qui
    # abandonnait un travail le laissait abandonné pour tous les tests suivants du
    # processus, qui ne l'auraient plus vu tourner.
    _passages_memoire.cache_clear()


class Passage(BaseModel):
    """Ce qu'un passage a fait. Sans aucun contenu d'événement."""

    publies: int
    sans_abonne: int
    echecs: int
    retenus: int
    mis_en_quarantaine: int
    motifs: list[str] = Field(default_factory=list)
    demande_un_regard: bool


@routeur.post(
    "/publication",
    summary="Faire tourner un passage de publication",
    responses={403: {"description": "Geste réservé à l'administration"}},
)
def publier(
    acces: AccesRequis, limite: int = Query(default=100, ge=1, le=1000)
) -> Passage:
    """Vide un lot de la boîte d'envoi, dans l'ordre.

    ⚠️ **Rejouable sans dommage.** Un passage sur une boîte vide ne fait rien, et
    un événement déjà publié n'est pas remis. C'est ce qui permet à
    l'ordonnanceur de l'appeler toutes les cinq secondes sans réfléchir.
    """
    exiger(acces, Permission.GERER_COMPTES)
    atelier = Atelier()
    rapport = publier_un_lot(
        atelier.boite, atelier.abonnements, maintenant(), limite=limite
    )
    return Passage(
        publies=rapport.publies,
        sans_abonne=rapport.sans_abonne,
        echecs=rapport.echecs,
        retenus=rapport.retenus,
        mis_en_quarantaine=rapport.mis_en_quarantaine,
        motifs=list(rapport.motifs),
        demande_un_regard=rapport.demande_un_regard,
    )


class EvenementVisible(BaseModel):
    """Un événement tel que l'exploitation le voit : identifiants et décisions, jamais la
    charge (pas 84 : modèle déclaré, la route rendait un dictionnaire libre que l'outil
    de contrat des écrans ne pouvait pas vérifier)."""

    identifiant: str
    nom: str
    cle: str
    cree_le: datetime | None
    tentatives: int
    dernier_echec: str | None


def _visible(e: EvenementSortant) -> EvenementVisible:
    return EvenementVisible(
        identifiant=e.identifiant,
        nom=e.nom,
        cle=e.cle,
        cree_le=e.cree_le,
        tentatives=e.tentatives,
        dernier_echec=e.dernier_echec,
    )


class DemandeDExploitation(BaseModel):
    """Le motif d'un geste d'exploitation, écrit au journal d'audit (pas 84).

    ⚠️ Fermé aux champs inconnus : l'auteur est la session, jamais un champ du corps.
    """

    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=500)


@routeur.get(
    "/en-attente",
    summary="Ce que la boîte d'envoi n'a pas encore publié",
)
def en_attente(
    acces: AccesRequis, limite: int = Query(default=50, ge=1, le=500)
) -> list[EvenementVisible]:
    """Les identifiants et les noms, jamais les charges.

    Un journal circule, se copie, part chez un prestataire d'analyse, et n'a pas
    le régime de protection d'une base métier. On rend des **identifiants** et
    des **décisions**, jamais des contenus.
    """
    exiger(acces, Permission.LIRE_AUDIT)
    return [_visible(e) for e in Atelier().boite.a_publier(limite)]


@routeur.get(
    "/quarantaine",
    summary="Ce qu'aucun consommateur n'a accepté",
)
def quarantaine(acces: AccesRequis) -> list[EvenementVisible]:
    """À regarder tous les matins.

    La quarantaine n'est pas une suppression : c'est la trace d'un fait qui a
    bien eu lieu et que personne n'a su traiter. Un tenant payé qui ne s'est
    jamais ouvert se trouve ici, et nulle part ailleurs.
    """
    exiger(acces, Permission.LIRE_AUDIT)
    return [_visible(e) for e in Atelier().boite.en_quarantaine()]


@routeur.post(
    "/quarantaine/{identifiant}/remise",
    summary="Remettre un événement en quarantaine dans la file du relais",
    responses={
        403: {"description": "Geste réservé à l'administration"},
        404: {"description": "Aucun événement en quarantaine sous cet identifiant"},
    },
)
def remettre_en_circulation(
    acces: AccesRequis, identifiant: str, demande: DemandeDExploitation
) -> EvenementVisible:
    """Le rejeu « à la main » que la quarantaine promettait (pas 84).

    ⚠️ `GERER_COMPTES` et non `LIRE_AUDIT` : lire la quarantaine est un contrôle, la
    rejouer remet un fait à des consommateurs qui ouvrent des tenants et envoient des
    messages. Même permission que la publication.

    Le motif dit ce qui a été corrigé : remettre sans corriger renverrait l'événement en
    quarantaine au bout de dix tentatives, et le journal doit dire pourquoi on a cru
    que cette fois serait la bonne.
    """
    exiger(acces, Permission.GERER_COMPTES)
    boite = Atelier().boite
    trouve = next((e for e in boite.en_quarantaine() if e.identifiant == identifiant), None)
    if trouve is None:
        # 404 et non 409 : la liste de quarantaine est la seule source, et un
        # événement publié ou en attente n'y figure pas.
        raise HTTPException(
            status_code=404, detail=f"aucun événement en quarantaine sous « {identifiant} »"
        )
    try:
        remis = trouve.remettre_en_circulation()
    except RemiseRefusee as refus:  # pragma: no cover — filtré par la recherche ci-dessus
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    boite.enregistrer(remis)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="orchestration.evenement_remis",
        objet_type="evenement",
        objet_id=identifiant,
        avant={"tentatives": trouve.tentatives, "dernier_echec": trouve.dernier_echec},
        apres={"nom": trouve.nom, "cle": trouve.cle},
        motif=demande.motif,
    )
    return _visible(remis)


# ── L'ordonnanceur ───────────────────────────────────────────────────────────

#: Le nom du travail de publication, employé comme clé de passage **et** comme
#: nom de verrou. Une constante plutôt que deux chaînes recopiées : deux graphies
#: divergentes donneraient un verrou pris sur un nom et un passage écrit sur
#: l'autre, donc aucune exclusion et aucun message pour le dire.
TRAVAIL_RELAIS = "relais"
TRAVAIL_RELANCE = "relance"

#: Le nom du verrou qui protège **le tour entier**, et non chaque travail.
#:
#: ⚠️ Un verrou par travail laisserait deux instances se répartir les travaux d'un
#: même tour, ce qui paraît mieux et ne l'est pas : l'ordre de déclaration cesserait
#: d'être tenu, et le balayage de relance pourrait déposer ses envois pendant que
#: l'autre instance vient de vider la boîte.
VERROU_DU_TOUR = "ordonnancement"

#: La taille de lot employée par la boucle de fond, où personne ne la choisit.
#: La route la reçoit en paramètre : l'exploitation peut vouloir rattraper un
#: arriéré d'un coup, la boucle ne le veut jamais. Voir `publier_un_lot` : un lot
#: borné puis validé permet d'avancer même très en retard.
LOT_PAR_DEFAUT = 100


def travaux_declares() -> list[Travail]:
    """Les travaux périodiques, dans l'ordre où ils doivent tourner.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **L'ORDRE COMPTE, ET LE RELAIS PASSE EN PREMIER.**

    Il est le chemin vital : c'est lui qui ouvre les tenants payés, et un client qui
    règle attend que son sous-domaine réponde. Le faire passer derrière un balayage
    qui parcourt des centaines de proformas ajouterait la durée du parcours au délai
    d'ouverture, sur chaque tour où les deux coïncident.

    Les envois déposés par le balayage n'attendent pas pour autant : ils partent au
    passage suivant du relais, quelques secondes plus tard, et non au passage suivant
    du balayage, une heure plus tard.

    ⚠️ **Un travail n'est déclaré que s'il peut tourner.** Le balayage a attendu ici
    la table de son suivi : le déclarer avant en aurait fait un « travail sans
    exécutant », que `faire_un_tour` signale précisément parce que c'est un défaut de
    branchement et non un travail au repos. Déclarer un travail qu'on ne peut pas
    faire tourner est pire que ne pas le déclarer : la sonde le montre, l'exploitant
    le croit actif, et rien ne tourne.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return travaux_inscrits()


def travail_du_relais() -> Travail:
    """Le seul travail que le Transverse déclare de son propre chef.

    Il est le sien : la boîte d'envoi et le relais ne sont le métier de personne, et
    vivent avec l'infrastructure au même titre que le journal d'audit.
    """
    return Travail(
        nom=TRAVAIL_RELAIS,
        cadence=timedelta(seconds=configuration().cadence_relais_secondes),
        objet=(
            "vide la boîte d'envoi : c'est ce qui ouvre les tenants payés et "
            "poste les messages en attente"
        ),
    )


def executant_du_relais() -> str:
    """Un passage de publication, bâti sur l'unité de travail en cours.

    ⚠️ Il ne prend pas de taille de lot. Rattraper un arriéré d'un coup est un geste
    **manuel**, et il a sa route : `POST /orchestration/publication` accepte une
    limite. Un tour d'ordonnanceur, lui, avance par lots bornés et repasse.
    """
    atelier = Atelier()
    rapport = publier_un_lot(
        atelier.boite, atelier.abonnements, maintenant(), limite=LOT_PAR_DEFAUT
    )
    # ⚠️ Un échec de remise ne lève pas. Le relais existe pour que la panne d'un
    # consommateur n'arrête pas les autres, et propager ici ferait reculer tout le
    # travail de publication parce qu'un seul abonné est injoignable.
    return (
        f"{rapport.publies} publié(s), {rapport.echecs} échec(s), "
        f"{rapport.retenus} retenu(s), {rapport.mis_en_quarantaine} en quarantaine"
    )




def _depot_des_passages():
    """Le dépôt durable si la base est là, celui qui oublie sinon.

    ⚠️ La différence n'est pas cosmétique : en mémoire, le compte à rebours de
    chaque travail repart à zéro au redémarrage, et un travail quotidien sur une
    plateforme déployée chaque matin ne passerait jamais. C'est la raison d'être de
    la table, et c'est pourquoi la persistance mémoire n'est pas un mode
    d'exploitation.
    """
    session = session_de_travail()
    return _passages_memoire() if session is None else DepotPassagesSql(session)


@lru_cache
def _passages_memoire() -> DepotPassagesMemoire:
    return DepotPassagesMemoire()


class ResultatTravail(BaseModel):
    travail: str
    reussi: bool
    compte_rendu: str


class Tour(BaseModel):
    """Ce qu'un tour a fait. Sans aucun contenu d'événement."""

    #: `False` quand une autre instance tenait le verrou. Ce n'est pas une erreur :
    #: c'est le fonctionnement voulu, et l'appelant doit pouvoir le distinguer d'un
    #: tour qui n'avait rien à faire.
    execute: bool
    resultats: list[ResultatTravail] = Field(default_factory=list)
    sans_executant: list[str] = Field(default_factory=list)
    #: Les travaux abandonnés : trop d'échecs d'affilée. Ils ne repartiront pas seuls.
    abandonnes: list[str] = Field(default_factory=list)
    #: ⚠️ **Vrai quand aucun travail n'est inscrit.** C'est un défaut de composition,
    #: pas un état de repos : un ordonnanceur sans travail tourne indéfiniment sans
    #: rien faire, et rien ne le distingue d'un ordonnanceur dont rien n'est dû.
    #:
    #: Le cas s'est présenté en éprouvant le tour hors requête : les travaux sont
    #: inscrits par la composition de l'application, et un tour appelé sans elle
    #: passait au vert en ne faisant rien.
    aucun_travail_inscrit: bool = False


@routeur.post(
    "/ordonnancement",
    summary="Faire un tour d'ordonnanceur",
    responses={403: {"description": "Geste réservé à l'administration"}},
)
def ordonnancer(
    acces: AccesRequis, limite: int = Query(default=100, ge=1, le=1000)
) -> Tour:
    """Fait tourner ce qui est dû, une seule instance à la fois.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Rejouable sans dommage, et appelable aussi souvent qu'on veut.** Un tour
    dont rien n'est dû ne fait rien. C'est ce qui permet à un ordonnanceur
    extérieur — `cron`, un déclencheur d'orchestrateur, une sonde — de l'appeler à
    son propre rythme sans connaître les cadences déclarées ici.

    LE VERROU EST PRIS AVANT DE LIRE LES PASSAGES, PAS APRÈS

    Lire d'abord ouvrirait une fenêtre entre la lecture et la prise du verrou :
    deux instances liraient le même « rien n'est passé depuis une heure », l'une
    prendrait le verrou, ferait le travail, et l'autre le referait au tour suivant
    sur un état déjà périmé qu'elle a en main.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.GERER_COMPTES)
    return _tour_verrouille(limite)


def _tour_verrouille(limite: int) -> Tour:
    """Un tour, si et seulement si ce processus obtient le verrou.

    Partagé par la route et par la boucle de fond : les deux ont exactement le même
    besoin, et l'écrire deux fois ferait que l'une des deux finirait par oublier le
    verrou. Ce serait l'oubli le plus coûteux du module, puisqu'il ne casse rien et
    double simplement le travail.
    """
    session = session_de_travail()
    if session is None:
        # Sans base, il n'y a personne à exclure : une seule instance, en mémoire.
        return _un_tour(limite)
    with verrou_exclusif(session, VERROU_DU_TOUR) as pris:
        if not pris:
            return Tour(execute=False)
        return _un_tour(limite)


def _un_tour(limite: int) -> Tour:
    """Le tour lui-même, verrou déjà arbitré. Partagé avec la boucle de fond.

    C'est la fonction que l'en-tête de ce module annonçait au pas 8 : la route et
    la boucle appellent la même chose, et le socle n'a pas changé.
    """
    # ⚠️ Plus d'atelier construit ici : chaque exécutant bâtit ce dont il a besoin
    # au moment où il tourne. C'est ce qui permet à un travail d'un autre service de
    # s'inscrire sans que ce module connaisse ses dépôts.
    depot = _depot_des_passages()
    passages = depot.tous()
    travaux = travaux_declares()

    if not travaux:
        # ⚠️ Journalisé en `error` et non en `warning`. Un ordonnanceur sans travail
        # est un service qui tourne sans rien faire : la boîte d'envoi ne se vide
        # plus, les tenants payés ne s'ouvrent plus, et aucune requête n'échoue pour
        # le signaler. Voir `_inscrire_les_travaux_periodiques` dans `app/main.py`.
        _journal.error(
            "ordonnanceur : aucun travail inscrit. La composition de l'application "
            "n'a pas été jouée, et rien ne tournera."
        )
        return Tour(execute=True, aucun_travail_inscrit=True)

    rapport = faire_un_tour(travaux, passages, executants_inscrits(), maintenant())
    # Une écriture par travail, début et fin portés ensemble. ⚠️ Écrire le début
    # à part demanderait de **valider** entre les deux, ce qui rendrait le verrou
    # consultatif au milieu du tour : il tombe à la validation. Voir l'en-tête de
    # `app/orchestration/tour.py`, où la contradiction est expliquée.
    for passage in rapport.fins:
        depot.enregistrer(passage)

    apres = depot.tous()
    return Tour(
        execute=True,
        resultats=[
            ResultatTravail(
                travail=r.travail, reussi=r.reussi, compte_rendu=r.compte_rendu
            )
            for r in rapport.resultats
        ],
        sans_executant=list(rapport.sans_executant),
        abandonnes=sorted(
            nom for nom, passage in apres.items() if passage.abandonne
        ),
    )


class EtatDUnTravail(BaseModel):
    """L'état d'un travail déclaré (pas 84 : modèle déclaré, voir `EvenementVisible`)."""

    travail: str
    objet: str
    cadence_secondes: float
    jamais_passe: bool
    termine_le: datetime | None
    echecs_consecutifs: int
    dernier_echec: str | None
    abandonne: bool


@routeur.get(
    "/ordonnancement",
    summary="Où en est chaque travail périodique",
    responses={403: {"description": "Geste réservé à l'administration"}},
)
def etat_de_l_ordonnanceur(acces: AccesRequis) -> list[EtatDUnTravail]:
    """L'état de chaque travail déclaré, y compris ceux qui ne sont jamais passés.

    ⚠️ **Les travaux déclarés, et non les passages enregistrés.** Lister la table
    montrerait ce qui a tourné et tairait ce qui n'a jamais démarré — c'est-à-dire
    exactement le cas qu'on cherche à voir. Un travail déclaré sans ligne apparaît
    ici avec `jamais_passe`.
    """
    exiger(acces, Permission.GERER_COMPTES)
    passages = _depot_des_passages().tous()
    etat = []
    for travail in travaux_declares():
        passage = passages.get(
            travail.nom, PassageOrdonnance(travail=travail.nom)
        )
        etat.append(_etat(travail, passage))
    return etat


def _etat(travail: Travail, passage: PassageOrdonnance) -> EtatDUnTravail:
    return EtatDUnTravail(
        travail=travail.nom,
        objet=travail.objet,
        cadence_secondes=travail.cadence.total_seconds(),
        jamais_passe=passage.jamais_passe,
        termine_le=passage.termine_le,
        echecs_consecutifs=passage.echecs_consecutifs,
        dernier_echec=passage.dernier_echec,
        abandonne=passage.abandonne,
    )


@routeur.post(
    "/ordonnancement/{travail}/reprise",
    summary="Reprendre un travail périodique abandonné",
    responses={
        403: {"description": "Geste réservé à l'administration"},
        404: {"description": "Aucun travail déclaré sous ce nom"},
        409: {"description": "Le travail n'est pas abandonné"},
    },
)
def reprendre_un_travail(
    acces: AccesRequis, travail: str, demande: DemandeDExploitation
) -> EtatDUnTravail:
    """Le « geste d'exploitation » qu'exigeait l'abandon, et qui n'existait pas (pas 84).

    Le travail redevient dû tout de suite : le tour suivant, de la boucle de fond ou de
    `POST /orchestration/ordonnancement`, dira si la cause est corrigée. Le motif est
    écrit au journal avec l'auteur et le dernier échec.
    """
    exiger(acces, Permission.GERER_COMPTES)
    declare = next((t for t in travaux_declares() if t.nom == travail), None)
    if declare is None:
        raise HTTPException(status_code=404, detail=f"aucun travail déclaré sous « {travail} »")
    depot = _depot_des_passages()
    passage = depot.tous().get(travail, PassageOrdonnance(travail=travail))
    try:
        repris = passage.reprendre()
    except RepriseRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    depot.enregistrer(repris)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="orchestration.travail_repris",
        objet_type="travail",
        objet_id=travail,
        avant={
            "echecs_consecutifs": passage.echecs_consecutifs,
            "dernier_echec": passage.dernier_echec,
        },
        motif=demande.motif,
    )
    return _etat(declare, repris)


def tour_hors_requete() -> Tour:
    """Un tour complet sans requête HTTP : ouvre son unité de travail, et la valide.

    ─────────────────────────────────────────────────────────────────────────────
    C'EST LE POINT D'ENTRÉE DE LA BOUCLE DE FOND, ET LE SEUL

    La route, elle, s'exécute **dans** une unité de travail déjà ouverte par
    l'intergiciel : la transaction est la requête. La boucle n'a pas de requête,
    donc pas d'intergiciel, donc pas de session : elle doit ouvrir la sienne.

    ⚠️ **`unite_de_travail` plutôt qu'une session ouverte à la main.** Elle pose le
    cloisonnement sur le locataire, valide à la sortie normale, annule sur
    exception, et repose l'atelier dans la variable de contexte que tous les dépôts
    consultent. Ouvrir une session à la main ici referait ces quatre choses, moins
    bien, et divergerait le jour où l'une d'elles changerait.

    LE LOCATAIRE PAR DÉFAUT, ET CE QUE CELA VEUT DIRE

    Le tour tourne pour le locataire par défaut, c'est-à-dire le centre. C'est
    correct aujourd'hui parce que la boîte d'envoi qui compte est la sienne :
    l'ouverture d'un tenant est une saga **du centre**, pas du tenant qu'elle crée.

    ⚠️ Ce ne le restera pas. Le jour où un tenant entreprise produira ses propres
    événements, ce tour devra boucler sur les locataires actifs, et le verrou
    devra porter le nom du locataire. La forme est prête pour cela — le nom du
    verrou est déjà une variable — mais la boucle ne l'est pas, et le dire ici vaut
    mieux que le découvrir.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.transverse.adaptateurs.entrant.dependances import unite_de_travail
    from app.partage.locataire import etabli

    locataire = configuration().locataire_par_defaut
    # ⚠️ **`etabli` en plus de `unite_de_travail`, et la boucle l'a appris à ses
    # dépens.** L'intergiciel HTTP fait deux choses que l'on croyait n'en faire
    # qu'une : il ouvre l'unité de travail *et* il établit le locataire courant. La
    # première pose le cloisonnement dans la session ; la seconde alimente
    # `courant()`, que les dépôts lisent pour se construire.
    #
    # Le premier tour hors requête a échoué sur `LocataireNonEtabli`, exactement
    # comme il devait : le message dit qu'une lecture sans locataire servirait des
    # lignes arbitraires sans que rien ne le signale. C'est le refus qui a rendu la
    # couture visible, là où une valeur par défaut l'aurait laissée passer.
    with etabli(locataire), unite_de_travail(locataire):
        return _tour_verrouille(LOT_PAR_DEFAUT)
