"""Surface publique du contexte L · Vitrine publique.

**Les autres contextes n'importent QUE ce module** — jamais `application`, jamais
`domaine.entites`, jamais un sous-module interne. C'est cette règle, vérifiée par
`tests/test_architecture.py`, qui permet de réorganiser l'intérieur d'un contexte
sans casser les onze autres.

Ce que la Vitrine promet :

* un contenu éditorial **modifiable sans recompilation** — le cabinet corrige un
  fichier, le site suit ;
* une lecture d'annonce **à une date**, jamais « l'annonce courante » : c'est
  l'appelant qui dit quel jour on est, et c'est ce qui rend le comportement
  reproductible en test comme en production ;
* des erreurs explicites — un article absent lève, il ne rend pas `None`.
"""

from __future__ import annotations

from app.contextes.vitrine.adaptateurs.sortant.depot_yaml import (
    ContenuIllisible,
    DepotContenuVitrineYaml,
    charger_annonces,
    charger_articles,
    charger_institutions,
)
from app.contextes.vitrine.application.service_contenu import (
    ArticleInconnu,
    ServiceContenu,
    SlugsEnDoublon,
)
from app.contextes.vitrine.contrats import (
    Annonce,
    AppelAction,
    Article,
    Bloc,
    BlocEncadre,
    BlocIntertitre,
    BlocListe,
    BlocParagraphe,
    Institution,
    Rubrique,
)
from app.contextes.vitrine.domaine.ports import DepotContenuVitrine

__all__ = [
    # Lecture
    "ServiceContenu",
    # Sources
    "DepotContenuVitrine",
    "DepotContenuVitrineYaml",
    "charger_annonces",
    "charger_articles",
    "charger_institutions",
    # Types échangés
    "Annonce",
    "AppelAction",
    "Article",
    "Bloc",
    "BlocEncadre",
    "BlocIntertitre",
    "BlocListe",
    "BlocParagraphe",
    "Institution",
    "Rubrique",
    # Erreurs — à traiter, jamais à ignorer
    "ArticleInconnu",
    "ContenuIllisible",
    "SlugsEnDoublon",
]
