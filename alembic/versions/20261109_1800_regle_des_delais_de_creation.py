"""La règle des délais de la création (lot 4, 9 novembre 2026).

Rédaction et validation des statuts, retrait des originaux : fixés par la direction, aucun par
défaut.

Revision ID: a5c9e3f17d84
Revises: f4b8d2e06c73
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a5c9e3f17d84"
down_revision: str | None = "f4b8d2e06c73"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "regle_des_delais_de_creation",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_regle_des_delais_de_creation"),
    )
    for instruction in instructions_activation_pour("regle_des_delais_de_creation"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON regle_des_delais_de_creation")
    op.drop_table("regle_des_delais_de_creation")
