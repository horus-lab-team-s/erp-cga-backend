"""Identité fiscale du cabinet, et factures définitives des honoraires.

18 octobre 2026 : la direction saisit le NIU, le RCCM et le régime de TVA quand elle les
a ; la facture définitive ne s'émet qu'avec eux, et se fige à son émission.

Revision ID: a7c3e5f91b24
Revises: f2b8d4e61a73
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a7c3e5f91b24"
down_revision: str | None = "f2b8d4e61a73"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("identite_fiscale_du_cabinet", "facture_d_honoraires")


def upgrade() -> None:
    op.create_table(
        "identite_fiscale_du_cabinet",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_identite_fiscale_du_cabinet"),
    )
    op.create_table(
        "facture_d_honoraires",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("numero", sa.String(length=32), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("periode", sa.Date(), nullable=False),
        sa.Column("annee", sa.Integer(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "numero", name="pk_facture_d_honoraires"),
        sa.UniqueConstraint(
            "locataire", "niu", "periode", name="uq_facture_d_honoraires_echeance"
        ),
    )
    for table in _TABLES:
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
        op.drop_table(table)
