"""Où chaque service inscrit son travail périodique, sans que personne ne l'importe.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME QUE CE MODULE RÉSOUT, ET COMMENT IL S'EST PRÉSENTÉ

Le balayage de relance appartient à la Souscription. L'ordonnanceur vit dans le
Transverse. La première tentative de branchement a fait importer la Souscription par
le Transverse, et **le test d'architecture l'a refusée** : le graphe n'autorise au
Transverse que le Référentiel et les Tenants.

Il avait raison, et pas pour une raison de forme. Un ordonnanceur qui importe chaque
service dont il fait tourner un travail devient un point de couplage central : il
faut le modifier pour ajouter un travail, il traîne au démarrage tout ce que ces
services traînent, et le jour où l'un part vivre ailleurs il faut le découdre.

LE SENS EST INVERSÉ : LE SERVICE S'INSCRIT, L'ORDONNANCEUR CONSULTE

C'est exactement ce que fait déjà le registre pour les sondes, et pour la même
raison. Un service sait ce qu'il a à faire tourner ; l'ordonnanceur sait seulement
tenir un rythme.

⚠️ **CE MODULE NE CONNAÎT AUCUN SERVICE.** Il ne stocke que des noms, des cadences
et des fonctions. C'est ce qui lui permet de vivre dans `app/orchestration/`, où le
test d'architecture interdit tout import de contexte métier.

QUI INSCRIT, ET QUAND

La composition de l'application, dans `app/main.py`. C'est le seul endroit qui a le
droit de connaître tous les services, parce que c'est son métier de les assembler.

⚠️ **L'inscription est idempotente**, et il le faut : `creer_application()` est
appelé plusieurs fois dans une même exécution de la suite de tests, et une
inscription qui empilerait produirait trois fois le même travail, donc trois
balayages par tour.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable

from app.orchestration.ordonnanceur import Travail
from app.orchestration.relais import Abonne
from app.orchestration.tour import Executant

#: Ce qui rend un abonné neuf, sur la session du moment. Voir `_ABONNES`.
FabriqueDAbonne = Callable[[], Abonne]

__all__ = [
    "abonnes_inscrits",
    "executants_inscrits",
    "inscrire_un_abonne",
    "inscrire_un_travail",
    "oublier_les_travaux",
    "travaux_inscrits",
]

#: L'ordre d'inscription est l'ordre d'exécution. Voir `travaux_dus` : le relais
#: passe avant le balayage parce qu'il est le chemin vital, et un dictionnaire
#: préserve l'ordre d'insertion depuis Python 3.7.
_TRAVAUX: dict[str, tuple[Travail, Executant]] = {}


def inscrire_un_travail(travail: Travail, executant: Executant) -> None:
    """Inscrit un travail et ce qui le fait tourner. Rejouable sans dommage.

    ⚠️ **Les deux ensemble, jamais l'un sans l'autre.** Un travail inscrit sans
    exécutant serait signalé par `faire_un_tour` comme un défaut de branchement, ce
    qu'il serait — et ce module peut simplement l'empêcher plutôt que le détecter.

    ⚠️ **Réinscrire remplace, et n'empile pas.** `creer_application()` est appelé
    plusieurs fois dans une même exécution de tests ; empiler produirait trois fois
    le même travail, donc trois balayages par tour et trois relances au même client.
    """
    _TRAVAUX[travail.nom] = (travail, executant)


def travaux_inscrits() -> list[Travail]:
    """Les travaux, dans l'ordre où ils ont été inscrits."""
    return [travail for travail, _ in _TRAVAUX.values()]


def executants_inscrits() -> dict[str, Executant]:
    return {nom: executant for nom, (_, executant) in _TRAVAUX.items()}


def oublier_les_travaux() -> None:
    """Repart d'un ordonnanceur sans travail ni abonné. Destiné aux tests.

    Nommé plutôt que laissé à un accès direct aux dictionnaires : un test qui vide
    l'état d'un module par la porte de derrière cesse de fonctionner à la première
    refonte, et personne ne comprend pourquoi.
    """
    _TRAVAUX.clear()
    _ABONNES.clear()


# ── Les abonnés aux événements ────────────────────────────────────────────────
#
# Même inversion, et pour la même raison. Le balayage de relance dépose des
# événements `RelanceDue` ; celui qui les postera vit dans la Souscription, et le
# relais vit dans le Transverse, à qui le graphe interdit de lire la Souscription.

#: ⚠️ **Des fabriques, et non des abonnés.**
#:
#: Un abonné construit ses dépôts sur la session de la requête ou du tour en cours.
#: Inscrire l'objet au démarrage conserverait une session déjà fermée, panne qui
#: n'apparaîtrait qu'au second événement traité. La fabrique est appelée quand
#: l'atelier se monte, et rend un abonné neuf sur la session du moment.
_ABONNES: dict[str, list[FabriqueDAbonne]] = {}


def inscrire_un_abonne(nom_evenement: str, fabrique: FabriqueDAbonne) -> None:
    """Inscrit une fabrique d'abonné pour un nom d'événement.

    ⚠️ **Plusieurs abonnés par événement sont admis**, contrairement aux travaux où
    un nom vaut identité. Un `TenantOuvert` intéresse légitimement le
    provisionnement et, demain, la facturation : refuser le second obligerait à les
    tordre en un seul abonné qui ferait deux choses.

    ⚠️ **Mais la même fabrique n'est inscrite qu'une fois.** `creer_application()`
    est appelé plusieurs fois dans une exécution de tests, et empiler produirait
    trois envois du même message au même client.
    """
    inscrits = _ABONNES.setdefault(nom_evenement, [])
    if fabrique not in inscrits:
        inscrits.append(fabrique)


def abonnes_inscrits() -> dict[str, list[FabriqueDAbonne]]:
    return {nom: list(fabriques) for nom, fabriques in _ABONNES.items()}
