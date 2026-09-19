"""Décider sur le référentiel : valider, proposer, trancher (pas 95).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CES ROUTES VIVENT AU TRANSVERSE, ET NON SOUS /referentiel

Le référentiel appartient au **socle** : il dit ce que la loi prévoit, et il ne dépend de
personne. Le transverse le lit (pour la passerelle), et l'inverse créerait un cycle. Or
décider sur le référentiel suppose de savoir **qui parle** et **ce qu'il a le droit de
faire** : c'est exactement ce que porte le transverse.

Ces routes sont donc la porte du transverse vers le référentiel, comme le journal d'audit
est sa porte vers la trace : le transverse identifie, confronte la session au circuit du
cabinet, écrit au journal, et appelle les cas d'usage **par la surface publique** du
référentiel. Le référentiel, lui, conserve la décision et l'applique à la lecture.

⚠️ EFFET IMMÉDIAT, POUR CE CABINET SEULEMENT

Une version validée ici entre au calcul suivant de ce cabinet : contrôle de conformité,
échéancier, paie, score de risque. Les autres cabinets lisent le référentiel commun, intact.
Voir `referentiel/domaine/surcouche.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.referentiel.api import (
    DecisionRefusee,
    DecisionSurLeReferentiel,
    Fondement,
    HorsDuCircuit,
    StatutDecision,
    circuit_de_validation,
    decision_sans_objet,
    depot_des_decisions,
    parametres_communs,
    parametres_du_cabinet,
    proposer_une_version,
    retirer_une_decision,
    trancher_une_proposition,
    valider_une_version,
)
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
)
from app.contextes.transverse.application.autorisation import Acces
from app.contextes.transverse.domaine.roles import Permission
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/transverse/referentiel", tags=["Référentiel : décisions du cabinet"])


def _reserver_au_cabinet(acces: Acces) -> None:
    """Après `exiger(acces, Permission.LIRE_DOSSIER)`, écrit dans chaque route : l'adhérent lit
    un dossier, il ne décide pas du taux avec lequel on le contrôle. 403 : aucune donnée de
    dossier n'est en jeu. Les droits de proposer et de valider viennent ensuite du circuit.
    """
    if not acces.interne:
        raise HTTPException(
            status_code=403, detail="les décisions sur le référentiel sont réservées au cabinet."
        )


def _journaliser(acces: Acces, action: str, decision: DecisionSurLeReferentiel) -> None:
    """Une entrée par décision. `nature` y figure : les notifications désignent les valideurs
    selon la nature du paramètre (voir `notifications/abonnements.yaml`)."""
    nature = next((p.nature.value for p in parametres_communs() if p.code == decision.code), None)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="parametre",
        objet_id=decision.code,
        apres={
            "code": decision.code,
            "nature": nature,
            "decision": decision.identifiant,
            "sorte": decision.sorte.value,
            "applicable_du": decision.applicable_du.isoformat(),
            "valeur": None if decision.valeur is None else str(decision.valeur),
            "statut": decision.statut.value,
            "fin_d_effet": None
            if decision.fin_d_effet is None
            else decision.fin_d_effet.isoformat(),
        },
        motif=decision.motif_du_retrait or decision.motif_de_la_decision or decision.motif,
    )


def _traduire(refus: Exception) -> HTTPException:
    """403 hors du circuit, 404 inconnu, 409 refusé par l'état, les dates ou la valeur."""
    if isinstance(refus, HorsDuCircuit):
        return HTTPException(status_code=403, detail=str(refus))
    if isinstance(refus, LookupError):
        return HTTPException(status_code=404, detail=str(refus))
    return HTTPException(status_code=409, detail=str(refus))


# ── Lire ──────────────────────────────────────────────────────────────────────


class DroitsSurUneNature(BaseModel):
    peut_proposer: bool
    peut_valider: bool
    quatre_yeux: bool


class MonCircuit(BaseModel):
    """Ce que **cette session** peut décider, par nature de paramètre."""

    par_nature: dict[str, DroitsSurUneNature]
    motif_minimum: int
    source: str


@routeur.get("/circuit", summary="Ce que ma session peut décider sur le référentiel")
def lire_mon_circuit(acces: AccesRequis) -> MonCircuit:
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    circuit = circuit_de_validation()
    detenues = {p.value for p in acces.permissions}
    return MonCircuit(
        par_nature={
            nature.value: DroitsSurUneNature(
                peut_proposer=bool(detenues.intersection(etape.proposer)),
                peut_valider=bool(detenues.intersection(etape.valider)),
                quatre_yeux=etape.quatre_yeux,
            )
            for nature, etape in circuit.par_nature.items()
        },
        motif_minimum=circuit.motif_minimum,
        source=circuit.source,
    )


class EtatDUneDecision(BaseModel):
    decision: DecisionSurLeReferentiel
    nature: str | None
    #: Pourquoi une décision appliquée ne s'applique plus (le référentiel commun a changé).
    sans_objet: str | None = None


@routeur.get("/decisions", summary="Les décisions du cabinet sur le référentiel")
def lister_les_decisions(acces: AccesRequis) -> list[EtatDUneDecision]:
    """Toutes, de la plus récente à la plus ancienne. Rien n'est effacé : un refus se lit."""
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    communs = {p.code: p for p in parametres_communs()}
    etats = []
    for decision in reversed(depot_des_decisions().toutes()):
        parametre = communs.get(decision.code)
        etats.append(
            EtatDUneDecision(
                decision=decision,
                nature=parametre.nature.value if parametre else None,
                sans_objet=decision_sans_objet(parametre, decision)
                if decision.statut is StatutDecision.APPLIQUEE
                else None,
            )
        )
    return etats


# ── Décider ───────────────────────────────────────────────────────────────────


class DemandeDeValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Le texte consulté ou la décision prise. Recopié au journal d'audit.
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/parametres/{code}/versions/{applicable_du}/validation",
    summary="Valider une version livrée « à valider »",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Paramètre ou version inconnus"},
        409: {"description": "Version déjà validée, ou motif trop court"},
    },
)
def valider_une_version_livree(
    acces: AccesRequis, code: str, applicable_du: date, demande: DemandeDeValidation
) -> DecisionSurLeReferentiel:
    """La version devient validée **pour ce cabinet**, au nom de la personne qui valide."""
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    try:
        decision = valider_une_version(
            parametres_du_cabinet(),
            code=code,
            applicable_du=applicable_du,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            circuit=circuit_de_validation(),
            depot=depot_des_decisions(),
        )
    except (HorsDuCircuit, LookupError, DecisionRefusee) as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "referentiel.version_validee", decision)
    return decision


class FondementDemande(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texte: str = Field(min_length=5, max_length=1000)
    source: str = Field(min_length=5, max_length=1000)


class DemandeDeProposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valeur: bool | int | float | str
    applicable_du: date
    #: Obligatoire, comme pour toute version du fichier : sans lui, on ne saura pas quoi
    #: mettre à jour à la loi de finances suivante.
    fondement: FondementDemande
    note: str | None = Field(default=None, max_length=1000)
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/parametres/{code}/propositions",
    summary="Proposer une nouvelle version datée",
    status_code=201,
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Paramètre inconnu"},
        409: {"description": "Date, valeur ou proposition déjà en attente"},
    },
)
def proposer_une_nouvelle_version(
    acces: AccesRequis, code: str, demande: DemandeDeProposition
) -> DecisionSurLeReferentiel:
    """**Sans effet** tant qu'une personne désignée par le circuit ne l'a pas validée."""
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    try:
        decision = proposer_une_version(
            parametres_du_cabinet(),
            code=code,
            valeur=demande.valeur,
            applicable_du=demande.applicable_du,
            fondement=Fondement(texte=demande.fondement.texte, source=demande.fondement.source),
            note=demande.note,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            circuit=circuit_de_validation(),
            depot=depot_des_decisions(),
        )
    except (HorsDuCircuit, LookupError, DecisionRefusee) as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "referentiel.version_proposee", decision)
    return decision


class Tranchage(StrEnum):
    VALIDER = "VALIDER"
    REFUSER = "REFUSER"


class DemandeDeTranchage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Tranchage
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/decisions/{identifiant}/tranchage",
    summary="Valider ou refuser une proposition",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Décision inconnue"},
        409: {"description": "Déjà tranchée, ou proposée par la même personne"},
    },
)
def trancher(
    acces: AccesRequis, identifiant: str, demande: DemandeDeTranchage
) -> DecisionSurLeReferentiel:
    """Validée, elle entre au calcul suivant de ce cabinet. Refusée, elle reste au dépôt."""
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    try:
        decision = trancher_une_proposition(
            parametres_du_cabinet(),
            identifiant=identifiant,
            valider=demande.decision is Tranchage.VALIDER,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            circuit=circuit_de_validation(),
            depot=depot_des_decisions(),
        )
    except (HorsDuCircuit, LookupError, DecisionRefusee) as refus:
        raise _traduire(refus) from refus
    action = (
        "referentiel.proposition_validee"
        if demande.decision is Tranchage.VALIDER
        else "referentiel.proposition_refusee"
    )
    _journaliser(acces, action, decision)
    return decision


class DemandeDeRetrait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Aujourd'hui au plus tôt : un retrait ne réécrit pas les calculs déjà rendus.
    a_compter_du: date
    motif: str = Field(min_length=10, max_length=2000)


@routeur.post(
    "/decisions/{identifiant}/retrait",
    summary="Mettre fin à une décision validée, à compter d'une date",
    responses={
        403: {"description": "Hors du circuit du cabinet"},
        404: {"description": "Décision inconnue"},
        409: {"description": "Rien à retirer, date passée ou motif trop court"},
    },
)
def retirer(
    acces: AccesRequis, identifiant: str, demande: DemandeDeRetrait
) -> DecisionSurLeReferentiel:
    """Pas 98. À compter de la date, la valeur du référentiel commun reprend pour le cabinet.

    Sans ce geste, une décision validée par erreur était définitive : aucune route ne permettait
    d'y revenir, et la seule issue était d'éditer la base.
    """
    exiger(acces, Permission.LIRE_DOSSIER)
    _reserver_au_cabinet(acces)
    try:
        decision = retirer_une_decision(
            parametres_communs(),
            identifiant=identifiant,
            a_compter_du=demande.a_compter_du,
            motif=demande.motif,
            par=acces.compte,
            nom=acces.nom_complet,
            le=maintenant(),
            permissions={p.value for p in acces.permissions},
            circuit=circuit_de_validation(),
            depot=depot_des_decisions(),
        )
    except (HorsDuCircuit, LookupError, DecisionRefusee) as refus:
        raise _traduire(refus) from refus
    _journaliser(acces, "referentiel.decision_retiree", decision)
    return decision
