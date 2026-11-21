"""Table du registre des immobilisations.

17 octobre 2026 : une fiche par bien, la dotation calculée et passée en écriture, et le
tableau des amortissements produit pour la DSF.

Revision ID: b8d1f4a73c20
Revises: a4c7e2f91b36
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "b8d1f4a73c20"
down_revision: str | None = "a4c7e2f91b36"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "immobilisation",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("categorie", sa.String(length=4), nullable=False),
        sa.Column("mise_en_service", sa.Date(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "entreprise", "code", name="pk_immobilisation"),
    )
    op.create_index(
        "ix_immobilisation_categorie", "immobilisation", ["locataire", "entreprise", "categorie"]
    )
    for instruction in instructions_activation_pour("immobilisation"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON immobilisation")
    op.drop_index("ix_immobilisation_categorie", table_name="immobilisation")
    op.drop_table("immobilisation")
