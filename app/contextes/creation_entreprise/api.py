"""Surface publique du contexte I · Création d'entreprise.

**Les autres contextes n'importent QUE ce module.**

Ce que la création promet :

* un **tunnel qui ne se saute pas** — on n'avance que d'un cran, on ne revient
  pas, et l'abandon porte toujours son motif ;
* une **checklist figée à l'ouverture** du dossier, jamais recalculée : un
  dossier instruit sous une liste reste lisible sous cette liste ;
* une **conversion qui exige RCCM et NIU** — sans le premier il n'y a pas de
  personne morale, sans le second pas de contribuable ;
* une entreprise créée avec la **date du RCCM**, jamais celle du jour, pour que
  les obligations fiscales commencent quand elles ont réellement commencé ;
* aucun montant en dur : le capital minimum se lit au référentiel, à une date.

⚠️ **Ce contexte n'écrit pas au portefeuille.** `convertir` *fabrique* une
`Entreprise` et la rend ; c'est l'adaptateur entrant qui l'enregistre, parce que
lui seul connaît la transaction en cours. La frontière reste ainsi visible dans
le code plutôt que cachée dans un cas d'usage.

Consommateurs prévus : `pilotage` (état du pipeline de créations).
"""

from __future__ import annotations

from app.contextes.creation_entreprise.adaptateurs.sortant.depots import (
    DepotDossiersCreationMemoire,
    DepotDossiersCreationSql,
)
from app.contextes.creation_entreprise.adaptateurs.sortant.donnees_demo import PIPELINE_DEMO
from app.contextes.creation_entreprise.application.conversion import (
    ConversionImpossible,
    convertir,
)
from app.contextes.creation_entreprise.application.tunnel import (
    ConstatConstitution,
    Diagnostic,
    abandonner,
    avancer,
    depasse_le_delai_annonce,
    diagnostiquer,
    enregistrer_identifiant,
    fournir_piece,
    ouvrir_dossier,
)
from app.contextes.creation_entreprise.domaine.checklist import (
    SOCIETES,
    checklist_de,
    code_du_capital_minimum,
)
from app.contextes.creation_entreprise.domaine.entites import (
    ETATS_TERMINAUX,
    DossierCreation,
    EtapeCreation,
    Fondateur,
    Immatriculation,
    Jalon,
    PieceConstitution,
    TransitionInterdite,
    etapes_ouvertes_depuis,
)
from app.contextes.creation_entreprise.domaine.ports import (
    DepotDossiersCreation,
    DossierCreationIntrouvable,
)

__all__ = [
    "ETATS_TERMINAUX",
    "PIPELINE_DEMO",
    "SOCIETES",
    "ConstatConstitution",
    "ConversionImpossible",
    "DepotDossiersCreation",
    "DepotDossiersCreationMemoire",
    "DepotDossiersCreationSql",
    "Diagnostic",
    "DossierCreation",
    "DossierCreationIntrouvable",
    "EtapeCreation",
    "Fondateur",
    "Immatriculation",
    "Jalon",
    "PieceConstitution",
    "TransitionInterdite",
    "abandonner",
    "avancer",
    "checklist_de",
    "code_du_capital_minimum",
    "convertir",
    "depasse_le_delai_annonce",
    "diagnostiquer",
    "enregistrer_identifiant",
    "etapes_ouvertes_depuis",
    "fournir_piece",
    "ouvrir_dossier",
]
