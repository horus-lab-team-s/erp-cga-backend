"""Lecture automatique au dépôt : réglage du cabinet, accord de l'adhérent, lecture préparée.

18 octobre 2026 : l'agent lit au dépôt les pièces des adhérents consentants, sans jamais
bloquer le dépôt ; le collaborateur trouve la facture préremplie et des points à vérifier.

Revision ID: f2b8d4e61a73
Revises: e4a6c9b13d58
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "f2b8d4e61a73"
down_revision: str | None = "e4a6c9b13d58"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("reglage_de_lecture", "consentement_de_lecture", "lecture_preparee")


def upgrade() -> None:
    op.create_table(
        "reglage_de_lecture",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_reglage_de_lecture"),
    )
    op.create_table(
        "consentement_de_lecture",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "niu", name="pk_consentement_de_lecture"),
    )
    op.create_table(
        "lecture_preparee",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("piece", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "piece", name="pk_lecture_preparee"),
    )
    for table in _TABLES:
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
        op.drop_table(table)
