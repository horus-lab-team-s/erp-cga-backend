"""API du contexte Conformité."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.contextes.conformite.adaptateurs.entrant.presentateur_verdict import (
    Verdict,
    composer_verdict,
)
from app.contextes.conformite.adaptateurs.sortant.depot_regles_yaml import DepotReglesYaml
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.domaine.entites import FactureAControler, RapportConformite, Regle
from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/conformite", tags=["Conformité"])


@lru_cache
def moteur() -> MoteurConformite:
    """Assemble le moteur à partir des dépôts YAML.

    C'est ici, dans l'adaptateur entrant, que se fait le câblage : le cas d'usage
    reçoit ses dépendances et ignore d'où elles viennent. Basculer vers PostgreSQL
    ne changera que ces trois lignes.
    """
    referentiel = configuration().dossier_referentiel
    return MoteurConformite(
        regles=DepotReglesYaml(referentiel / "regles").charger(),
        parametres=ServiceParametres.depuis_depot(
            DepotParametresYaml(referentiel / "parametres.yaml")
        ),
    )


class ReponseControle(BaseModel):
    """Ce dont l'écran E02 a besoin, en un appel.

    La facture est renvoyée avec le rapport : la fiche § 8.2 impose d'afficher les
    données extraites — fournisseur, NIU, date, montants, mode de règlement — à côté
    des constats, et de pouvoir les corriger. Les séparer en deux appels obligerait
    l'écran à recoller deux états qui doivent rester cohérents.
    """

    facture: FactureAControler
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
    return ReponseControle(
        facture=facture, verdict=composer_verdict(rapport), rapport=rapport
    )


@routeur.get("/demonstration", summary="Références du jeu de démonstration")
def lister_demonstration() -> list[str]:
    return sorted(FACTURES_DEMO)


@routeur.get(
    "/demonstration/rapports",
    summary="Contrôler tout le flux entrant de démonstration",
    description=(
        "Rend le contrôle de chaque facture du jeu de démonstration. La boîte de réception "
        "affiche la pastille de conformité sur chaque ligne : la peupler par appels unitaires "
        "coûterait un aller-retour par ligne, sur des connexions où chacun se paie."
    ),
)
def controler_tout() -> list[ReponseControle]:
    moteur_ = moteur()
    rapports = []
    for reference in sorted(FACTURES_DEMO):
        facture = FACTURES_DEMO[reference]
        rapport = moteur_.controler(facture)
        rapports.append(
            ReponseControle(
                facture=facture, verdict=composer_verdict(rapport), rapport=rapport
            )
        )
    return rapports


# Déclaré APRÈS /demonstration/rapports : sinon « rapports » serait capturé comme
# une référence de facture.
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
    return ReponseControle(
        facture=facture, verdict=composer_verdict(rapport), rapport=rapport
    )
