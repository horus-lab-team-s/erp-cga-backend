"""Surface publique du contexte F · Obligations et déclarations.

**Les autres contextes n'importent QUE ce module.**

Ce que les obligations promettent :

* une **échéance calculée**, jamais figée — à partir du type d'obligation, de la
  date de clôture et du centre de rattachement, tous deux historisés ;
* un échéancier généré depuis le **profil** du dossier et non depuis les pièces
  reçues : une déclaration néant est due même sans opération ;
* un profil évalué **à la fin de chaque période**, ce qui rend le franchissement
  de seuil correct en cours d'exercice ;
* une déclaration de TVA qui **isole et justifie** la TVA rejetée par le contrôle
  de conformité, pièce par pièce — ce qu'aucune déclaration ordinaire ne montre ;
* un retard **calculé**, jamais stocké.

Consommateurs prévus : `cloture` (la DSF est une obligation déclarative comme une
autre), `pilotage` (retards et charge du portefeuille).
"""

from __future__ import annotations

# Le catalogue des types d'obligation, en mémoire : ce sont des définitions, pas
# des données de dossier. Exporté parce que J · Pilotage en a besoin pour juger
# les retards, et qu'il ne doit pas entrer par `adaptateurs.sortant`.
from app.contextes.obligations.adaptateurs.sortant.accuses import accuses_du_portail
from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    DepotTypesObligationMemoire,
)
from app.contextes.obligations.adaptateurs.sortant.effectif import effectif_du_dossier
from app.contextes.obligations.application.declaration_tva import (
    ComptesTVA,
    DeclarationTVA,
    LigneRejet,
    etablir_declaration_tva,
)
from app.contextes.obligations.contrats import (
    ObligationInstance,
    Penalite,
    Periodicite,
    Relance,
    StatutObligation,
    TypeObligation,
)
from app.contextes.obligations.domaine.echeances import (
    calculer_echeance,
    calculer_penalite,
    mois_de_retard,
)
from app.contextes.obligations.domaine.obligations import (
    JALONS_RELANCE,
    generer_echeancier,
    relances_du_jour,
)
from app.contextes.obligations.domaine.ports import DepotObligations, DepotTypesObligation

__all__ = [
    # Types échangés
    "ComptesTVA",
    "DeclarationTVA",
    "LigneRejet",
    "ObligationInstance",
    "Penalite",
    "Periodicite",
    "Relance",
    "StatutObligation",
    "TypeObligation",
    # Sources
    "DepotObligations",
    "DepotTypesObligation",
    "DepotTypesObligationMemoire",
    # Échéances et pénalités
    "calculer_echeance",
    "calculer_penalite",
    "mois_de_retard",
    # Échéancier et relances
    "JALONS_RELANCE",
    "accuses_du_portail",
    "effectif_du_dossier",
    "generer_echeancier",
    "relances_du_jour",
    # La déclaration de TVA — le troisième maillon de la chaîne
    "etablir_declaration_tva",
]
