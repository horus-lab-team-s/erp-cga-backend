"""Surface publique du contexte D · Conformité documentaire.

**Les autres contextes n'importent QUE ce module.**

Ce que la conformité promet aux autres contextes :

* `MoteurConformite.controler(facture)` rend un `RapportConformite` **immuable** :
  réévaluer produit un nouveau rapport, jamais une mise à jour de l'ancien ;
* chaque `Constat` porte sa **conséquence fiscale déclarative** et son enjeu chiffré.
  La conformité ne modifie rien : elle décrit. C'est la comptabilité qui pose
  l'attribut fiscal sur la ligne d'écriture, et les obligations qui rejettent la TVA
  du mois ;
* le rapport conserve les **paramètres du référentiel employés**, avec leur valeur et
  leur date d'effet, ce qui le rend reproductible des années plus tard.

Consommateurs prévus : `collecte` (contrôle à la réception d'une pièce),
`comptabilite` (héritage de l'attribut fiscal), `obligations` (TVA rejetée du mois),
`cloture` (réintégrations du tableau de passage), `creation_entreprise` (même moteur,
autre jeu de règles pour les checklists de pièces), `pilotage` (score de risque).
"""

from __future__ import annotations

from .modeles import (
    ConsequenceFiscale,
    Constat,
    ContexteControle,
    Document,
    FactureAControler,
    LigneFacture,
    ModeReglement,
    Montants,
    Partie,
    RapportConformite,
    RegimeEmetteur,
    Regle,
    RegleEnEchec,
    Reglement,
    Severite,
    TypeDocument,
)
from .moteur import MoteurConformite, charger_regles
from .presentation import Verdict, composer_verdict

__all__ = [
    # Contrôle
    "MoteurConformite",
    "charger_regles",
    # Entrée
    "ContexteControle",
    "Document",
    "FactureAControler",
    "LigneFacture",
    "ModeReglement",
    "Montants",
    "Partie",
    "RegimeEmetteur",
    "Reglement",
    "TypeDocument",
    # Sortie
    "ConsequenceFiscale",
    "Constat",
    "RapportConformite",
    "RegleEnEchec",
    "Severite",
    # Règles
    "Regle",
    # Restitution
    "Verdict",
    "composer_verdict",
]
