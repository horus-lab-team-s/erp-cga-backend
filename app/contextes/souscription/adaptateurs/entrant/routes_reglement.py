"""Qui est en règle, la règle de dépassement, les grâces, les révisions de tarif.

─────────────────────────────────────────────────────────────────────────────────
18 OCTOBRE 2026, POINTS 4a ET 5 DE LA LISTE DU GÉRANT

* la **situation** de chaque dossier (en règle, en retard, sous grâce, bloqué), pour
  que les responsables sachent qui est en règle : `LIRE_DOSSIER`, sur leur portée ;
* la **règle de dépassement** et les **grâces** : des faveurs, au super
  administrateur (`ACCORDER_FAVEURS`, second facteur et motif) ;
* la **révision** d'un tarif d'honoraires, à partir d'une date : `FIXER_LES_TARIFS`,
  la direction, comme le contrat lui-même.

Ce module pose aussi le contrôle que `exiger_dossier` consulte : un geste de
traitement sur un dossier BLOQUÉ rend 423 (voir `transverse/dependances.py`).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.domaine.honoraires import (
    RevisionDHonoraires,
    RevisionRefusee,
)
from app.contextes.souscription.domaine.situation_de_reglement import (
    REGLE_D_OFFICE,
    EtatDeReglement,
    GraceDeReglement,
    RegleDeDepassement,
    SituationDeReglement,
    situation_de_reglement,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    installer_le_controle_de_reglement,
    restreindre,
)
from app.partage.formats import montant_fcfa
from app.partage.horloge import aujourd_hui, maintenant

__all__ = ["routeur", "situation_du_dossier"]

routeur = APIRouter(prefix="/souscription", tags=["Honoraires des adhérents"])


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _regle(boutique) -> RegleDeDepassement:
    return boutique.regle_de_depassement.lire() or REGLE_D_OFFICE


def situation_du_dossier(niu: str, a_la_date: date | None = None) -> SituationDeReglement:
    boutique = _comptoir()
    return situation_de_reglement(
        niu,
        boutique.contrats_honoraires.lire(niu),
        boutique.reglements_honoraires.pour(niu),
        _regle(boutique),
        boutique.graces_de_reglement.pour(niu),
        a_la_date or aujourd_hui(),
    )


def _raison_du_blocage(niu: str) -> str | None:
    situation = situation_du_dossier(niu)
    if situation.traitable:
        return None
    return (
        f"Dossier non en règle : {montant_fcfa(situation.du)} d'honoraires dus, en "
        f"retard depuis le {situation.en_retard_depuis:%d/%m/%Y}. Le traitement est "
        "suspendu jusqu'au règlement, ou jusqu'à une grâce accordée par le super "
        "administrateur."
    )


installer_le_controle_de_reglement(_raison_du_blocage)


def _journal(acces, *, action: str, objet_id: str, apres: dict, motif: str) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=f"reglement.{action}",
        objet_type="reglement",
        objet_id=objet_id,
        apres=apres,
        motif=motif,
    )


# ── La règle de dépassement ──────────────────────────────────────────────────────


class RegleEnVigueur(BaseModel):
    regle: RegleDeDepassement
    #: Aucune règle fixée : le « d'office ».
    d_office: bool


@routeur.get("/reglement/regle", summary="La règle de dépassement du cabinet")
def lire_la_regle(acces: AccesRequis) -> RegleEnVigueur:
    if not acces.detient(Permission.ACCORDER_FAVEURS):
        exiger(acces, Permission.LIRE_DOSSIER)
    fixee = _comptoir().regle_de_depassement.lire()
    return RegleEnVigueur(regle=fixee or REGLE_D_OFFICE, d_office=fixee is None)


class RegleAFixer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delai_jours: int = Field(ge=0, le=365)
    motif: str = Field(min_length=10, max_length=500)


@routeur.put("/reglement/regle", summary="Fixer la règle de dépassement")
def fixer_la_regle(acces: AccesRequis, demande: RegleAFixer) -> RegleEnVigueur:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=demande.motif)
    regle = RegleDeDepassement(
        delai_jours=demande.delai_jours,
        motif=demande.motif,
        fixee_par=acces.compte,
        fixee_le=maintenant(),
    )
    _comptoir().regle_de_depassement.enregistrer(regle)
    _journal(
        acces,
        action="regle",
        objet_id="regle-de-depassement",
        apres={"delai_jours": regle.delai_jours},
        motif=demande.motif,
    )
    return RegleEnVigueur(regle=regle, d_office=False)


# ── Les situations ───────────────────────────────────────────────────────────────


class LigneDeSituation(BaseModel):
    denomination: str
    situation: SituationDeReglement


@routeur.get(
    "/reglement/situations",
    summary="Qui est en règle : la situation de chaque dossier sous contrat d'honoraires",
)
def lire_les_situations(acces: AccesRequis) -> list[LigneDeSituation]:
    exiger(acces, Permission.LIRE_DOSSIER)
    contrats = restreindre(acces, _comptoir().contrats_honoraires.lister(), lambda c: c.niu)
    ordre = {
        EtatDeReglement.BLOQUE: 0,
        EtatDeReglement.SOUS_GRACE: 1,
        EtatDeReglement.EN_RETARD: 2,
        EtatDeReglement.EN_REGLE: 3,
        EtatDeReglement.SANS_CONTRAT: 4,
    }
    lignes = [
        LigneDeSituation(denomination=c.denomination, situation=situation_du_dossier(c.niu))
        for c in contrats
    ]
    return sorted(lignes, key=lambda ligne: (ordre[ligne.situation.etat], ligne.denomination))


# ── Les grâces ───────────────────────────────────────────────────────────────────


class GracesDuDossier(BaseModel):
    situation: SituationDeReglement
    graces: list[GraceDeReglement]


@routeur.get("/honoraires/{niu}/graces", summary="La situation et les grâces d'un dossier")
def lire_les_graces(acces: AccesRequis, niu: str) -> GracesDuDossier:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return GracesDuDossier(
        situation=situation_du_dossier(niu),
        graces=_comptoir().graces_de_reglement.pour(niu),
    )


class GraceAAccorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jusqu_au: date
    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/honoraires/{niu}/graces", summary="Accorder une grâce de règlement", status_code=201
)
def accorder_une_grace(acces: AccesRequis, niu: str, demande: GraceAAccorder) -> GracesDuDossier:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=demande.motif)
    jour = aujourd_hui()
    if not jour <= demande.jusqu_au <= jour + timedelta(days=365):
        raise HTTPException(
            status_code=422,
            detail="une grâce court d'aujourd'hui à un an au plus : au-delà, c'est la règle "
            "qu'il faut changer.",
        )
    boutique = _comptoir()
    if boutique.contrats_honoraires.lire(niu) is None:
        raise HTTPException(status_code=404, detail=f"aucun contrat d'honoraires pour {niu}.")
    grace = GraceDeReglement(
        identifiant=f"GR-{uuid.uuid4().hex[:12]}",
        niu=niu,
        jusqu_au=demande.jusqu_au,
        motif=demande.motif,
        accordee_par=acces.compte,
        accordee_le=maintenant(),
    )
    boutique.graces_de_reglement.enregistrer(grace)
    _journal(
        acces,
        action="grace",
        objet_id=niu,
        apres={"grace": grace.identifiant, "jusqu_au": grace.jusqu_au.isoformat()},
        motif=demande.motif,
    )
    return GracesDuDossier(
        situation=situation_du_dossier(niu), graces=boutique.graces_de_reglement.pour(niu)
    )


class Levee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=500)


@routeur.post("/honoraires/{niu}/graces/{identifiant}/levee", summary="Lever une grâce")
def lever_une_grace(
    acces: AccesRequis, niu: str, identifiant: str, demande: Levee
) -> GracesDuDossier:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=demande.motif)
    boutique = _comptoir()
    grace = next(
        (g for g in boutique.graces_de_reglement.pour(niu) if g.identifiant == identifiant), None
    )
    if grace is None:
        raise HTTPException(status_code=404, detail=f"aucune grâce {identifiant} à ce dossier.")
    if grace.levee_le is not None:
        raise HTTPException(status_code=409, detail="cette grâce est déjà levée.")
    boutique.graces_de_reglement.enregistrer(
        GraceDeReglement.model_validate(
            {**grace.model_dump(), "levee_le": maintenant(), "levee_par": acces.compte}
        )
    )
    _journal(acces, action="levee", objet_id=niu, apres={"grace": identifiant}, motif=demande.motif)
    return GracesDuDossier(
        situation=situation_du_dossier(niu), graces=boutique.graces_de_reglement.pour(niu)
    )


# ── Les révisions de tarif (point 4a) ────────────────────────────────────────────


class RevisionAPorter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    montant: Decimal = Field(gt=0)
    a_partir_du: date
    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/honoraires/{niu}/contrat/revisions",
    summary="Réviser le tarif d'honoraires à partir d'une date",
    status_code=201,
)
def reviser_le_tarif(acces: AccesRequis, niu: str, demande: RevisionAPorter):
    exiger(acces, Permission.FIXER_LES_TARIFS)
    boutique = _comptoir()
    contrat = boutique.contrats_honoraires.lire(niu)
    if contrat is None:
        raise HTTPException(status_code=404, detail=f"aucun contrat d'honoraires pour {niu}.")
    revision = RevisionDHonoraires(
        montant=demande.montant,
        a_partir_du=demande.a_partir_du,
        motif=demande.motif,
        fixe_par=acces.compte,
        fixe_le=maintenant(),
    )
    try:
        revise = contrat.reviser(revision, boutique.reglements_honoraires.pour(niu))
    except RevisionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    boutique.contrats_honoraires.enregistrer(revise)
    _journal(
        acces,
        action="revision",
        objet_id=niu,
        apres={"montant": str(revision.montant), "a_partir_du": revision.a_partir_du.isoformat()},
        motif=demande.motif,
    )
    from app.contextes.souscription.adaptateurs.entrant.routes_honoraires import mes_honoraires

    return mes_honoraires(niu)
