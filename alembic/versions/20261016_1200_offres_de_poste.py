"""Tables des offres de poste du cabinet et des candidatures reçues.

16 octobre 2026 : le cabinet publie ses offres sur le site ; le candidat lit les
qualifications, puis postule avec son CV.

Revision ID: a4c7e2f91b36
Revises: e3b9f6a24c18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a4c7e2f91b36"
down_revision: str | None = "e3b9f6a24c18"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "offre_de_poste",
        sa.Column("identifiant", sa.String(length=32), nullable=False),
        sa.Column("etat", sa.String(length=16), nullable=False),
        sa.Column("creee_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_offre_de_poste"),
    )
    op.create_index(
        "ix_offre_de_poste_etat", "offre_de_poste", ["locataire", "etat", "creee_le"]
    )
    op.create_table(
        "candidature",
        sa.Column("identifiant", sa.String(length=32), nullable=False),
        sa.Column("offre", sa.String(length=32), nullable=False),
        sa.Column("recue_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_candidature"),
    )
    op.create_index("ix_candidature_offre", "candidature", ["locataire", "offre", "recue_le"])
    for table in ("offre_de_poste", "candidature"):
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in ("candidature", "offre_de_poste"):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_index("ix_candidature_offre", table_name="candidature")
    op.drop_table("candidature")
    op.drop_index("ix_offre_de_poste_etat", table_name="offre_de_poste")
    op.drop_table("offre_de_poste")
