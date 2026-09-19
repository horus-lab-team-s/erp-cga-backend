"""Surface publique du contexte B · Portefeuille adhérents.

**Les autres contextes n'importent QUE ce module** — jamais `application`, jamais
`domaine.entites`, jamais un sous-module interne.

Ce que le portefeuille promet :

* une lecture de statut **à une date**, jamais « le régime actuel » — une facture
  de 2024 se contrôle avec le régime de 2024 ;
* une erreur explicite quand le statut est inconnu, jamais une valeur par défaut :
  supposer le régime du réel parce qu'on ne sait pas produirait un contrôle faux,
  et il serait faux en silence ;
* des exercices **sans présomption d'année civile** : ni douze mois, ni du
  1er janvier au 31 décembre ;
* le diagnostic de franchissement de seuil, avec alerte **avant** le
  franchissement — c'est là que se trouve la valeur, pas dans le constat après coup.

Consommateurs prévus : `conformite` (portée des règles par régime), `comptabilite`
(assujettissement, exercice), `obligations` (rattachement, échéances, mandat),
`cloture` (adhésion, exercice), `creation_entreprise` (conversion en adhérent),
`pilotage` (indicateurs de portefeuille).
"""

from __future__ import annotations

from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_memoire import (
    DepotEntreprisesMemoire,
    EntrepriseIntrouvable,
    HistoireAmputee,
)

# ⚠️ Voir le commentaire équivalent dans `comptabilite/api.py` : n'exporter que
# la réalisation mémoire condamnait les contextes voisins à la lire.
from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_sql import (
    DepotEntreprisesSql,
)
from app.contextes.portefeuille.adaptateurs.sortant.donnees_demo import PORTEFEUILLE_DEMO
from app.contextes.portefeuille.adaptateurs.sortant.magasins_memoire import (
    entreprises_en_memoire,
    vider_les_entreprises_en_memoire,
)
from app.contextes.portefeuille.application.validation_identifiants import (
    AnomalieIdentifiant,
    verifier_identifiants,
    verifier_portefeuille,
)
from app.contextes.portefeuille.contrats import (
    Adhesion,
    Associe,
    CentreRattachement,
    Dirigeant,
    Entreprise,
    Exercice,
    FormeJuridique,
    MandatDeclaratif,
    MotifChangement,
    Periode,
    RegimeFiscal,
    StatutIntrouvable,
    StatutRattachement,
    StatutRegime,
    Tiers,
    TypeTiers,
)
from app.contextes.portefeuille.domaine.adhesion import (
    AdhesionRefusee,
    inscrire_une_adhesion,
    prochain_numero_d_adhesion,
    resilier_l_adhesion,
)
from app.contextes.portefeuille.domaine.entites import statut_initial
from app.contextes.portefeuille.domaine.ports import DepotEntreprises
from app.contextes.portefeuille.domaine.reclassement import (
    CAUSES_ADMISES,
    InscriptionRefusee,
    inscrire_un_regime,
)
from app.contextes.portefeuille.domaine.regimes import (
    PALIER_ALERTE,
    DiagnosticSeuil,
    diagnostiquer_seuil,
    retour_au_synthetique_admis,
)
from app.contextes.portefeuille.domaine.temporel import verifier_succession

__all__ = [
    "entreprises_en_memoire",
    "vider_les_entreprises_en_memoire",
    "AdhesionRefusee",
    "inscrire_une_adhesion",
    "prochain_numero_d_adhesion",
    "resilier_l_adhesion",
    "CAUSES_ADMISES",
    "InscriptionRefusee",
    "inscrire_un_regime",
    # Types échangés
    "Adhesion",
    "Associe",
    "CentreRattachement",
    "Dirigeant",
    "Entreprise",
    "Exercice",
    "FormeJuridique",
    "MandatDeclaratif",
    "MotifChangement",
    "Periode",
    "RegimeFiscal",
    "StatutRattachement",
    "StatutRegime",
    "Tiers",
    "TypeTiers",
    # Sources — le port, et sa réalisation en mémoire en attendant PostgreSQL.
    "DepotEntreprises",
    "DepotEntreprisesMemoire",
    "DepotEntreprisesSql",
    # Jeu de démonstration : six dossiers, avec leurs statuts datés.
    "PORTEFEUILLE_DEMO",
    # Règles de régime
    "PALIER_ALERTE",
    "DiagnosticSeuil",
    "diagnostiquer_seuil",
    "retour_au_synthetique_admis",
    # Fabriques et vérifications
    "statut_initial",
    "verifier_succession",
    # Contrôle des identifiants contre le référentiel daté
    "AnomalieIdentifiant",
    "verifier_identifiants",
    "verifier_portefeuille",
    # Erreurs — à traiter, jamais à ignorer
    "EntrepriseIntrouvable",
    "HistoireAmputee",
    "StatutIntrouvable",
]
