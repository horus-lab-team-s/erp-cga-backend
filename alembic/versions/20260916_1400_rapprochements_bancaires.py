"""Table des rapprochements bancaires (pas 101).

Un relevé importé et ce que le comptable en a fait : appariements avec les lignes
d'écriture, justifications des lignes sans écriture, état de rapprochement arrêté. La table
des écritures n'est pas touchée : le rapprochement les désigne, il ne les marque pas.

Clé composite `(locataire, entreprise, identifiant)`.

Revision ID: e9f4b7c38d25
Revises: d8e3a6b27c14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e9f4b7c38d25"
down_revision: str | None = "d8e3a6b27c14"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "rapprochement_bancaire",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=20), nullable=False),
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("journal", sa.String(length=10), nullable=False),
        sa.Column("du", sa.Date(), nullable=False),
        sa.Column("au", sa.Date(), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(
            "locataire", "entreprise", "identifiant", name="pk_rapprochement_bancaire"
        ),
    )
    op.create_index(
        "ix_rapprochement_periode",
        "rapprochement_bancaire",
        ["locataire", "entreprise", "journal", "au"],
    )
    for instruction in instructions_activation_pour("rapprochement_bancaire"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON rapprochement_bancaire")
    op.drop_index("ix_rapprochement_periode", table_name="rapprochement_bancaire")
    op.drop_table("rapprochement_bancaire")
