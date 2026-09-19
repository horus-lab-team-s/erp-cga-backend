"""Contrats de domaine du contexte E · Comptabilité SYSCOHADA.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types,
  aucun service, aucune entrée-sortie. C'est la seule chose qu'une couche
  `domaine` d'un autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage**. Réservé aux couches `application`
  et `adaptateurs`.

Pourquoi les séparer : sans cela, une entité du contexte F · Obligations qui
importerait `api.py` tirerait transitivement la couche application de la
comptabilité. Le cercle interne dépendrait du cercle externe, ce que la Clean
Architecture interdit — et ce que `tests/test_architecture.py` refuse.

Les consommateurs prévus sont F · Obligations, qui lira les écritures pour
établir les déclarations, et H · Clôture, qui en tirera la balance puis la liasse.
"""

from __future__ import annotations

from app.contextes.comptabilite.domaine.entites import (
    AttributFiscal,
    Compte,
    DestinationCompte,
    EcritureComptable,
    EtatEcriture,
    Journal,
    LigneEcriture,
    NatureJournal,
    Sens,
    TypeEcriture,
)
from app.contextes.comptabilite.domaine.imputation import (
    PlanImputation,
    RegleImputation,
)

# ⚠️ Les **fonctions** de projection franchissent la frontière au même titre que
# leurs types. Elles sont pures, sans dépendance à la couche application, et un
# voisin qui reçoit un `SoldeCompte` sans pouvoir en contrôler l'équilibre devrait
# refaire le calcul chez lui — c'est-à-dire en écrire une seconde version qui
# divergerait de celle-ci au premier correctif.
from app.contextes.comptabilite.domaine.projections import (
    COMPTES_DU_CHIFFRE_D_AFFAIRES,
    LigneGrandLivre,
    SoldeCompte,
    TrouSequence,
    chiffre_affaires,
    controle_balance_equilibree,
    controle_bouclage,
    resultat,
    sequences_incompletes,
)

__all__ = [
    "AttributFiscal",
    "Compte",
    "DestinationCompte",
    "EcritureComptable",
    "EtatEcriture",
    "Journal",
    "LigneEcriture",
    "LigneGrandLivre",
    "NatureJournal",
    "PlanImputation",
    "RegleImputation",
    "Sens",
    "SoldeCompte",
    "TrouSequence",
    "COMPTES_DU_CHIFFRE_D_AFFAIRES",
    "chiffre_affaires",
    "controle_balance_equilibree",
    "controle_bouclage",
    "resultat",
    "sequences_incompletes",
    "TypeEcriture",
]
