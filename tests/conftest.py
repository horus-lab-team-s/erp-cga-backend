from __future__ import annotations

from pathlib import Path

import pytest

from app.contextes.conformite.api import DepotReglesYaml, MoteurConformite
from app.contextes.conformite.domaine.entites import Regle
from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
from app.infrastructure.config import RACINE_DEPOT

REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel"


@pytest.fixture(scope="session")
def dossier_referentiel() -> Path:
    assert REFERENTIEL.is_dir(), f"référentiel introuvable : {REFERENTIEL}"
    return REFERENTIEL


@pytest.fixture(scope="session")
def parametres(dossier_referentiel: Path) -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(dossier_referentiel / "parametres.yaml")
    )


@pytest.fixture(scope="session")
def regles(dossier_referentiel: Path) -> list[Regle]:
    return DepotReglesYaml(dossier_referentiel / "regles").charger()


@pytest.fixture(scope="session")
def moteur(regles: list[Regle], parametres: ServiceParametres) -> MoteurConformite:
    return MoteurConformite(regles, parametres)
