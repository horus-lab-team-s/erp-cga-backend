"""Contrats du contexte G · Social et paie — rang 1.

N'expose que des entités. Un seul consommateur est prévu : J · Pilotage, pour
l'effectif suivi et la masse salariale du portefeuille.

⚠️ **F · Obligations lit ce contexte depuis le pas 56, pour une seule question** :
le dossier a-t-il employé quelqu'un sur la période ? (`a_employe_sur`)

Ce commentaire affirmait l'inverse, et le justifiait : l'échéancier prenait
`a_des_salaries` en paramètre, pour ne pas charger le fichier du personnel à chaque
calcul d'échéance. Le paramètre valait `False` par défaut, un seul appelant sur
quatre l'acceptait, et aucun écran ne l'envoyait. **La CNPS et les retenues sur
salaires n'apparaissaient donc jamais**, ni à l'échéancier, ni aux relances, ni au
pilotage, alors que le catalogue les désigne comme le piège le plus coûteux du
métier. Éviter une arête avait coûté l'obligation elle-même.

L'échéancier lit les contrats du dossier une fois, pas le fichier du personnel.
"""

from __future__ import annotations

from app.contextes.social.domaine.entites import (
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

__all__ = [
    "AvantageNature",
    "Bulletin",
    "Contrat",
    "GroupeRisque",
    "LigneRetenue",
    "NatureAvantage",
    "Periode",
    "Salarie",
    "TypeContrat",
    "a_employe_sur",
]
