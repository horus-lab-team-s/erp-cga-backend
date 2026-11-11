"""Arrêté des charges sociales d'un mois.

18 octobre 2026 : la paie propose l'IRPP, la CNPS et les autres retenues ; un collaborateur
arrête les montants. Une ligne par dossier et par mois.

Revision ID: e4a6c9b13d58
Revises: d71b2e8c4a90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e4a6c9b13d58"
down_revision: str | None = "d71b2e8c4a90"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "arrete_des_charges",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=20), nullable=False),
        sa.Column("periode", sa.String(length=7), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "entreprise", "periode", name="pk_arrete_des_charges"),
    )
    for instruction in instructions_activation_pour("arrete_des_charges"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON arrete_des_charges")
    op.drop_table("arrete_des_charges")
