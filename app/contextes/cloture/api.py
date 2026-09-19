"""Surface publique du contexte H · Clôture et DSF.

**Les autres contextes n'importent QUE ce module.**

Ce que la clôture promet :

* une **liasse ventilée depuis la balance**, poste par poste, chaque ligne portant
  les comptes qui l'ont alimentée ;
* un **résultat calculé deux fois** — par le compte de résultat et par le bilan —
  et un contrôle qui les compare, parce que deux chemins indépendants sont la
  seule façon de rendre le contrôle utile ;
* **aucun équilibrage d'office** : une balance déséquilibrée produit une liasse
  déséquilibrée et un contrôle en échec, jamais un ajustement silencieux ;
* **aucun compte perdu** : ce que le plan de correspondance ne couvre pas est
  signalé nommément, jamais rangé dans un poste « divers » ;
* un **tableau de passage** où atterrit le chiffrage du moteur de conformité —
  c'est le dernier maillon de la chaîne annoncée au § 04 de l'architecture ;
* une **TVA rejetée signalée et non réintégrée** : elle relève de la déclaration
  de TVA, pas du résultat fiscal. Les confondre ferait payer l'impôt deux fois sur
  la même somme ;
* un **abattement CGA prudent** : jamais sur un déficit, jamais sans adhésion
  couvrant l'exercice, et toujours calculé **après** les réintégrations.

⚠️ **Ce contexte ne persiste rien**, et c'est un choix. Tout ce qu'il produit est
une fonction de la balance, des écritures et du référentiel à la date de clôture.
Le stocker créerait deux vérités — la liasse figée et la liasse recalculée — et
personne ne saurait laquelle fait foi le jour où une écriture de régularisation
est passée. Ce qui se conserve, c'est l'**accusé de dépôt**, et il appartient à
F · Obligations, qui tient déjà ce registre.

Consommateurs prévus : `pilotage` (avancement des clôtures).
"""

from __future__ import annotations

from app.contextes.cloture.application.collecte_reintegrations import (
    MoissonReintegrations,
    moissonner,
)
from app.contextes.cloture.application.liasse import (
    EtatsFinanciers,
    assembler_les_etats,
    determiner_le_systeme,
)
from app.contextes.cloture.application.passage_fiscal import (
    ConsequenceAReintegrer,
    PassageFiscal,
    etablir_le_passage,
)
from app.contextes.cloture.domaine.entites import (
    ControleCoherence,
    Exercice,
    LigneLiasse,
    LignePassage,
    NaturePassage,
    PosteLiasse,
    SensPoste,
    SystemeDsf,
)
from app.contextes.cloture.domaine.postes import (
    COMPTES_BIDIRECTIONNELS,
    PLAN_LIASSE,
    poste_du_compte,
    postes_du_systeme,
)

__all__ = [
    "COMPTES_BIDIRECTIONNELS",
    "PLAN_LIASSE",
    "ConsequenceAReintegrer",
    "ControleCoherence",
    "EtatsFinanciers",
    "Exercice",
    "LigneLiasse",
    "LignePassage",
    "MoissonReintegrations",
    "NaturePassage",
    "PassageFiscal",
    "PosteLiasse",
    "SensPoste",
    "SystemeDsf",
    "assembler_les_etats",
    "determiner_le_systeme",
    "etablir_le_passage",
    "moissonner",
    "poste_du_compte",
    "postes_du_systeme",
]
