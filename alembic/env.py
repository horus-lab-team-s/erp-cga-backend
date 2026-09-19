"""Contexte de migration Alembic.

─────────────────────────────────────────────────────────────────────────────────
L'ADRESSE DE LA BASE VIENT DE LA CONFIGURATION, PAS DU FICHIER `.ini`

`sqlalchemy.url` dans `alembic.ini` est l'endroit où l'on met un mot de passe par
habitude, et où il finit versionné. Elle est donc lue ici depuis
`app.infrastructure.config`, qui la prend de l'environnement.

LES TABLES VIENNENT DU RECENSEMENT, PAS D'IMPORTS ÉPARS

`autogenerate` compare la base aux métadonnées **chargées**. Une table dont le
module n'a pas été importé n'existe pas de son point de vue : il produirait un
`DROP TABLE` pour une table parfaitement légitime.

Le recensement vit dans `app/tables.py`, et il est le seul endroit à tenir à
jour — voir son en-tête, et le test qui vérifie qu'il est complet.

`compare_type` EST ACTIVÉ

Sans lui, changer une colonne de `String(64)` à `String(120)` ne produit aucune
migration : le code attend une longueur, la base en applique une autre, et
l'écriture tronque ou échoue selon la version du serveur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.infrastructure.config import configuration

# Le recensement unique — voir `app/tables.py` pour le pourquoi.
from app.tables import METADONNEES

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", configuration().url_base_de_donnees)

metadonnees_cibles = METADONNEES


def hors_ligne() -> None:
    """Produit le SQL sans se connecter — pour relecture avant application."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=metadonnees_cibles,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def en_ligne() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connexion:
        context.configure(
            connection=connexion,
            target_metadata=metadonnees_cibles,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    hors_ligne()
else:
    en_ligne()
