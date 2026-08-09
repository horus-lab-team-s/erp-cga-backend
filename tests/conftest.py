from __future__ import annotations

from pathlib import Path

import pytest

from app.contexts.conformite.modeles import Regle
from app.contexts.conformite.moteur import MoteurConformite, charger_regles
from app.contexts.referentiel.service import ServiceParametres
from app.core.config import RACINE_DEPOT

REFERENTIEL = RACINE_DEPOT / "Docs" / "referentiel"


@pytest.fixture(scope="session")
def dossier_referentiel() -> Path:
    assert REFERENTIEL.is_dir(), f"référentiel introuvable : {REFERENTIEL}"
    return REFERENTIEL


@pytest.fixture(scope="session")
def parametres(dossier_referentiel: Path) -> ServiceParametres:
    return ServiceParametres.depuis_yaml(dossier_referentiel / "parametres.yaml")


@pytest.fixture(scope="session")
def regles(dossier_referentiel: Path) -> list[Regle]:
    return charger_regles(dossier_referentiel / "regles")


@pytest.fixture(scope="session")
def moteur(regles: list[Regle], parametres: ServiceParametres) -> MoteurConformite:
    return MoteurConformite(regles, parametres)
