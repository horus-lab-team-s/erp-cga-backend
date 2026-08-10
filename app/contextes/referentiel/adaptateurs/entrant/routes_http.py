"""API du contexte Référentiel normatif."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    DepotParametresYaml,
    ParametreInconnu,
    ParametreResolu,
    ServiceParametres,
)
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/referentiel", tags=["Référentiel normatif"])


@lru_cache
def service() -> ServiceParametres:
    depot = DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    return ServiceParametres.depuis_depot(depot)


class EtatValidation(BaseModel):
    a_la_date: date
    total: int
    non_valides: list[str]
    opposable: bool


@routeur.get("/parametres", summary="Codes des paramètres du référentiel")
def lister_codes() -> list[str]:
    return service().codes


@routeur.get(
    "/parametres/{code}",
    summary="Résoudre un paramètre à une date",
    description=(
        "Toute lecture se fait à une date. Il n'existe volontairement aucune façon de lire "
        "« la valeur courante » sans préciser laquelle : une facture de 2024 se contrôle avec "
        "les règles de 2024."
    ),
)
def resoudre(
    code: str,
    a_la_date: date = Query(..., description="Date d'effet à laquelle lire le paramètre"),
) -> ParametreResolu:
    try:
        return service().resoudre(code, a_la_date)
    except ParametreInconnu as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AucuneVersionApplicable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@routeur.get(
    "/validation",
    summary="État de validation du référentiel",
    description=(
        "Tant que des paramètres restent au statut A_VALIDER, aucun chiffre produit par la "
        "plateforme n'est opposable. Voir Docs/architecture/09-questions-ouvertes.md Q2."
    ),
)
def etat_validation(a_la_date: date = Query(...)) -> EtatValidation:
    parametres = service()
    non_valides = parametres.codes_non_valides(a_la_date)
    return EtatValidation(
        a_la_date=a_la_date,
        total=len(parametres.codes),
        non_valides=non_valides,
        opposable=not non_valides,
    )
