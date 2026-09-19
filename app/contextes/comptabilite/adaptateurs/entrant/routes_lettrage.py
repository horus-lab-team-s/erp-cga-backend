"""Le lettrage des comptes de tiers (pas 108), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
DEUX GESTES, UNE PERMISSION

Lettrer et délettrer demandent `SAISIR_ECRITURE` : c'est le travail courant du comptable sur
ses comptes, et un rôle qui ne saisit pas n'apparie pas davantage. Les deux gestes sont inscrits
au journal d'audit : un lettrage fait disparaître des lignes de ce qui « reste ouvert », et il
faut pouvoir dire qui l'a décidé.

La lecture n'est pas ici : le grand livre (`GET /grand-livre/{compte}`) porte la lettre de chaque
ligne, son origine et l'identifiant du lettrage à défaire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.comptabilite.adaptateurs.entrant.routes_http import _plan, depot
from app.contextes.comptabilite.adaptateurs.sortant.depots_lettrages import depot_des_lettrages
from app.contextes.comptabilite.adaptateurs.sortant.lettrage_yaml import (
    charger_les_reglages_du_lettrage,
)
from app.contextes.comptabilite.application.lettrage import lettrer
from app.contextes.comptabilite.domaine.lettrage import (
    LettrageDeLignes,
    LettrageRefuse,
    ReferenceDeLigne,
)
from app.contextes.transverse.api import Acces, AccesRequis, Permission, atelier, exiger_dossier
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · lettrage"])


def _journaliser(acces: Acces, action: str, lettrage: LettrageDeLignes) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="lettrage",
        objet_id=lettrage.identifiant,
        apres={
            "dossier": lettrage.dossier,
            "compte": lettrage.compte,
            "lettre": lettrage.lettre,
            "lignes": [f"{r.cle_ecriture}#{r.rang + 1}" for r in lettrage.lignes],
            "total_debit": str(lettrage.total_debit),
            "total_credit": str(lettrage.total_credit),
            "par": acces.nom_complet,
        },
    )


class DemandeDeLettrage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exercice: str = Field(min_length=4, max_length=16)
    compte: str = Field(pattern=r"^[1-9][0-9]{0,7}$")
    lignes: list[ReferenceDeLigne] = Field(min_length=2, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/lettrages",
    summary="Lettrer des lignes d'un compte de tiers qui se soldent",
    status_code=201,
    responses={422: {"description": "Sélection qui ne se solde pas, ou ligne non lettrable"}},
)
def lettrer_des_lignes(
    acces: AccesRequis, entreprise: str, demande: DemandeDeLettrage
) -> LettrageDeLignes:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    depot_lettrages = depot_des_lettrages()
    try:
        lettrage = lettrer(
            dossier=entreprise,
            exercice=demande.exercice,
            compte=demande.compte,
            references=demande.lignes,
            ecritures_de_l_exercice=depot(entreprise).lister(demande.exercice),
            plan=_plan.charger(),
            lettrages_du_compte=depot_lettrages.du_compte(
                entreprise, demande.exercice, demande.compte
            ),
            reglages=charger_les_reglages_du_lettrage(configuration().dossier_referentiel),
            par=acces.compte,
            le=maintenant(),
        )
    except LettrageRefuse as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    depot_lettrages.enregistrer(lettrage)
    _journaliser(acces, "comptabilite.lettrage_fait", lettrage)
    return lettrage


@routeur.post(
    "/dossiers/{entreprise}/lettrages/{identifiant}/delettrage",
    summary="Défaire un lettrage : les lignes redeviennent ouvertes",
    responses={404: {"description": "Lettrage inconnu"}, 422: {"description": "Déjà défait"}},
)
def delettrer(
    acces: AccesRequis,
    entreprise: str,
    identifiant: str,
    exercice: str = Query(..., min_length=4, max_length=16),
) -> LettrageDeLignes:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    depot_lettrages = depot_des_lettrages()
    lettrage = next(
        (
            l_
            for l_ in depot_lettrages.du_dossier(entreprise, exercice)
            if l_.identifiant == identifiant
        ),
        None,
    )
    if lettrage is None:
        raise HTTPException(status_code=404, detail=f"lettrage « {identifiant} » inconnu.")
    try:
        defait = lettrage.defaire(par=acces.compte, le=maintenant())
    except LettrageRefuse as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    depot_lettrages.enregistrer(defait)
    _journaliser(acces, "comptabilite.lettrage_defait", defait)
    return defait


__all__ = ["routeur"]
