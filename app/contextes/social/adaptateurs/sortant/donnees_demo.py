"""Le personnel de démonstration.

Cinq salariés sur deux dossiers, choisis pour que chaque particularité du calcul
soit visible sur un bulletin réel plutôt que dans un test :

* un manœuvre **sous le plafond** CNPS, sans avantage — le cas ordinaire ;
* un cadre **au-dessus du plafond**, qui rend visible l'asymétrie entre les
  branches plafonnées et les accidents du travail ;
* un salarié **logé et véhiculé**, qui montre des avantages en nature dans
  l'assiette et hors du net ;
* une **embauche en cours de mois**, incluse à la déclaration ;
* un **départ**, qui doit apparaître en mouvement de sortie.

⚠️ Données fictives. Les matricules CNPS respectent un format plausible et ne
désignent personne.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.social.domaine.entites import (
    AvantageNature,
    Contrat,
    NatureAvantage,
    Salarie,
    TypeContrat,
)

__all__ = ["CONTRATS_DEMO", "RATTACHEMENTS_DEMO", "SALARIES_DEMO"]

#: AGRO-NKOLO SA, l'un des dossiers du portefeuille de démonstration.
_AGRO = "M065544332211L"
#: SARL BATIMENT PLUS — le dossier de l'adhérent jp.nkoa.
_BATIMENT = "M081234567890P"

SALARIES_DEMO: list[Salarie] = [
    Salarie(
        matricule="SAL-0001",
        nom="ETOUNDI",
        prenom="Jean-Claude",
        entreprise=_AGRO,
        matricule_cnps="0912345678",
        date_naissance=date(1985, 3, 14),
        enfants_a_charge=3,
    ),
    Salarie(
        matricule="SAL-0002",
        nom="MENGUE",
        prenom="Sandrine",
        entreprise=_AGRO,
        matricule_cnps="0923456789",
        date_naissance=date(1979, 11, 2),
        enfants_a_charge=2,
    ),
    Salarie(
        matricule="SAL-0003",
        nom="ABANDA",
        prenom="Pierre",
        entreprise=_AGRO,
        matricule_cnps="0934567890",
        date_naissance=date(1992, 6, 30),
    ),
    Salarie(
        matricule="SAL-0004",
        nom="NKOULOU",
        prenom="Alphonse",
        entreprise=_BATIMENT,
        matricule_cnps="0945678901",
        date_naissance=date(1988, 1, 20),
        enfants_a_charge=1,
    ),
    Salarie(
        matricule="SAL-0005",
        nom="TSANGA",
        prenom="Marie",
        entreprise=_BATIMENT,
        date_naissance=date(1996, 9, 8),
    ),
]

RATTACHEMENTS_DEMO: dict[str, str] = {s.matricule: s.entreprise for s in SALARIES_DEMO}

CONTRATS_DEMO: list[Contrat] = [
    # ── Le cadre : au-dessus du plafond CNPS, logé et véhiculé ──────────────
    #
    # Le seul dossier où le plafond joue. Sans lui, l'asymétrie entre branches
    # plafonnées et accidents du travail resterait invisible sur tous les écrans.
    Contrat(
        salarie="SAL-0001",
        type_contrat=TypeContrat.CDI,
        debut=date(2021, 4, 1),
        salaire_base=Decimal("950000"),
        primes=Decimal("150000"),
        avantages=(
            AvantageNature(nature=NatureAvantage.LOGEMENT),
            AvantageNature(nature=NatureAvantage.VEHICULE),
        ),
        poste="Directeur d'exploitation",
    ),
    # ── Le cas ordinaire : sous le plafond, sans avantage ───────────────────
    Contrat(
        salarie="SAL-0002",
        type_contrat=TypeContrat.CDI,
        debut=date(2019, 9, 16),
        salaire_base=Decimal("320000"),
        primes=Decimal("25000"),
        poste="Comptable",
    ),
    # ── Un CDD qui se termine : produit un mouvement de sortie en juillet ────
    #
    # `fin` est **exclue** : le dernier jour travaillé est le 31 juillet.
    Contrat(
        salarie="SAL-0003",
        type_contrat=TypeContrat.CDD,
        debut=date(2026, 2, 1),
        fin=date(2026, 8, 1),
        salaire_base=Decimal("180000"),
        poste="Magasinier",
    ),
    Contrat(
        salarie="SAL-0004",
        type_contrat=TypeContrat.CDI,
        debut=date(2022, 7, 4),
        salaire_base=Decimal("410000"),
        primes=Decimal("40000"),
        avantages=(AvantageNature(nature=NatureAvantage.DOMESTIQUE, nombre=2),),
        poste="Chef de chantier",
    ),
    # ── Une embauche en cours de mois : incluse à la déclaration de juillet ──
    Contrat(
        salarie="SAL-0005",
        type_contrat=TypeContrat.CDD,
        debut=date(2026, 7, 20),
        fin=date(2027, 1, 20),
        salaire_base=Decimal("150000"),
        poste="Assistante administrative",
    ),
]
