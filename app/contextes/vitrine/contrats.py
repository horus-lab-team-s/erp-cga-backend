"""Contrats de domaine du contexte L · Vitrine publique.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types,
  aucun service, aucune entrée-sortie. C'est la seule chose qu'une couche
  `domaine` d'un autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage**. Réservé aux couches `application`
  et `adaptateurs`.

Pourquoi les séparer : sans cela, une entité d'un autre contexte important
`api.py` tirerait transitivement la couche application de la Vitrine. Le cercle
interne dépendrait du cercle externe, ce que la Clean Architecture interdit — et
ce que `tests/test_architecture.py` refuse.

En pratique, aucun contexte ne lit aujourd'hui la Vitrine, et c'est voulu : le
contenu éditorial n'a rien à dire au métier fiscal. La surface existe malgré tout,
parce que la discipline vaut d'être tenue avant qu'on en ait besoin, pas après.
"""

from __future__ import annotations

from app.contextes.vitrine.domaine.entites import (
    Annonce,
    AppelAction,
    Article,
    Bloc,
    BlocEncadre,
    BlocIntertitre,
    BlocListe,
    BlocParagraphe,
    Institution,
    Rubrique,
)

__all__ = [
    "Annonce",
    "AppelAction",
    "Article",
    "Bloc",
    "BlocEncadre",
    "BlocIntertitre",
    "BlocListe",
    "BlocParagraphe",
    "Institution",
    "Rubrique",
]
