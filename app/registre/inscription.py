"""Où chaque service inscrit sa sonde, sans que le registre ne l'importe.

─────────────────────────────────────────────────────────────────────────────────
LA TROISIÈME FOIS QUE CE SENS S'INVERSE, ET LA MÊME RAISON

Les travaux périodiques s'inscrivent, les abonnés aux événements s'inscrivent, et les
sondes de santé font de même.

Le motif n'a pas changé : le registre vit dans `app/registre/`, que le test
d'architecture empêche d'importer **aucun** contexte métier. La contrainte n'est pas
formelle. Un registre qui importerait chaque service pour le sonder deviendrait le
point par lequel tout se charge : consulter l'état de la plateforme demanderait de
monter la plateforme entière, et l'on veut précisément pouvoir le consulter **quand la
plateforme va mal**.

⚠️ **CE MODULE NE CONNAÎT AUCUN SERVICE.** Il ne stocke que des noms et des fonctions.

QUI INSCRIT

La composition de l'application, dans `app/main.py`. C'est le seul endroit qui a le
droit de connaître tous les services, parce que c'est son métier de les assembler.

⚠️ **Une sonde par service, et la réinscription remplace.** Contrairement aux abonnés,
où plusieurs écoutes du même événement sont légitimes, deux sondes pour un service
poseraient une question sans réponse : laquelle fait foi quand elles divergent ?
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.registre.etat import Sonde

__all__ = ["inscrire_une_sonde", "oublier_les_sondes", "sondes_inscrites"]

_SONDES: dict[str, Sonde] = {}


def inscrire_une_sonde(service: str, sonde: Sonde) -> None:
    """Inscrit la sonde d'un service. Rejouable : la dernière remplace.

    ⚠️ Le nom du service n'est **pas vérifié ici**. Ce module ne connaît pas la liste
    des services, et aller la chercher lui ferait importer la déclaration, donc créer
    le cycle qu'on évite. C'est le test d'architecture qui garde la correspondance,
    et il le fait mieux : à la relecture, sur le graphe entier.
    """
    _SONDES[service] = sonde


def sondes_inscrites() -> dict[str, Sonde]:
    return dict(_SONDES)


def oublier_les_sondes() -> None:
    """Repart d'un registre sans sonde. Destiné aux tests."""
    _SONDES.clear()
