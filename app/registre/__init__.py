"""Le registre des services : qui existe, dans quel état, et qui dépend de qui.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE PAQUET EXISTE

La plateforme est un **monolithe modulaire** destiné à se découper en services.
Quatorze contextes bornés, un graphe de dépendances établi flux par flux, et la
règle qu'aucun ne lit la base d'un autre. Cette découpe est ce qui rendra
l'extraction possible le jour où un service devra vivre à part.

Elle était **déclarée dans un fichier de test**.

Le graphe y était tenu — le test échouait à la moindre arête interdite — mais rien
ne pouvait le lire à l'exécution. Aucune route ne savait dire quels services
existent, aucun tableau de bord ne savait répondre à « si le Référentiel tombe,
qui s'arrête ? », et le tableau « ce qui tourne aujourd'hui » du document de
conception était de la **prose recopiée à la main**, qui dérivait dès qu'on
oubliait de la mettre à jour.

⚠️ C'est la même famille de défaut que les deux pas précédents : quelque chose de
juste sur le papier, jamais confronté à ce que la machine en fait.

CE QUE CE PAQUET NE FAIT PAS

Il ne découvre rien. Un registre de découverte — celui qui apprend à l'exécution
qu'une instance vient de démarrer sur tel port — n'a de sens que lorsque les
services sont réellement séparés et déployés indépendamment. Ils ne le sont pas.

Ici, la liste est **déclarée**, et chaque état est **constaté** : par le système de
fichiers pour l'état de construction, par une sonde pour l'état d'exécution, par le
graphe pour l'état de dépendance. Le jour où un service partira vivre ailleurs,
cette déclaration deviendra sa fiche d'inscription, et la découverte s'ajoutera
sans rien retirer.

⚠️ IL NE DÉPEND DE RIEN, ET C'EST STRUCTURANT

Le registre est lu par le test d'architecture, par la sonde, et par une route. Lui
laisser importer un contexte métier créerait un cycle : le contexte serait décrit
par un registre qui a besoin de lui pour se charger. Il ne connaît que des noms.
─────────────────────────────────────────────────────────────────────────────────
"""

from app.registre.inscription import (
    inscrire_une_sonde,
    oublier_les_sondes,
    sondes_inscrites,
)
from app.registre.services import (
    ARETES_AUTORISEES,
    SERVICES,
    SOCLE,
    Service,
    autorises_pour,
    dependances_transitives,
    qui_tombe_avec,
    service,
)

__all__ = [
    "ARETES_AUTORISEES",
    "SERVICES",
    "SOCLE",
    "Service",
    "autorises_pour",
    "inscrire_une_sonde",
    "oublier_les_sondes",
    "sondes_inscrites",
    "dependances_transitives",
    "qui_tombe_avec",
    "service",
]
