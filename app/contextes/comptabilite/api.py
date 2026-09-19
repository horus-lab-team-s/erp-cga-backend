"""Surface publique du contexte E · Comptabilité SYSCOHADA.

**Les autres contextes n'importent QUE ce module** — jamais `application`, jamais
`domaine.entites`, jamais un sous-module interne. C'est cette règle, vérifiée par
`tests/test_architecture.py`, qui permet de réorganiser l'intérieur d'un contexte
sans casser les onze autres.

Ce que la comptabilité promet :

* des écritures **équilibrées, numérotées sans trou et immuables une fois
  validées** — les trois propriétés qui rendent une piste d'audit défendable ;
* une **balance** et un **grand livre** calculés sur les écritures, jamais
  persistés : il n'existe qu'une source, le journal ;
* des **attributs fiscaux portés par la ligne**, reçus du contexte D et jamais
  calculés ici, qui alimenteront la déclaration du mois puis le tableau de passage ;
* le refus de comptabiliser une pièce bloquante, par une exception plutôt que par
  un booléen qu'on oublie de tester.

Consommateurs prévus : `obligations` (TVA du mois, ligne « TVA rejetée par le
contrôle de conformité »), `cloture` (balance, tableau de passage, liasse),
`pilotage` (indicateurs).
"""

from __future__ import annotations

from app.contextes.comptabilite.adaptateurs.sortant.depot_ecritures_memoire import (
    DepotEcrituresMemoire,
    EcritureFigee,
    EcritureIntrouvable,
)

# Pas 104 : le plan de travail du pilotage lit les rapprochements et les revues, pour dire
# quel relevé reste à rapprocher et quel mois reste à transmettre ou à reprendre.
from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
    depot_des_rapprochements,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues

# ⚠️ Le dépôt SQL est exporté **à côté** du dépôt mémoire, et l'asymétrie
# précédente n'était pas anodine : seule la réalisation mémoire franchissait la
# frontière du contexte. Un contexte voisin qui voulait lire ces données n'avait
# donc littéralement pas d'autre choix que la mémoire — et le contexte F y a
# calculé des déclarations fiscales sur des données de démonstration pendant que
# la comptabilité réelle vivait en base.
#
# Ce que le port `api.py` expose commande ce que les voisins peuvent faire.
from app.contextes.comptabilite.adaptateurs.sortant.depots_sql import (
    DepotEcrituresSql,
)
from app.contextes.comptabilite.adaptateurs.sortant.donnees_demo import (
    PLAN_IMPUTATION_DEMO,
    DepotPlanImputationMemoire,
    ecritures_demo,
)
from app.contextes.comptabilite.adaptateurs.sortant.magasins_memoire import (
    ecritures_en_memoire,
    vider_les_ecritures_en_memoire,
)
from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    DepotJournauxMemoire,
    DepotPlanMemoire,
)
from app.contextes.comptabilite.application.consequences_fiscales import (
    ComptabilisationInterdite,
    SyntheseFiscale,
    appliquer_rapport,
    montant_tva_rejetee,
    reperer_lignes,
    synthetiser,
    verifier_comptabilisation_autorisee,
)
from app.contextes.comptabilite.application.proposition_ecriture import (
    FactureDUnAutreDossier,
    ImputationImpossible,
    proposer_ecriture_achat,
    rattacher_au_dossier,
)
from app.contextes.comptabilite.application.tenue_du_journal import (
    BrouillonEcriture,
    CompteInconnu,
    ContrepassationAntidatee,
    ContrepassationEnDouble,
    CorrectionDeBrouillon,
    CorrectionRefusee,
    DateHorsExercice,
    ExerciceClos,
    JournalInconnu,
    MoisVerrouille,
    SaisieRefusee,
    apercevoir_la_contrepassation,
    contrepasser_une_ecriture,
    corriger_un_brouillon,
    enregistrer_une_ecriture,
    valider_une_ecriture,
)
from app.contextes.comptabilite.contrats import (
    AttributFiscal,
    Compte,
    DestinationCompte,
    EcritureComptable,
    EtatEcriture,
    Journal,
    LigneEcriture,
    LigneGrandLivre,
    NatureJournal,
    PlanImputation,
    RegleImputation,
    Sens,
    SoldeCompte,
    TrouSequence,
    TypeEcriture,
)
from app.contextes.comptabilite.domaine.imputation import resoudre_compte
from app.contextes.comptabilite.domaine.ports import (
    DepotEcritures,
    DepotJournaux,
    DepotPlanComptable,
    DepotPlanImputation,
)
from app.contextes.comptabilite.domaine.projections import (
    COMPTES_DU_CHIFFRE_D_AFFAIRES,
    balance,
    balance_par_racine,
    chiffre_affaires,
    controle_balance_equilibree,
    controle_bouclage,
    grand_livre,
    resultat,
    sequences_incompletes,
)
from app.contextes.comptabilite.domaine.rapprochement import StatutRapprochement
from app.contextes.comptabilite.domaine.revue import StatutRemarque, StatutRevue

__all__ = [
    "StatutRapprochement",
    "StatutRemarque",
    "StatutRevue",
    "depot_des_rapprochements",
    "depot_des_revues",
    "ecritures_en_memoire",
    "vider_les_ecritures_en_memoire",
    # Types échangés
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
    "SyntheseFiscale",
    "TrouSequence",
    "TypeEcriture",
    # Sources — les ports…
    "DepotEcritures",
    "DepotJournaux",
    "DepotPlanComptable",
    "DepotPlanImputation",
    # …et leurs réalisations en mémoire, en attendant PostgreSQL.
    "DepotEcrituresMemoire",
    "DepotEcrituresSql",
    "DepotJournauxMemoire",
    "DepotPlanImputationMemoire",
    "DepotPlanMemoire",
    "COMPTES_SYSCOHADA",
    "JOURNAUX_CABINET",
    "PLAN_IMPUTATION_DEMO",
    "ecritures_demo",
    # Projections — la balance alimente la liasse, le grand livre le lettrage
    "balance",
    "balance_par_racine",
    "COMPTES_DU_CHIFFRE_D_AFFAIRES",
    "chiffre_affaires",
    "grand_livre",
    "resultat",
    # Contrôles de cohérence, à rejouer avant tout dépôt
    "controle_balance_equilibree",
    "controle_bouclage",
    "sequences_incompletes",
    # Imputation — proposer l'écriture d'une facture contrôlée
    "proposer_ecriture_achat",
    "rattacher_au_dossier",
    "resoudre_compte",
    # Tenue du journal — la première écriture du produit
    "BrouillonEcriture",
    "ContrepassationAntidatee",
    "ContrepassationEnDouble",
    "CorrectionDeBrouillon",
    "CorrectionRefusee",
    "apercevoir_la_contrepassation",
    "periodes_verrouillees_du_dossier",
    "contrepasser_une_ecriture",
    "corriger_un_brouillon",
    "enregistrer_une_ecriture",
    "valider_une_ecriture",
    # Soudure avec le contexte D · Conformité
    "appliquer_rapport",
    "montant_tva_rejetee",
    "reperer_lignes",
    "synthetiser",
    "verifier_comptabilisation_autorisee",
    # Erreurs — à traiter, jamais à ignorer
    "ComptabilisationInterdite",
    "EcritureFigee",
    "EcritureIntrouvable",
    "FactureDUnAutreDossier",
    "ImputationImpossible",
    "SaisieRefusee",
    "CompteInconnu",
    "DateHorsExercice",
    "ExerciceClos",
    "JournalInconnu",
    "MoisVerrouille",
]


def periodes_verrouillees_du_dossier(dossier: str):
    """Les mois verrouillés d'un dossier (pas 107), pour un voisin qui prépare un geste d'écriture
    (pas 110 : l'impact d'une contre-passation, chez les obligations). La même fonction que les
    routes de la comptabilité : un voisin qui la recopierait verrouillerait autrement."""
    from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
        periodes_verrouillees_du_dossier as _periodes,
    )

    return _periodes(dossier)
