"""Table des lettrages de lignes (pas 108).

Le lettrage apparie, sur un compte de tiers, les mouvements qui se soldent. Il vit à part des
écritures, qui sont intangibles. Un lettrage défait reste une ligne : sa lettre ne se réutilise
jamais.

Clé composite `(locataire, entreprise, identifiant)`.

Revision ID: b3d8f1a26c59
Revises: a7c2e9f15b48
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "b3d8f1a26c59"
down_revision: str | None = "a7c2e9f15b48"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "lettrage",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=20), nullable=False),
        sa.Column("identifiant", sa.String(length=40), nullable=False),
        sa.Column("exercice", sa.String(length=16), nullable=False),
        sa.Column("compte", sa.String(length=8), nullable=False),
        sa.Column("statut", sa.String(length=8), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "entreprise", "identifiant", name="pk_lettrage"),
    )
    op.create_index(
        "ix_lettrage_compte", "lettrage", ["locataire", "entreprise", "exercice", "compte"]
    )
    for instruction in instructions_activation_pour("lettrage"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON lettrage")
    op.drop_index("ix_lettrage_compte", table_name="lettrage")
    op.drop_table("lettrage")
