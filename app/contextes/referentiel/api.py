"""Surface publique du contexte A · Référentiel normatif.

**Les autres contextes n'importent QUE ce module.** Jamais `service`, jamais
`modeles`, jamais un sous-module interne. C'est cette règle — vérifiée par
`tests/test_architecture.py` — qui permet de réorganiser l'intérieur d'un contexte
sans casser les dix autres.

Ce que le référentiel promet aux autres contextes :

* une lecture de paramètre **à une date**, jamais « la valeur courante » ;
* un `ParametreResolu` qui porte sa valeur, sa date d'effet, son fondement légal et
  son statut de validation, afin que l'appelant puisse le consigner dans ses propres
  traces ;
* une erreur explicite quand la valeur manque, jamais un défaut silencieux.
"""

from __future__ import annotations

from functools import lru_cache as _lru_cache

from app.contextes.referentiel.adaptateurs.sortant.circuit_yaml import charger_le_circuit
from app.contextes.referentiel.adaptateurs.sortant.depot_yaml import (
    DepotBaremesYaml,
    DepotParametresYaml,
    charger_baremes,
    charger_parametres,
)
from app.contextes.referentiel.adaptateurs.sortant.depots_decisions import (
    depot_des_decisions,
    vider_les_decisions,
)
from app.contextes.referentiel.application.decisions import (
    HorsDuCircuit,
    proposer_une_version,
    retirer_une_decision,
    trancher_une_proposition,
    valider_une_version,
)
from app.contextes.referentiel.application.service_parametres import (
    AucuneVersionApplicable,
    BaremeInconnu,
    ParametreInconnu,
    ServiceBaremes,
    ServiceParametres,
)
from app.contextes.referentiel.contrats import (
    Bareme,
    BaremeResolu,
    Fondement,
    Parametre,
    ParametreResolu,
    StatutValidation,
    TrancheBareme,
    Unite,
    VersionBareme,
    VersionParametre,
)
from app.contextes.referentiel.domaine.ports import DepotBaremes, DepotParametres
from app.contextes.referentiel.domaine.surcouche import (
    CircuitDeValidation,
    DecisionRefusee,
    DecisionSurLeReferentiel,
    EtapeDuCircuit,
    SorteDeDecision,
    StatutDecision,
    appliquer_la_surcouche,
    decision_sans_objet,
)

__all__ = [
    # Lecture
    "ServiceBaremes",
    "ServiceParametres",
    # Sources
    "DepotBaremes",
    "DepotBaremesYaml",
    "DepotParametres",
    "DepotParametresYaml",
    "charger_baremes",
    "charger_parametres",
    # Types échangés
    "Bareme",
    "BaremeResolu",
    "Fondement",
    "Parametre",
    "ParametreResolu",
    "StatutValidation",
    "TrancheBareme",
    "Unite",
    "VersionBareme",
    "VersionParametre",
    # Erreurs — à traiter, jamais à ignorer
    # Surcouche du cabinet (pas 95)
    "CircuitDeValidation",
    "DecisionRefusee",
    "DecisionSurLeReferentiel",
    "EtapeDuCircuit",
    "HorsDuCircuit",
    "SorteDeDecision",
    "StatutDecision",
    "charger_le_circuit",
    "circuit_de_validation",
    "decision_sans_objet",
    "depot_des_decisions",
    "parametres_communs",
    "parametres_du_cabinet",
    "proposer_une_version",
    "retirer_une_decision",
    "service_parametres",
    "trancher_une_proposition",
    "valider_une_version",
    "vider_les_decisions",
    "AucuneVersionApplicable",
    "BaremeInconnu",
    "ParametreInconnu",
]


# ── Le point de montage unique (pas 95) ─────────────────────────────────────
#
# ⚠️ **TOUT CONTEXTE QUI LIT UN PARAMÈTRE PASSE PAR `service_parametres()`.**
#
# Jusqu'au pas 94, dix modules construisaient chacun leur `ServiceParametres` sur le
# fichier, et la plupart le mémoïsaient. C'était sans conséquence tant que le fichier était
# la seule source. Depuis que chaque cabinet peut valider une version, un montage qui
# lirait le fichier seul calculerait avec l'ancien taux, et un montage mémoïsé garderait
# l'ancien taux jusqu'au redémarrage : la conformité rejetterait une TVA que l'échéancier
# accepte. C'est la faute que la surface de la conformité avait déjà corrigée pour son
# moteur (« deux montages divergent au premier changement »), étendue à tout le produit.


def _fichier_des_parametres():
    from app.infrastructure.config import configuration

    return configuration().dossier_referentiel / "parametres.yaml"


@_lru_cache
def _communs_lus(chemin, date_de_modification: float) -> tuple[Parametre, ...]:
    # La date de modification est dans la clé : un fichier commun remplacé au déploiement
    # est relu, sans redémarrage et sans relire le disque à chaque calcul.
    return tuple(charger_parametres(chemin))


def parametres_communs() -> tuple[Parametre, ...]:
    """Le référentiel commun, tel que le fichier le livre, pour tous les cabinets."""
    chemin = _fichier_des_parametres()
    return _communs_lus(chemin, chemin.stat().st_mtime)


def parametres_du_cabinet() -> list[Parametre]:
    """Le référentiel **tel que le cabinet courant l'emploie** : le commun, et ses décisions.

    Hors de tout cabinet (un script, un amorçage), le commun seul : il n'y a la décision de
    personne à appliquer.
    """
    from app.partage.locataire import courant_ou_none

    communs = parametres_communs()
    if courant_ou_none() is None:
        return list(communs)
    decisions = depot_des_decisions().toutes()
    if not decisions:
        return list(communs)
    return appliquer_la_surcouche(communs, decisions)


def service_parametres() -> ServiceParametres:
    """Le service de lecture du cabinet courant. **Jamais mémoïsé par l'appelant.**"""
    return ServiceParametres(parametres_du_cabinet())


def circuit_de_validation() -> CircuitDeValidation:
    """Le circuit du cabinet, relu à chaque appel : un petit fichier, et un réglage changé
    doit valoir au geste suivant."""
    from app.infrastructure.config import configuration

    return charger_le_circuit(configuration().dossier_referentiel)
