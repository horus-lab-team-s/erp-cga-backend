"""Versements du parcours : frais de traitement, tranches, et leur règle.

9 novembre 2026 (réunion du 4 novembre, demande 21) : la proforma se règle en plusieurs
tranches, les frais de traitement versés à la soumission en sont déduits, et le super
administrateur fixe les frais par service et le seuil de démarrage.

Revision ID: e3a7c1d95b62
Revises: d9b2c6e48f17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e3a7c1d95b62"
down_revision: str | None = "d9b2c6e48f17"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("regle_des_versements", "versement_du_parcours")


def upgrade() -> None:
    op.create_table(
        "regle_des_versements",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("fixee_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_regle_des_versements"),
    )
    op.create_table(
        "versement_du_parcours",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("reference_externe", sa.String(length=160), nullable=False),
        sa.Column("constate_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_versement_du_parcours"),
        sa.UniqueConstraint(
            "locataire", "reference_externe", name="uq_versement_du_parcours_reference"
        ),
    )
    op.create_index(
        "ix_versement_du_parcours_dossier", "versement_du_parcours", ["locataire", "dossier"]
    )
    for table in _TABLES:
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_index("ix_versement_du_parcours_dossier", table_name="versement_du_parcours")
    op.drop_table("versement_du_parcours")
    op.drop_table("regle_des_versements")
