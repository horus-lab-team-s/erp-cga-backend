"""La route du registre des services : qui existe, dans quel état, et qui dépend de qui.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI ELLE VIT DANS LE TRANSVERSE ET NON DANS UN CONTEXTE À ELLE

Le registre n'est le métier de personne, exactement comme la boîte d'envoi et les
sagas. Lui donner un quinzième contexte ferait un service dont le seul objet serait
de décrire les quatorze autres, donc un service qui devrait se décrire lui-même.

Il est monté sous `/transverse`, avec l'identité et le journal d'audit : ce sont
les surfaces d'administration de la plateforme.

CE QU'ELLE SERT, ET À QUI

À l'exploitant qui doit décider s'il faut réveiller quelqu'un. « Le Référentiel ne
répond plus » n'apprend rien ; « le Référentiel ne répond plus, et douze services
en dépendent, dont la Comptabilité et les Obligations » l'apprend.

Au développeur qui arrive sur le projet et veut voir la découpe sans lire quatorze
répertoires.

⚠️ **ELLE N'EST PAS PUBLIQUE.** Le graphe des dépendances d'une plateforme est une
carte de ses points de rupture : il dit quel service arrêter pour tout arrêter.
Elle demande `GERER_COMPTES`, comme les routes d'orchestration.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.contextes.transverse.adaptateurs.entrant.dependances import AccesRequis, exiger
from app.contextes.transverse.domaine.roles import Permission
from app.registre.etat import (
    EtatDeConstruction,
    EtatDExecution,
    Sonde,
    fiche_de,
    fiches,
    joignables_selon,
)
from app.registre.inscription import sondes_inscrites
from app.registre.services import SERVICES

routeur = APIRouter(prefix="/transverse", tags=["Technique"])


def sondes_du_processus() -> dict[str, Sonde]:
    """Les sondes que les services ont inscrites au démarrage.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Elles étaient écrites ici, et c'était une erreur qui ne se voyait pas.**

    Trois sondes vivaient dans ce module, dont celle du Référentiel — un contexte que
    le graphe autorise au Transverse, ce qui rendait la faute légale et invisible. La
    quatrième aurait été refusée par le test d'architecture, et l'on aurait découvert
    à ce moment-là qu'il fallait tout déplacer.

    Le sens est désormais celui des travaux et des abonnés : **le service déclare,
    le registre consulte.** Chaque sonde vit dans l'adaptateur entrant de son
    contexte, où elle a accès à ce qu'elle doit vérifier sans que personne n'importe
    personne.

    UNE SONDE DOIT ÊTRE BON MARCHÉ

    Le registre les appelle **toutes** à chaque consultation, et Consul interroge le
    chemin de santé de chaque service toutes les dix secondes. Une sonde qui
    compterait des lignes deviendrait elle-même une cause de panne, et le ferait au
    moment où l'on consulte le registre, c'est-à-dire pendant un incident.

    Celles qui existent lisent une configuration déjà en mémoire, ou un fichier de
    quelques kilo-octets. ⚠️ Ce n'est pas gratuit, et le jour où une sonde coûtera
    davantage il faudra mettre son verdict en cache avec une durée de validité.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return sondes_inscrites()


class FicheDeService(BaseModel):
    """Un service du registre, ses trois états et ce qui tombe avec lui.

    Pas 84 : modèle déclaré. La route rendait un dictionnaire libre, que l'outil de
    contrat des écrans ne pouvait pas vérifier ; les réponses ont été capturées avant
    et après le typage, et comparées au champ près.
    """

    lettre: str
    nom: str
    libelle: str
    plan: str
    objet: str
    prefixes: list[str]
    construction: str
    execution: str
    motif: str | None
    dependances: list[str]
    entraine: list[str]
    appuis_tombes: list[str]


class RegistreDesServices(BaseModel):
    services: list[FicheDeService]
    en_construction: list[str]
    en_difficulte: list[str]


class SanteDUnService(BaseModel):
    service: str
    etat: str
    #: Absent de la réponse quand il n'y a rien à dire (voir `response_model_exclude_none`).
    motif: str | None = None


@routeur.get(
    "/services",
    summary="Le registre des services : construction, exécution, dépendances",
    responses={403: {"description": "Geste réservé à l'administration"}},
)
def registre(requete: Request, acces: AccesRequis) -> RegistreDesServices:
    """Les quatorze services, leurs trois états, et ce qui tombe avec chacun.

    ⚠️ **Les trois états sont rendus séparément, jamais fondus en un seul.**

    Un champ unique mentirait dans les deux sens : un service complet dont la base
    est tombée serait « incomplet », et un service à peine commencé mais dont le
    processus tourne serait « opérationnel ». Le second est le plus coûteux, parce
    qu'il fait croire qu'une fonctionnalité existe.
    """
    exiger(acces, Permission.GERER_COMPTES)
    # Les chemins viennent du **schéma OpenAPI**, jamais de `app.routes` : cette
    # version de FastAPI conserve des enveloppes autour des routeurs inclus, et
    # parcourir `app.routes` rendrait zéro route de contexte. Voir
    # `joignables_selon`, où le piège est écrit.
    joignables = joignables_selon(requete.app.openapi()["paths"])
    inventaire = fiches(sondes_du_processus(), joignables)

    return RegistreDesServices(
        services=[
            FicheDeService(
                lettre=f.service.lettre,
                nom=f.service.nom,
                libelle=f.service.libelle,
                plan=f.service.plan,
                objet=f.service.objet,
                prefixes=list(f.service.prefixes),
                construction=f.construction.value,
                execution=f.execution.etat.value,
                motif=f.execution.motif,
                dependances=list(f.dependances),
                entraine=list(f.entraine),
                appuis_tombes=list(f.appuis_tombes),
            )
            for f in inventaire
        ],
        # Deux compteurs, parce que ce sont les deux questions qu'on pose en
        # arrivant : « où en est la construction » et « qu'est-ce qui ne va pas ».
        en_construction=[
            f.service.nom
            for f in inventaire
            if f.construction is not EtatDeConstruction.EN_SERVICE
        ],
        en_difficulte=[f.service.nom for f in inventaire if f.a_un_probleme],
    )


# ── Les sources de la recherche globale (pas 93) ─────────────────────────────


class SourceOuverte(BaseModel):
    """Une source que la session peut interroger, telle que l'écran de recherche la lit."""

    service: str
    libelle: str
    chemin: str


@routeur.get(
    "/services/recherche",
    summary="Les sources de la recherche globale ouvertes à la session",
)
def sources_de_recherche(requete: Request, acces: AccesRequis) -> list[SourceOuverte]:
    """Les routes de recherche déclarées au registre, **que cette session peut appeler**.

    ─────────────────────────────────────────────────────────────────────────────
    DEUX FILTRES, ET CHACUN ÉVITE UN ÉCRAN FAUX

    * **La permission** : un comptable ne reçoit pas la source « Comptes du cabinet ».
      L'appeler rendrait 403, et l'écran afficherait une erreur sur une recherche que
      personne ne lui a proposée. Ce n'est pas une protection (chaque route refuse
      elle-même), c'est ne pas proposer ce qui sera refusé.
    * **La joignabilité** : une source déclarée dont la route n'est montée nulle part
      (service retiré du déploiement) n'est pas rendue. Voir `joignables_selon`.

    Ouverte à toute session : ce qu'elle révèle (la liste des sources et leurs
    chemins) est déjà dans le schéma de l'API, et chaque source contrôle son périmètre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    chemins = set(requete.app.openapi()["paths"])
    ouvertes = []
    for service in SERVICES:
        source = service.recherche
        if source is None or source.chemin not in chemins:
            continue
        if source.permission not in Permission.__members__:
            continue
        if not acces.detient(Permission(source.permission)):
            continue
        ouvertes.append(
            SourceOuverte(service=service.nom, libelle=source.libelle, chemin=source.chemin)
        )
    return ouvertes


# ── Le contrôle de santé par service, celui que Consul interroge ─────────────

#: ⚠️ **`429` n'est pas employé ici pour ce qu'il veut dire en HTTP.**
#:
#: Consul traduit les codes de statut de ses contrôles HTTP en trois niveaux, et un
#: seul code déclenche le niveau intermédiaire : `2xx` vaut *passing*, **`429` vaut
#: *warning***, tout le reste vaut *critical*. Ce n'est pas un choix de ce projet,
#: c'est la convention de Consul, et s'en écarter ferait qu'un service suspect serait
#: rangé parmi les services morts.
#:
#: La coïncidence est heureuse et n'en est pas une : le registre a trois niveaux
#: parce qu'un état d'exécution en a naturellement trois, et Consul a fait le même
#: constat avant nous.
CODE_SUSPECT = 429


@routeur.get(
    "/services/{nom}/sante",
    summary="Contrôle de santé d'un seul service, au format attendu par Consul",
    responses={
        200: {"description": "Le service répond"},
        404: {"description": "Aucun service de ce nom"},
        429: {"description": "Signal à regarder, sans certitude de panne"},
        503: {"description": "Le service ne peut pas travailler"},
    },
    # ⚠️ Le motif n'apparaissait que s'il existait : sans cette option, le modèle
    # ajouterait `"motif": null` à chaque contrôle, et la réponse aurait changé.
    response_model_exclude_none=True,
)
def sante_d_un_service(nom: str, reponse: Response, acces: AccesRequis) -> SanteDUnService:
    """L'état d'un seul service, en un code de statut.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI UN CHEMIN PAR SERVICE ET NON LA SONDE GLOBALE

    Une sonde unique ferait tomber ou tenir les quatorze ensemble, ce qui est
    précisément l'information qu'on ne veut pas. Savoir que « la plateforme est en
    panne » n'aide personne à décider quoi redémarrer ; savoir que le Référentiel
    est critique et que douze services en dépendent le décide.

    ⚠️ **ELLE NE REND NI LE GRAPHE NI LES APPUIS TOMBÉS.**

    Un contrôle de santé est appelé toutes les dix secondes, par un agent qui n'a
    besoin que d'un code. Lui rendre la liste de ce qui tombe avec ce service serait
    du gaspillage à chaque appel — et surtout, cette liste est une carte des points
    de rupture de la plateforme. Elle vit sur `GET /transverse/services`, sous la
    même permission, appelée quand un humain la demande.

    ⚠️ **`SANS_SONDE` rend `200`, et il faut le dire.**

    Trois services sur quatorze n'ont pas de sonde, et ce n'est pas un retard : ils
    n'ont aucune ressource à eux, et relire celle d'un autre créerait deux vérités sur
    une même question. Les rendre critiques afficherait un tableau Consul rouge en
    permanence, et un tableau rouge en permanence n'est plus lu. Le corps porte l'état
    exact : `SANS_SONDE` n'est pas `REPOND`, et qui veut la nuance l'a.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.GERER_COMPTES)
    try:
        fiche = fiche_de(nom, sondes_du_processus())
    except KeyError:
        # 404 et non 400 : la règle du projet est qu'une ressource hors périmètre
        # n'apprend rien de son existence. Ici elle n'existe simplement pas.
        raise HTTPException(status_code=404, detail=f"aucun service « {nom} »") from None

    if fiche.execution.etat is EtatDExecution.EN_PANNE:
        reponse.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif fiche.execution.etat is EtatDExecution.SUSPECT:
        reponse.status_code = CODE_SUSPECT

    return SanteDUnService(
        service=nom, etat=fiche.execution.etat.value, motif=fiche.execution.motif or None
    )
