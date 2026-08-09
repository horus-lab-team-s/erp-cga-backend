"""Surface publique du contexte A · Référentiel normatif.

**Les autres contextes n'importent QUE ce module.** Jamais `service`, jamais
`modeles`, jamais un sous-module interne. C'est cette règle — vérifiée par
`tests/test_architecture.py` — qui permet de réorganiser l'intérieur d'un contexte
sans casser les dix autres.

Ce que le référentiel promet aux autres contextes :

* une lecture de paramètre **à une date**, jamais « la valeur courante » ;
* un `ParametreResolu` qui porte sa valeur, sa date d'effet, son fondement légal et
  son statut de validation, afin que l'appelant puisse le consigner dans ses propres
  traces ;
* une erreur explicite quand la valeur manque, jamais un défaut silencieux.
"""

from __future__ import annotations

from .modeles import (
    Fondement,
    Parametre,
    ParametreResolu,
    StatutValidation,
    Unite,
    VersionParametre,
)
from .service import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
    charger_parametres,
)

__all__ = [
    # Lecture
    "ServiceParametres",
    "charger_parametres",
    # Types échangés
    "Fondement",
    "Parametre",
    "ParametreResolu",
    "StatutValidation",
    "Unite",
    "VersionParametre",
    # Erreurs — à traiter, jamais à ignorer
    "AucuneVersionApplicable",
    "ParametreInconnu",
]
