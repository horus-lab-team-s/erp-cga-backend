"""Contrats de domaine du contexte A · Référentiel normatif.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types, aucun
  service, aucune entrée-sortie. C'est la seule chose qu'une couche `domaine` d'un
  autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage** — `ServiceParametres` et ses erreurs.
  Réservé aux couches `application` et `adaptateurs`.

Pourquoi les séparer : sans cela, une entité de la Conformité important `api.py`
tirerait transitivement la couche application du Référentiel. Le cercle interne
dépendrait du cercle externe, ce que la Clean Architecture interdit — et ce que
`tests/test_architecture.py` refuse désormais.
"""

from __future__ import annotations

from app.contextes.referentiel.domaine.entites import (
    Bareme,
    BaremeResolu,
    Borne,
    Fondement,
    NatureParametre,
    Parametre,
    ParametreResolu,
    SeuilSansBorne,
    StatutValidation,
    TrancheBareme,
    Unite,
    VersionBareme,
    VersionParametre,
    seuil_atteint,
)

__all__ = [
    "Borne",
    "SeuilSansBorne",
    "seuil_atteint",
    "VersionBareme",
    "TrancheBareme",
    "BaremeResolu",
    "Bareme",
    "Fondement",
    "NatureParametre",
    "Parametre",
    "ParametreResolu",
    "StatutValidation",
    "Unite",
    "VersionParametre",
]
