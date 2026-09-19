"""Table des rapports mensuels de la direction (pas 106).

Un rapport mensuel archivé : ses sections figées au moment de la génération et leur empreinte
SHA-256, aussi écrite au journal d'audit. Regénérer un mois crée une version suivante ; aucune
ligne n'est remplacée.

Clé composite `(locataire, identifiant)`.

Revision ID: a7c2e9f15b48
Revises: f1a5c8d49e36
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a7c2e9f15b48"
down_revision: str | None = "f1a5c8d49e36"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "rapport_mensuel",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=32), nullable=False),
        sa.Column("mois", sa.String(length=7), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("empreinte", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_rapport_mensuel"),
    )
    op.create_index(
        "ix_rapport_mensuel_mois", "rapport_mensuel", ["locataire", "mois", "version"]
    )
    for instruction in instructions_activation_pour("rapport_mensuel"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON rapport_mensuel")
    op.drop_index("ix_rapport_mensuel_mois", table_name="rapport_mensuel")
    op.drop_table("rapport_mensuel")
