"""Configuration de l'application."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Racine du dépôt : Backend_erp_cga/app/core/config.py → trois niveaux au-dessus.
RACINE_DEPOT = Path(__file__).resolve().parents[3]


class Configuration(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CGA_", env_file=".env", extra="ignore")

    nom_application: str = "Plateforme CGA Broad Range Consulting Group"
    environnement: str = "developpement"

    #: Le référentiel est aujourd'hui un dossier de fichiers YAML versionnés en Git.
    #: Cible : une table PostgreSQL éditable par le fiscaliste. Le service de lecture ne
    #: connaît pas l'origine — voir Docs/architecture/02-referentiel-normatif.md § 3.
    dossier_referentiel: Path = RACINE_DEPOT / "Docs" / "referentiel"

    #: Le contenu éditorial de la vitrine — articles, annonces, institutions.
    #: Volontairement **hors de Docs/** : ce n'est pas de la documentation de
    #: projet, c'est la matière que le cabinet édite lui-même. Un dossier à la
    #: racine se trouve sans explication, et se sauvegarde sans se demander ce
    #: qu'on emporte. Cible : une table PostgreSQL et un écran d'administration —
    #: seul l'adaptateur sortant du contexte L changera ce jour-là.
    dossier_contenu_vitrine: Path = RACINE_DEPOT / "Contenu_vitrine"

    origines_cors: list[str] = ["http://localhost:3000"]


@lru_cache
def configuration() -> Configuration:
    return Configuration()
