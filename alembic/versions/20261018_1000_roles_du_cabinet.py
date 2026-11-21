"""Table des rôles réglés par le cabinet.

18 octobre 2026 : la matrice des rôles se règle par le super administrateur. Une ligne par
rôle livré retouché, et par rôle créé par le cabinet ; la matrice livrée reste dans le code.

Revision ID: c3e9a5d27f41
Revises: b8d1f4a73c20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c3e9a5d27f41"
down_revision: str | None = "b8d1f4a73c20"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "role_du_cabinet",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("libelle", sa.String(length=80), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("systeme", sa.Boolean(), nullable=False),
        sa.Column("externe", sa.Boolean(), nullable=False),
        sa.Column("reglee_par", sa.String(length=200), nullable=True),
        sa.Column("reglee_le", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("locataire", "code", name="pk_role_du_cabinet"),
    )
    for instruction in instructions_activation_pour("role_du_cabinet"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON role_du_cabinet")
    op.drop_table("role_du_cabinet")
