"""Les trois états d'un service, et pourquoi les confondre est le piège.

─────────────────────────────────────────────────────────────────────────────────
« L'ÉTAT DU SERVICE » N'EST PAS UNE QUESTION, C'EN EST TROIS

**L'état de construction** répond à « est-ce écrit ? ». Il change quand quelqu'un
livre du code, c'est-à-dire quelques fois par semaine.

**L'état d'exécution** répond à « répond-il maintenant ? ». Il change quand une
base tombe, c'est-à-dire à l'improviste et pour quelques minutes.

**L'état de dépendance** répond à « qu'est-ce qui tombe avec lui ? ». Il ne change
qu'avec le graphe, c'est-à-dire quelques fois par an.

⚠️ Les mêler en un seul champ produit un registre qui ment dans les deux sens. Un
service complet dont la base est tombée serait « incomplet ». Un service à peine
commencé mais dont le processus tourne serait « opérationnel ». Et le second est le
plus coûteux : c'est celui qui fait croire qu'une fonctionnalité existe.

CE QUI EST CONSTATÉ, ET CE QUI EST DÉCLARÉ

L'état de construction est **constaté sur le système de fichiers**. Il aurait été
plus simple de l'écrire à la main dans la déclaration — et il aurait dérivé, comme
a dérivé le tableau « ce qui tourne aujourd'hui » du document de conception, qui
était de la prose recopiée. Un service dont on retire les routes redevient
automatiquement « en construction », sans que personne y pense.

L'état d'exécution est **constaté par une sonde**, quand le service en fournit une.
Un service sans sonde le dit — `SANS_SONDE` — plutôt que de se déclarer sain. Un
registre qui répond « tout va bien » pour un service qu'il n'interroge pas est
exactement la sonde complaisante que le pas 12 a supprimée.

L'état de dépendance est **calculé sur le graphe**, qui est la seule chose ici qui
soit déclarée à la main. Il l'est parce qu'un graphe de dépendances métier ne se
devine pas : l'ordre des imports dit ce que le code fait, pas ce qu'il a le droit
de faire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.registre.services import SERVICES, Service, autorises_pour, qui_tombe_avec

__all__ = [
    "MODULES_QUI_N_OUVRENT_RIEN",
    "CONTEXTES_DIR",
    "joignables_selon",
    "EtatDeConstruction",
    "EtatDExecution",
    "FicheDeService",
    "Sonde",
    "Verdict",
    "construction_de",
    "fiche_de",
    "fiches",
]

#: La racine des services sur le disque. Remontée depuis ce fichier plutôt que
#: reçue en configuration : le registre décrit **ce dépôt**, et un chemin
#: configurable laisserait croire qu'il pourrait en décrire un autre.
CONTEXTES_DIR = Path(__file__).resolve().parents[1] / "contextes"


class EtatDeConstruction(StrEnum):
    """Jusqu'où le service est écrit. Constaté, jamais déclaré.

    ⚠️ Les valeurs sont ordonnées du moins au plus avancé, et l'ordre compte : un
    service ne peut atteindre un rang qu'en ayant les précédents. Un service qui
    exposerait des routes sans domaine ne serait pas « en service », il serait une
    façade sur du vide.
    """

    #: Le répertoire existe, et c'est tout. Une place réservée.
    DECLARE = "DECLARE"
    #: Des règles sont écrites. Le service sait quelque chose, personne ne peut
    #: encore le lui demander.
    DOMAINE = "DOMAINE"
    #: Des cas d'usage existent. Le service sait faire, il n'est pas joignable.
    CAS_D_USAGE = "CAS_D_USAGE"
    #: Des routes sont exposées. Le service est joignable.
    EN_SERVICE = "EN_SERVICE"


class EtatDExecution(StrEnum):
    """Répond-il maintenant ?

    ⚠️ **Quatre valeurs, et trois sont des nuances de « pas sûr ».** C'est voulu :
    ramener une sonde à sain/cassé oblige à trancher des cas qui ne se tranchent
    pas, et le tranchage se fait toujours du mauvais côté.
    """

    #: Sa sonde a répondu, rien à signaler.
    REPOND = "REPOND"
    #: Sa sonde a levé, ou a déclaré une panne. Le service ne peut pas travailler.
    EN_PANNE = "EN_PANNE"
    #: ⚠️ **Quelque chose mérite un regard, sans être certainement cassé.**
    #:
    #: Ce niveau existe parce qu'il manquait, et le manque s'est vu tout de suite.
    #: La sonde des Tenants signale un répertoire vide — ce qui trahit un garnissage
    #: échoué neuf fois sur dix, et qui est parfaitement normal sur une installation
    #: neuve. Rendue `EN_PANNE`, elle peignait **treize services en rouge** sur une
    #: base vierge.
    #:
    #: Une sonde qui crie au loup finit ignorée, et le jour où elle a raison
    #: personne ne regarde. Un service `SUSPECT` n'entraîne donc aucun appui tombé :
    #: il est signalé, il n'alarme pas.
    SUSPECT = "SUSPECT"
    #: ⚠️ **Il n'en fournit aucune.** Distinct de `REPOND` à dessein : un registre
    #: qui répond « tout va bien » pour un service qu'il n'interroge pas ment.
    SANS_SONDE = "SANS_SONDE"


class Verdict(BaseModel):
    """L'état d'exécution d'un service, et son motif s'il y en a un."""

    model_config = ConfigDict(frozen=True)

    etat: EtatDExecution
    motif: str | None = None

    @classmethod
    def panne(cls, motif: str) -> Verdict:
        """Le service ne peut pas travailler. Ses dépendants seront marqués."""
        return cls(etat=EtatDExecution.EN_PANNE, motif=motif)

    @classmethod
    def suspect(cls, motif: str) -> Verdict:
        """Quelque chose mérite un regard, sans certitude. N'alarme personne d'autre.

        ⚠️ À employer dès que le constat a une explication innocente plausible. Une
        sonde qui hésite entre les deux doit choisir celui-ci : le coût d'un
        `SUSPECT` qui était une panne est qu'on la voit une consultation plus tard ;
        le coût d'une `panne` qui n'en était pas une est qu'on cesse de lire les
        alertes.
        """
        return cls(etat=EtatDExecution.SUSPECT, motif=motif)


#: Une sonde de service. Rend `None` quand tout va bien, sinon un verdict.
#:
#: ⚠️ Elle **rend** son verdict plutôt que de lever, contrairement aux exécutants de
#: l'ordonnanceur. La différence tient au propos : un exécutant qui lève dit « ce
#: travail a échoué », ce qui est exceptionnel ; une sonde qui trouve une panne fait
#: exactement son métier, et une exception serait ici le cas nominal.
#:
#: Elle doit être **bon marché**. Le registre les appelle toutes à chaque
#: consultation, et une sonde qui compte des lignes deviendrait elle-même une cause
#: de panne sous charge — au moment précis où l'on consulte le registre.
Sonde = Callable[[], "Verdict | None"]


class FicheDeService(BaseModel):
    """Tout ce que le registre sait d'un service, les trois états compris."""

    model_config = ConfigDict(frozen=True)

    service: Service
    construction: EtatDeConstruction
    execution: Verdict
    #: Ce dont il a le droit de dépendre, socle compris.
    dependances: tuple[str, ...] = ()
    #: Ce qui cesse de fonctionner s'il s'arrête. La question qui décide s'il faut
    #: réveiller quelqu'un.
    entraine: tuple[str, ...] = ()
    #: Les dépendances dont la sonde est **en panne**. ⚠️ Un service dont la sonde
    #: répond mais dont un appui est tombé n'est **pas** sain, et le dire seulement
    #: sur l'appui laisserait chercher longtemps.
    #:
    #: ⚠️ Un appui `SUSPECT` ne compte pas. Sans cette distinction, une sonde qui
    #: hésite entraînerait tous ses dépendants dans son hésitation, et le registre
    #: rendrait treize services en difficulté sur une installation neuve.
    appuis_tombes: tuple[str, ...] = ()

    @property
    def joignable(self) -> bool:
        return self.construction is EtatDeConstruction.EN_SERVICE

    @property
    def a_un_probleme(self) -> bool:
        """⚠️ Un appui tombé compte comme un problème, même sonde au vert.

        C'est ce qui distingue un registre d'une liste de sondes : la sonde du
        service dit qu'il tourne, le registre dit qu'il ne peut rien faire d'utile.
        """
        return self.execution.etat is EtatDExecution.EN_PANNE or bool(self.appuis_tombes)


#: Les modules qui vivent dans une couche sans en être : ils n'ouvrent aucune porte.
#:
#: ⚠️ **`sonde.py` n'est pas un adaptateur entrant au sens du registre.** Une sonde est
#: appelée par l'exploitation, jamais par un utilisateur, et elle ne rend joignable
#: aucune fonctionnalité. La compter ferait qu'un service doté d'une sonde et d'aucune
#: route passerait pour `EN_SERVICE` : le registre annoncerait une façade sur du vide,
#: qui est le plus coûteux des deux mensonges possibles. C'est le cas du contexte N
#: depuis le pas 121.
MODULES_QUI_N_OUVRENT_RIEN = frozenset({"sonde.py"})


def _modules(service: Service, *couche: str) -> int:
    """Le nombre de modules Python d'une couche, hors `__init__`, cache et sondes.

    ⚠️ `__init__.py` est exclu : le paquet en porte un dès sa création, et le
    compter ferait qu'un répertoire vide passerait pour un domaine écrit.
    """
    racine = CONTEXTES_DIR / service.nom
    for partie in couche:
        racine = racine / partie
    if not racine.is_dir():
        return 0
    return len(
        [
            f
            for f in racine.rglob("*.py")
            if f.name != "__init__.py"
            and f.name not in MODULES_QUI_N_OUVRENT_RIEN
            and "__pycache__" not in f.parts
        ]
    )


def construction_de(
    service: Service, *, joignable: bool | None = None
) -> EtatDeConstruction:
    """Jusqu'où ce service est écrit, constaté sur le disque.

    ─────────────────────────────────────────────────────────────────────────────
    `joignable` permet de constater les routes sur **l'application montée** plutôt
    que sur le disque, et c'est nettement plus juste : un `routes_http.py` présent
    mais dont le routeur n'est monté nulle part ne rend le service joignable par
    personne. Le compter mentirait exactement là où le registre doit dire vrai.

    Sans cet argument, la présence du fichier fait foi. C'est le repli employé
    quand l'application n'est pas montée : un test unitaire, un outil en ligne de
    commande, ou la fiche demandée avant le démarrage.

    ⚠️ **`EN_SERVICE` exige aussi un domaine.** Des routes sans règles derrière
    elles ne sont pas un service, c'est une façade sur du vide, et l'annoncer « en
    service » ferait croire qu'une fonctionnalité existe. C'est le plus coûteux des
    deux mensonges possibles.
    ─────────────────────────────────────────────────────────────────────────────
    """
    a_des_routes = (
        joignable
        if joignable is not None
        else _modules(service, "adaptateurs", "entrant") > 0
    )
    if a_des_routes and _modules(service, "domaine") > 0:
        return EtatDeConstruction.EN_SERVICE
    if _modules(service, "application") > 0:
        return EtatDeConstruction.CAS_D_USAGE
    if _modules(service, "domaine") > 0:
        return EtatDeConstruction.DOMAINE
    return EtatDeConstruction.DECLARE


def joignables_selon(chemins: Iterable[str]) -> dict[str, bool]:
    """Quels services répondent réellement, d'après les chemins exposés.

    ─────────────────────────────────────────────────────────────────────────────
    L'ARGUMENT EST LA LISTE DES CHEMINS, ET NON L'APPLICATION

    Passer l'application ferait dépendre le registre de FastAPI, donc du cadre web,
    pour répondre à une question qui n'en relève pas. Il ne connaît que des chaînes.

    ⚠️ **La liste des chemins doit venir du schéma OpenAPI**, jamais de `app.routes`.
    Cette version de FastAPI conserve des enveloppes `_IncludedRouter` autour des
    routeurs inclus : parcourir `app.routes` rend zéro route de contexte, ce qui
    ferait déclarer les quatorze services non joignables. Le piège a déjà coûté un
    comptage faux au pas 8, et il est écrit ici pour ne pas le payer deux fois.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exposes = list(chemins)
    return {
        s.nom: any(
            chemin == prefixe or chemin.startswith(prefixe + "/")
            for prefixe in s.prefixes
            for chemin in exposes
        )
        for s in SERVICES
    }


def _sonder(nom: str, sondes: Mapping[str, Sonde]) -> Verdict:
    sonde = sondes.get(nom)
    if sonde is None:
        return Verdict(etat=EtatDExecution.SANS_SONDE)
    try:
        verdict = sonde()
    except Exception as panne:  # noqa: BLE001 — une sonde qui lève est une panne, pas un incident
        # ⚠️ Rattrapé ici, et non laissé remonter. Une sonde mal écrite ferait
        # échouer la consultation du registre **entier**, c'est-à-dire priverait
        # l'exploitant de l'outil au moment précis où il en a besoin.
        #
        # Ce n'est pas théorique : la première rédaction de la sonde des Tenants
        # appelait une méthode inexistante. Le registre l'a marquée en panne et a
        # rendu les treize autres fiches, au lieu de rendre une pile d'appels.
        return Verdict.panne(f"{type(panne).__name__} : {panne}")
    return verdict or Verdict(etat=EtatDExecution.REPOND)


def fiches(
    sondes: Mapping[str, Sonde] | None = None,
    joignables: Mapping[str, bool] | None = None,
) -> list[FicheDeService]:
    """La fiche de chaque service déclaré, dans l'ordre des lettres.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LES SONDES SONT APPELÉES UNE FOIS CHACUNE, ET AVANT LE RESTE.**

    Le calcul des appuis tombés a besoin du verdict de tous les autres services :
    les sonder au fil de la construction des fiches ferait appeler la sonde du
    Référentiel une fois par service qui en dépend, soit douze fois par
    consultation. À trois sondes c'est invisible ; à trente, la page de diagnostic
    devient elle-même une charge.
    ─────────────────────────────────────────────────────────────────────────────
    """
    sondes = sondes or {}
    joignables = joignables or {}
    verdicts = {s.nom: _sonder(s.nom, sondes) for s in SERVICES}

    resultat = []
    for service in SERVICES:
        dependances = sorted(autorises_pour(service.nom))
        resultat.append(
            FicheDeService(
                service=service,
                construction=construction_de(
                    service, joignable=joignables.get(service.nom)
                ),
                execution=verdicts[service.nom],
                dependances=tuple(dependances),
                entraine=tuple(sorted(qui_tombe_avec(service.nom))),
                appuis_tombes=tuple(
                    nom
                    for nom in dependances
                    if verdicts[nom].etat is EtatDExecution.EN_PANNE
                ),
            )
        )
    return resultat


def fiche_de(
    nom: str,
    sondes: Mapping[str, Sonde] | None = None,
    joignables: Mapping[str, bool] | None = None,
) -> FicheDeService:
    """La fiche d'un service. Lève sur un nom inconnu, comme `service`."""
    for fiche in fiches(sondes, joignables):
        if fiche.service.nom == nom:
            return fiche
    raise KeyError(f"service inconnu : « {nom} »")


#: Le nombre de services dont la construction n'est pas terminée. Sert à la sonde
#: de santé, qui n'a pas la place d'afficher quatorze fiches.
def en_construction(fiches_: list[FicheDeService]) -> list[str]:
    return [
        f.service.nom for f in fiches_ if f.construction is not EtatDeConstruction.EN_SERVICE
    ]
