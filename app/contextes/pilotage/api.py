"""Surface publique du contexte J · Pilotage CGA.

⚠️ **Personne n'importe ce module**, et c'est vérifié : `test_architecture.py`
impose que le pilotage ne soit lu par aucun contexte. « J agrège et n'est lu par
personne. Si un contexte venait à le lire, c'est qu'un indicateur y aurait pris
une valeur métier, à redescendre chez son responsable. »

La surface existe malgré tout, parce que l'adaptateur entrant en a besoin et que
la discipline vaut aussi pour lui : il n'entre pas dans `application` ni dans
`domaine` par la porte de derrière.

Ce que le pilotage promet :

* un **score décomposé**, jamais un chiffre seul — chaque composante porte sa
  mesure, son poids, sa contribution et **les références des éléments** qui l'ont
  produite. On descend du score au dossier, et du dossier à la pièce ;
* une **pondération lue au référentiel**, jamais dans le code : elle appartient à
  la direction, et une direction qui doit demander un déploiement pour changer un
  poids ne pilote pas ;
* un **poids absent valant zéro et le disant**, plutôt qu'un montant inventé ;
* un **score non borné à cent** : un dossier cumulant douze anomalies doit se
  distinguer de celui qui en compte trois ;
* des **décisions de direction datées et signées** (pas 100), prises devant le score du
  jour sur un catalogue de mesures lu au référentiel, et gardées avec l'instantané du
  score qui les a fondées. C'est la seule chose que le pilotage écrit ;
* une **charge par collaborateur qui n'évalue personne** — elle mesure la
  répartition du travail, et sa seule action est de la rééquilibrer.

Ce que le pilotage **ne fait pas**, et c'est écrit plutôt que comblé : ni
rentabilité par adhérent — faute de suivi du temps passé, et un chiffre plausible
et faux serait employé pour arbitrer —, ni dossier de gestion à restituer, qui
suppose un contenu que le cabinet doit d'abord arrêter.
"""

from __future__ import annotations

from app.contextes.pilotage.adaptateurs.sortant.depots_decisions import (
    depot_des_decisions_de_direction,
    vider_les_decisions_de_direction,
)
from app.contextes.pilotage.adaptateurs.sortant.mesures_yaml import (
    charger_le_catalogue_des_mesures,
)
from app.contextes.pilotage.application.decisions import (
    clore_une_decision,
    prendre_une_mesure,
)
from app.contextes.pilotage.application.score_risque import (
    ObservationsDossier,
    evaluer_le_risque,
    repartir_la_charge,
)
from app.contextes.pilotage.domaine.decisions import (
    CatalogueDesMesures,
    DecisionDeDirection,
    DecisionIntrouvable,
    DecisionRefusee,
    MesureDeDirection,
)
from app.contextes.pilotage.domaine.entites import (
    ACTION_ATTENDUE,
    CODE_POIDS,
    ChargeCollaborateur,
    Composante,
    MesureComposante,
    NiveauRisque,
    ScoreRisque,
    SeuilsInverses,
)

__all__ = [
    "ACTION_ATTENDUE",
    "CODE_POIDS",
    "CatalogueDesMesures",
    "DecisionDeDirection",
    "DecisionIntrouvable",
    "DecisionRefusee",
    "MesureDeDirection",
    "catalogue_des_mesures",
    "charger_le_catalogue_des_mesures",
    "clore_une_decision",
    "depot_des_decisions_de_direction",
    "prendre_une_mesure",
    "vider_les_decisions_de_direction",
    "ChargeCollaborateur",
    "Composante",
    "MesureComposante",
    "NiveauRisque",
    "ObservationsDossier",
    "ScoreRisque",
    "SeuilsInverses",
    "evaluer_le_risque",
    "repartir_la_charge",
]


def catalogue_des_mesures() -> CatalogueDesMesures:
    """Le catalogue des mesures du cabinet, relu à chaque appel (pas 100).

    Sans mémoïsation, comme la politique d'écart : une mesure ajoutée par la direction
    apparaît au prochain écran, pas au prochain redémarrage.
    """
    from app.infrastructure.config import configuration

    return charger_le_catalogue_des_mesures(configuration().dossier_referentiel)
