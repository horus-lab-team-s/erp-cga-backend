"""API du contexte Référentiel normatif."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.contextes.referentiel.api import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ParametreResolu,
    ServiceParametres,
    service_parametres,
)

routeur = APIRouter(prefix="/referentiel", tags=["Référentiel normatif"])


def service() -> ServiceParametres:
    # ⚠️ Pas 95 : le référentiel **du cabinet**, par le point de montage unique, et non plus
    # le fichier commun mémoïsé ici. Voir `service_parametres` dans `referentiel/api.py`.
    return service_parametres()


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


# ── La géographie du Cameroun (8 novembre 2026) ─────────────────────────────


@routeur.get(
    "/geographie/decoupage",
    summary="Régions, départements et arrondissements du Cameroun",
    description=(
        "Le découpage officiel de l'INC (10 régions, 58 départements, 360 arrondissements), "
        "avec ses codes. Publique : le site la sert au formulaire de création. `cle` est la "
        "valeur de la question `region_siege`."
    ),
)
def lire_le_decoupage() -> dict:
    from app.contextes.referentiel.api import geographie

    geo = geographie()
    return {"regions": geo.decoupage, "source": geo.sources.get("decoupage", "")}


@routeur.get(
    "/geographie/arrondissements/{code}/lieux",
    summary="Villes, localités et quartiers d'un arrondissement",
    description=(
        "Les lieux d'OpenStreetMap rattachés à cet arrondissement. ⚠️ Les quartiers sont "
        "PARTIELS : le formulaire laisse toujours écrire un quartier absent de la liste."
    ),
    responses={404: {"description": "Arrondissement inconnu"}},
)
def lire_les_lieux(code: str) -> dict:
    from app.contextes.referentiel.api import geographie

    geo = geographie()
    if not geo.arrondissement_connu(code):
        raise HTTPException(status_code=404, detail=f"arrondissement « {code} » inconnu.")
    lieux = geo.lieux.get(code, {"villes": [], "localites": [], "quartiers": []})
    return {"arrondissement": code, **lieux}
