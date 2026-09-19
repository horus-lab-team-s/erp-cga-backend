"""Les cas d'usage des décisions d'un cabinet sur le référentiel (pas 95).

─────────────────────────────────────────────────────────────────────────────────
TROIS GESTES

* `valider_une_version` : une version que le référentiel **en vigueur pour ce cabinet**
  livre « à valider » devient validée, au nom de la personne qualifiée. Un seul geste :
  il n'y a personne d'autre à qui faire valider, la proposition est le fichier.
* `proposer_une_version` : une nouvelle valeur datée, **sans effet** tant qu'elle n'est pas
  validée.
* `trancher_une_proposition` : valider ou refuser, par une autre personne si le circuit
  l'exige.

⚠️ QUI A LE DROIT : LES PERMISSIONS SONT DES CHAÎNES

Le référentiel appartient au socle et ne connaît pas la table des rôles. Le circuit nomme
des permissions ; la route (au transverse) passe celles que la session détient. Une
permission mal orthographiée dans le circuit ne donne donc le droit à personne, et le test
d'intégrité du circuit le signale.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime

from app.contextes.referentiel.domaine.entites import Fondement, Parametre, StatutValidation
from app.contextes.referentiel.domaine.surcouche import (
    CircuitDeValidation,
    DecisionRefusee,
    DecisionSurLeReferentiel,
    SorteDeDecision,
    StatutDecision,
    valeur_conforme_a_l_unite,
)

__all__ = [
    "HorsDuCircuit",
    "proposer_une_version",
    "retirer_une_decision",
    "trancher_une_proposition",
    "valider_une_version",
]


class HorsDuCircuit(PermissionError):
    """La session ne détient aucune des permissions que le circuit désigne pour ce geste."""


def _parametre(parametres: Iterable[Parametre], code: str) -> Parametre:
    for parametre in parametres:
        if parametre.code == code:
            return parametre
    raise LookupError(f"paramètre « {code} » absent du référentiel.")


def _exiger(permissions: frozenset[str], designees: tuple[str, ...], geste: str) -> None:
    if not permissions.intersection(designees):
        raise HorsDuCircuit(f"le circuit du cabinet ne vous désigne pas pour {geste} ce paramètre.")


def _motif(motif: str, circuit: CircuitDeValidation) -> str:
    if len(motif.strip()) < circuit.motif_minimum:
        raise DecisionRefusee(
            f"le motif doit compter au moins {circuit.motif_minimum} caractères : il dit sur "
            "quel texte ou quelle décision la version repose."
        )
    return motif.strip()


def valider_une_version(
    parametres: Iterable[Parametre],
    *,
    code: str,
    applicable_du: date,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    circuit: CircuitDeValidation,
    depot,
) -> DecisionSurLeReferentiel:
    parametre = _parametre(parametres, code)
    _exiger(frozenset(permissions), circuit.etape(parametre.nature).valider, "valider")
    version = next((v for v in parametre.versions if v.applicable_du == applicable_du), None)
    if version is None:
        raise LookupError(f"« {code} » n'a aucune version datée du {applicable_du}.")
    if version.statut is StatutValidation.VALIDE:
        raise DecisionRefusee(
            f"la version du {applicable_du} de « {code} » est déjà validée "
            f"(par {version.valide_par}, le {version.valide_le})."
        )
    decision = DecisionSurLeReferentiel(
        identifiant=f"DEC-{uuid.uuid4().hex[:12]}",
        code=code,
        sorte=SorteDeDecision.VALIDATION,
        applicable_du=applicable_du,
        motif=_motif(motif, circuit),
        propose_par=par,
        propose_par_nom=nom,
        propose_le=le,
        statut=StatutDecision.APPLIQUEE,
        tranche_par=par,
        tranche_par_nom=nom,
        tranche_le=le,
        motif_de_la_decision=motif.strip(),
    )
    depot.enregistrer(decision)
    return decision


def proposer_une_version(
    parametres: Iterable[Parametre],
    *,
    code: str,
    valeur: bool | int | float | str,
    applicable_du: date,
    fondement: Fondement,
    note: str | None,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    circuit: CircuitDeValidation,
    depot,
) -> DecisionSurLeReferentiel:
    parametre = _parametre(parametres, code)
    _exiger(frozenset(permissions), circuit.etape(parametre.nature).proposer, "proposer")
    raison = valeur_conforme_a_l_unite(valeur, parametre.unite)
    if raison is not None:
        raise DecisionRefusee(f"« {code} » ({parametre.unite.value}) : {raison}")
    if applicable_du < le.date():
        raise DecisionRefusee(
            f"une nouvelle version ne prend pas effet dans le passé ({applicable_du}) : les "
            "calculs déjà rendus sur cette période changeraient sans que personne ne les relise."
        )
    derniere = max(v.applicable_du for v in parametre.versions)
    if applicable_du <= derniere:
        raise DecisionRefusee(
            f"une nouvelle version de « {code} » doit prendre effet après le {derniere}, date "
            "de sa version la plus récente. Dater avant réécrirait des calculs déjà rendus : "
            "c'est une rectification, qui passe par le référentiel commun."
        )
    en_attente = [
        d for d in depot.toutes() if d.code == code and d.statut is StatutDecision.PROPOSEE
    ]
    if en_attente:
        raise DecisionRefusee(
            f"une proposition sur « {code} » attend déjà d'être tranchée "
            f"({en_attente[0].identifiant})."
        )
    decision = DecisionSurLeReferentiel(
        identifiant=f"DEC-{uuid.uuid4().hex[:12]}",
        code=code,
        sorte=SorteDeDecision.NOUVELLE_VERSION,
        applicable_du=applicable_du,
        valeur=valeur,
        fondement=fondement,
        note=note,
        motif=_motif(motif, circuit),
        propose_par=par,
        propose_par_nom=nom,
        propose_le=le,
        statut=StatutDecision.PROPOSEE,
    )
    depot.enregistrer(decision)
    return decision


def trancher_une_proposition(
    parametres: Iterable[Parametre],
    *,
    identifiant: str,
    valider: bool,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    circuit: CircuitDeValidation,
    depot,
) -> DecisionSurLeReferentiel:
    decision = depot.trouver(identifiant)
    if decision is None:
        raise LookupError(f"décision « {identifiant} » inconnue.")
    parametre = _parametre(parametres, decision.code)
    _exiger(frozenset(permissions), circuit.etape(parametre.nature).valider, "valider")
    geste = decision.valider if valider else decision.refuser
    tranchee = geste(par=par, nom=nom, le=le, motif=motif, circuit=circuit, nature=parametre.nature)
    depot.enregistrer(tranchee)
    return tranchee


def retirer_une_decision(
    parametres: Iterable[Parametre],
    *,
    identifiant: str,
    a_compter_du: date,
    motif: str,
    par: str,
    nom: str,
    le: datetime,
    permissions: Iterable[str],
    circuit: CircuitDeValidation,
    depot,
) -> DecisionSurLeReferentiel:
    """Mettre fin, vers l'avant, à une décision validée (pas 98).

    Réservé à qui peut **valider** selon le circuit : revenir sur une valeur engage autant que
    l'arrêter. Un seul geste, sans second regard : le retrait rend la valeur du référentiel
    commun, qui a déjà été relue ; c'est le sens où l'erreur coûte le moins.
    """
    decision = depot.trouver(identifiant)
    if decision is None:
        raise LookupError(f"décision « {identifiant} » inconnue.")
    parametre = _parametre(parametres, decision.code)
    _exiger(frozenset(permissions), circuit.etape(parametre.nature).valider, "retirer")
    retiree = decision.retirer(
        par=par, nom=nom, le=le, a_compter_du=a_compter_du, motif=motif, circuit=circuit
    )
    depot.enregistrer(retiree)
    return retiree
