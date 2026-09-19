"""Contrats de domaine du contexte B · Portefeuille adhérents.

**Deux surfaces publiques, pas une.**

* `contrats.py` — ce module — n'expose que des **entités pures** : des types,
  aucun service, aucune entrée-sortie. C'est la seule chose qu'une couche
  `domaine` d'un autre contexte a le droit d'importer.
* `api.py` expose en plus les **cas d'usage**. Réservé aux couches `application`
  et `adaptateurs`.

Le contexte B est lu par presque tous les autres — D pour le régime, E pour
l'assujettissement, F pour le rattachement et l'exercice, H pour l'adhésion.
C'est donc la surface la plus sollicitée du système, et celle qu'il faut garder
la plus stable.
"""

from __future__ import annotations

from app.contextes.portefeuille.domaine.entites import (
    Adhesion,
    Associe,
    CentreRattachement,
    Dirigeant,
    Entreprise,
    Exercice,
    FormeJuridique,
    MandatDeclaratif,
    RegimeFiscal,
    StatutIntrouvable,
    StatutRattachement,
    StatutRegime,
    Tiers,
    TypeTiers,
)

# ⚠️ Le diagnostic de seuil franchit la frontière **avec ses fonctions**, comme
# les projections comptables. Un voisin qui recevrait `Entreprise` sans pouvoir
# la comparer à un seuil devrait réécrire la comparaison chez lui : deux règles
# de franchissement au lieu d'une, et c'est la plus laxiste qui ferait loi.
from app.contextes.portefeuille.domaine.regimes import (
    PALIER_ALERTE,
    DiagnosticSeuil,
    diagnostiquer_seuil,
    retour_au_synthetique_admis,
)
from app.contextes.portefeuille.domaine.temporel import MotifChangement, Periode

__all__ = [
    "PALIER_ALERTE",
    "Adhesion",
    "Associe",
    "CentreRattachement",
    "DiagnosticSeuil",
    "Dirigeant",
    "Entreprise",
    "Exercice",
    "FormeJuridique",
    "MandatDeclaratif",
    "MotifChangement",
    "Periode",
    "RegimeFiscal",
    "StatutIntrouvable",
    "StatutRattachement",
    "StatutRegime",
    "Tiers",
    "TypeTiers",
    "diagnostiquer_seuil",
    "retour_au_synthetique_admis",
]
