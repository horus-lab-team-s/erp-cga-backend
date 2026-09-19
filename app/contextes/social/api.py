"""Surface publique du contexte G · Social et paie.

**Les autres contextes n'importent QUE ce module.**

Ce que le social promet :

* un **bulletin calculé**, jamais saisi — fonction du contrat, de la période et
  du référentiel à cette date. Recalculé en 2029 sur une paie de 2026, il rend le
  même chiffre ;
* le **plafond CNPS appliqué aux seules branches qui le connaissent** : pensions
  et prestations familiales plafonnées, accidents du travail sur salaire réel ;
* un **barème IRPP progressif et annualisé**, jamais un taux moyen appliqué au
  mois ;
* des **avantages en nature dans l'assiette et hors du net** — ils sont
  imposables, et ils ont déjà été fournis ;
* un **refus de calculer** plutôt qu'un bulletin amputé : un taux absent du
  référentiel lève. Un calcul qui ne peut pas être juste ne rend pas de résultat ;
* chaque ligne portant **son assiette, son taux et le code du paramètre** qui l'a
  produite, et signalant si cette valeur n'est pas encore validée.

Consommateurs : `pilotage` (effectif et masse salariale), et `obligations` pour une
seule question depuis le pas 56 : `a_employe_sur`, lue à travers `depots_du_social`.
"""

from __future__ import annotations

from app.contextes.social.adaptateurs.sortant.depots import (
    DepotContratsMemoire,
    DepotContratsSql,
    DepotSalariesMemoire,
    DepotSalariesSql,
    identifiant_de_contrat,
)
from app.contextes.social.adaptateurs.sortant.donnees_demo import (
    CONTRATS_DEMO,
    RATTACHEMENTS_DEMO,
    SALARIES_DEMO,
)
from app.contextes.social.adaptateurs.sortant.magasins import (
    depots_du_social,
    vider_les_magasins_du_social,
)
from app.contextes.social.application.dipe import (
    Declaration,
    MouvementSalarie,
    etablir_la_declaration,
)
from app.contextes.social.application.paie import (
    ParametrePaieAbsent,
    calculer_bulletin,
    evaluer_les_avantages,
)
from app.contextes.social.application.personnel import (
    ContratEnChevauchement,
    ContratIntrouvable,
    MatriculeDejaAttribue,
    clore_un_contrat,
    inscrire_un_salarie,
    ouvrir_un_contrat,
)
from app.contextes.social.domaine.entites import (
    CODE_ACCIDENTS_TRAVAIL,
    CODE_FORFAIT,
    AvantageNature,
    Bulletin,
    Contrat,
    GroupeRisque,
    LigneRetenue,
    NatureAvantage,
    Periode,
    Salarie,
    TypeContrat,
    a_employe_sur,
)
from app.contextes.social.domaine.ports import (
    DepotContrats,
    DepotSalaries,
    SalarieIntrouvable,
)

__all__ = [
    "CODE_ACCIDENTS_TRAVAIL",
    "CODE_FORFAIT",
    "CONTRATS_DEMO",
    "RATTACHEMENTS_DEMO",
    "SALARIES_DEMO",
    "AvantageNature",
    "Bulletin",
    "Contrat",
    "ContratEnChevauchement",
    "ContratIntrouvable",
    "Declaration",
    "DepotContrats",
    "DepotContratsMemoire",
    "DepotContratsSql",
    "DepotSalaries",
    "DepotSalariesMemoire",
    "DepotSalariesSql",
    "GroupeRisque",
    "LigneRetenue",
    "MatriculeDejaAttribue",
    "MouvementSalarie",
    "NatureAvantage",
    "ParametrePaieAbsent",
    "Periode",
    "Salarie",
    "SalarieIntrouvable",
    "TypeContrat",
    "a_employe_sur",
    "calculer_bulletin",
    "clore_un_contrat",
    "depots_du_social",
    "etablir_la_declaration",
    "evaluer_les_avantages",
    "identifiant_de_contrat",
    "inscrire_un_salarie",
    "ouvrir_un_contrat",
    "vider_les_magasins_du_social",
]
