"""API du contexte Conformité."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.config import configuration
from .donnees_demo import FACTURES_DEMO
from .modeles import FactureAControler, RapportConformite, Regle
from .moteur import MoteurConformite
from .presentation import Verdict, composer_verdict

routeur = APIRouter(prefix="/conformite", tags=["Conformité"])


@lru_cache
def moteur() -> MoteurConformite:
    return MoteurConformite.depuis_dossier(configuration().dossier_referentiel)


class ReponseControle(BaseModel):
    verdict: Verdict
    rapport: RapportConformite


@routeur.get("/regles", summary="Catalogue des règles de conformité")
def lister_regles(
    a_la_date: date | None = Query(
        None, description="Ne rend que les règles en vigueur à cette date"
    ),
) -> list[Regle]:
    regles = moteur().regles
    if a_la_date is not None:
        regles = [r for r in regles if r.en_vigueur(a_la_date)]
    return regles


@routeur.post("/controler", summary="Contrôler une facture")
def controler(
    facture: FactureAControler,
    a_la_date: date | None = Query(
        None,
        description=(
            "Date de contrôle. Par défaut la date d'émission de la facture : "
            "une facture de 2024 se contrôle avec les règles de 2024."
        ),
    ),
) -> ReponseControle:
    rapport = moteur().controler(facture, a_la_date)
    return ReponseControle(verdict=composer_verdict(rapport), rapport=rapport)


@routeur.get("/demonstration", summary="Références du jeu de démonstration")
def lister_demonstration() -> list[str]:
    return sorted(FACTURES_DEMO)


@routeur.get("/demonstration/{reference}", summary="Contrôler une facture de démonstration")
def controler_demonstration(reference: str) -> ReponseControle:
    facture = FACTURES_DEMO.get(reference)
    if facture is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"facture « {reference} » inconnue. "
                f"Disponibles : {', '.join(sorted(FACTURES_DEMO))}"
            ),
        )
    rapport = moteur().controler(facture)
    return ReponseControle(verdict=composer_verdict(rapport), rapport=rapport)
