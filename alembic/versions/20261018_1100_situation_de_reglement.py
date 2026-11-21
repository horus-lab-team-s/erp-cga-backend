"""Règle de dépassement et grâces de règlement.

18 octobre 2026 : un dossier en retard d'honoraires au-delà de la règle du cabinet ne se
traite plus, sauf grâce accordée par le super administrateur.

Revision ID: d71b2e8c4a90
Revises: c3e9a5d27f41
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "d71b2e8c4a90"
down_revision: str | None = "c3e9a5d27f41"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "regle_de_depassement",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("fixee_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_regle_de_depassement"),
    )
    op.create_table(
        "grace_de_reglement",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("jusqu_au", sa.Date(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_grace_de_reglement"),
    )
    op.create_index("ix_grace_de_reglement_niu", "grace_de_reglement", ["locataire", "niu"])
    for table in ("regle_de_depassement", "grace_de_reglement"):
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in ("grace_de_reglement", "regle_de_depassement"):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_index("ix_grace_de_reglement_niu", table_name="grace_de_reglement")
    op.drop_table("grace_de_reglement")
    op.drop_table("regle_de_depassement")
