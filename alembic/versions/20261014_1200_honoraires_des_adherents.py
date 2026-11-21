"""Tables des honoraires des adhérents : le contrat, et les règlements.

⚠️ CE QUI MANQUAIT (14 octobre 2026). Le montant convenu avec un adhérent n'était
écrit nulle part : son espace ne pouvait rien lui dire de ce qu'il devait, et la
caisse ne pouvait rien constater d'autre qu'une proforma.

Le contrat : un par entreprise et par cabinet, le dernier fait foi. Les
règlements : un par période, et la contrainte `uq_reglement_honoraires_periode`
est la garde du double règlement (mobile money et guichet pour la même période).
L'échéancier, lui, se calcule : il n'a pas de table.

Revision ID: b3d71f0c2e94
Revises: a1c8e5f30927
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "b3d71f0c2e94"
down_revision: str | None = "a1c8e5f30927"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "contrat_honoraires",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("fixe_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "niu", name="pk_contrat_honoraires"),
    )
    op.create_table(
        "reglement_honoraires",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("periode", sa.Date(), nullable=False),
        sa.Column("constate_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_reglement_honoraires"),
        sa.UniqueConstraint(
            "locataire", "niu", "periode", name="uq_reglement_honoraires_periode"
        ),
    )
    op.create_index(
        "ix_reglement_honoraires_constate",
        "reglement_honoraires",
        ["locataire", "constate_le"],
    )
    for table in ("contrat_honoraires", "reglement_honoraires"):
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in ("reglement_honoraires", "contrat_honoraires"):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_index("ix_reglement_honoraires_constate", table_name="reglement_honoraires")
    op.drop_table("reglement_honoraires")
    op.drop_table("contrat_honoraires")
