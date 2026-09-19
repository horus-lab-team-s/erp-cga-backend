"""Surface publique du contexte C · Collecte de pièces.

**Les autres contextes n'importent QUE ce module** — jamais `application`, jamais
`domaine.pieces`, jamais un sous-module interne.

Ce que la collecte promet :

* une pièce **horodatée à l'arrivée**, dont le canal n'affecte jamais la valeur
  probante — WhatsApp vaut le portail ;
* un cycle de vie de **traitement** strictement progressif, qui ne se confond pas
  avec le verdict de conformité : une pièce non conforme est comptabilisée comme
  les autres, et c'est la déclaration qui en tire les conséquences ;
* la **détection des doublons**, refusée sur l'identité d'empreinte et signalée
  sur l'identité de document — le seul contrôle qui empêche une TVA d'être déduite
  deux fois, et qu'aucune règle du contexte D ne peut voir puisqu'il porte sur un
  ensemble de pièces et non sur une seule ;
* une valeur extraite par OCR qui **n'est pas une valeur** tant qu'un humain ne
  l'a pas retenue, et qui ne l'est jamais automatiquement pour un montant ou un
  NIU ;
* une **complétude honnête** : ce qui était attendu est arrivé, ce qui n'est pas
  l'exhaustivité du dossier et ne prétend pas l'être.

Consommateurs prévus : `comptabilite` (lien pièce ↔ écriture, rapprochement sur
relevés importés), `obligations` (complétude avant dépôt), `pilotage` (délais de
transmission, dossiers enlisés).
"""

from __future__ import annotations

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import (
    DepotDemandesMemoire,
    DepotPiecesMemoire,
    MagasinFichiersMemoire,
    PieceIntrouvable,
)

# ⚠️ Les réalisations SQL sont exportées **aussi**, et pas seulement celles en
# mémoire. N'exporter que la mémoire condamnait les contextes voisins à entrer
# par `adaptateurs.sortant` — ce que le test d'architecture refuse à juste titre.
# C'est le même défaut, et la même correction, que pour `comptabilite` et
# `portefeuille`.
from app.contextes.collecte.adaptateurs.sortant.depots_sql import (
    DepotDemandesSql,
    DepotPiecesSql,
)
from app.contextes.collecte.adaptateurs.sortant.donnees_demo import (
    DEMANDES_DEMO,
    PIECES_DEMO,
    REFERENCES_BLOQUANTES,
    REFERENCES_NON_IMPUTABLES,
)
from app.contextes.collecte.adaptateurs.sortant.magasins_memoire import (
    demandes_en_memoire,
    pieces_en_memoire,
    vider_les_magasins_de_la_collecte,
)
from app.contextes.collecte.application.completude import (
    CompletudeDossier,
    evaluer_completude,
)
from app.contextes.collecte.application.controle_a_reception import (
    ControleAReception,
    controler_a_reception,
)
from app.contextes.collecte.application.demandes_de_piece import (
    DemandeIntrouvable,
    DemandeRefusee,
    demander_une_piece,
    tracer_une_relance,
)
from app.contextes.collecte.application.reception import (
    DepotRefuse,
    ResultatReception,
    identifier,
    receptionner,
)
from app.contextes.collecte.application.traitement import (
    classer_une_piece,
    comptabiliser_la_piece,
)
from app.contextes.collecte.contrats import (
    CanalDepot,
    DemandePiece,
    EtatPiece,
    ExtractionOCR,
    NiveauSuspicion,
    PieceJustificative,
    RelancePiece,
    StatutDemande,
    SuspicionDoublon,
    TransitionRefusee,
    TypePiece,
    ValeurExtraite,
    ValeurNonRetenue,
)
from app.contextes.collecte.domaine.demandes import (
    JALONS_RELANCE_PIECE,
    SEUIL_ESCALADE_JOURS,
    relances_du_jour,
)
from app.contextes.collecte.domaine.demandes import NatureDeReponse, ReponseDeLAdherent
from app.contextes.collecte.domaine.doublons import detecter_doublons
from app.contextes.collecte.domaine.espace_adherent import (
    ReglagesDesReponses,
    StatutPourLAdherent,
    mois_de_la_piece,
    texte_de_la_reponse,
)
from app.contextes.collecte.domaine.extraction import (
    CHAMPS_A_VALIDATION_OBLIGATOIRE,
    SEUIL_ACCEPTATION_AUTOMATIQUE,
)
from app.contextes.collecte.domaine.pieces import empreinte
from app.contextes.collecte.domaine.ports import (
    DepotDemandes,
    DepotPieces,
    MagasinFichiers,
    ServiceExtraction,
)

__all__ = [
    "demandes_en_memoire",
    "pieces_en_memoire",
    "vider_les_magasins_de_la_collecte",
    # Types échangés
    "CanalDepot",
    "CompletudeDossier",
    "ControleAReception",
    "DemandePiece",
    "EtatPiece",
    "ExtractionOCR",
    "NiveauSuspicion",
    "PieceJustificative",
    "RelancePiece",
    "ResultatReception",
    "StatutDemande",
    "SuspicionDoublon",
    "TypePiece",
    "ValeurExtraite",
    # Sources
    "DepotDemandes",
    "DepotPieces",
    "DepotDemandesMemoire",
    "DepotDemandesSql",
    "DepotPiecesMemoire",
    "DepotPiecesSql",
    "MagasinFichiersMemoire",
    "PieceIntrouvable",
    "MagasinFichiers",
    "ServiceExtraction",
    # Réception et identification
    "empreinte",
    "identifier",
    "receptionner",
    # Doublons — le contrôle qui protège la TVA déduite deux fois
    "detecter_doublons",
    # Soudure avec le contexte D · Conformité, au plus tôt
    "controler_a_reception",
    # Demandes et relances, pour la relance composée du pilotage (pas 111)
    "DemandeIntrouvable",
    "DemandeRefusee",
    "demander_une_piece",
    "tracer_une_relance",
    # L'espace de l'adhérent (pas 112) : le mois d'une pièce et la réponse lue par le cabinet,
    # pour l'état du mois et la relance du pilotage.
    "NatureDeReponse",
    "ReglagesDesReponses",
    "ReponseDeLAdherent",
    "StatutPourLAdherent",
    "mois_de_la_piece",
    "reglages_des_reponses",
    "texte_de_la_reponse",
    # Fin de traitement d'une pièce (pas 107)
    "classer_une_piece",
    "comptabiliser_la_piece",
    # Relance et complétude
    "evaluer_completude",
    "relances_du_jour",
    # Politiques du cabinet, révisables
    "CHAMPS_A_VALIDATION_OBLIGATOIRE",
    "JALONS_RELANCE_PIECE",
    "SEUIL_ACCEPTATION_AUTOMATIQUE",
    "SEUIL_ESCALADE_JOURS",
    # Jeu de démonstration — le flux entrant de juillet 2026. Exposé parce que la
    # comptabilité en dérive ses écritures : c'est la pièce qui justifie l'écriture,
    # et l'arête `comptabilite → collecte` va dans ce sens-là.
    "DEMANDES_DEMO",
    "PIECES_DEMO",
    "REFERENCES_BLOQUANTES",
    "REFERENCES_NON_IMPUTABLES",
    # Erreurs — à traiter, jamais à ignorer
    "DepotRefuse",
    "TransitionRefusee",
    "ValeurNonRetenue",
]


def reglages_des_reponses() -> ReglagesDesReponses:
    """Les réponses toutes faites, lues au référentiel : la même lecture que la route de la collecte."""
    from app.contextes.collecte.adaptateurs.entrant.routes_http import (
        reglages_des_reponses as _reglages,
    )

    return _reglages()
