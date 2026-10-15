"""Table des versions de la DSF : préparée, validée, déposée.

⚠️ CE QUI MANQUAIT (14 octobre 2026). La liasse se calculait en direct ; rien ne
la figeait pour la faire valider et la déposer. Chaque version garde son contenu
et son empreinte : c'est elle que l'accusé de la DGI doit porter.

Revision ID: d7a4c2e81b53
Revises: c5e82a1d4f07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "d7a4c2e81b53"
down_revision: str | None = "c5e82a1d4f07"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "declaration_dsf",
        sa.Column("identifiant", sa.String(length=96), nullable=False),
        sa.Column("entreprise", sa.String(length=64), nullable=False),
        sa.Column("exercice", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("etat", sa.String(length=20), nullable=False),
        sa.Column("preparee_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_declaration_dsf"),
    )
    op.create_index(
        "ix_declaration_dsf_dossier", "declaration_dsf", ["locataire", "entreprise", "exercice"]
    )
    for instruction in instructions_activation_pour("declaration_dsf"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON declaration_dsf")
    op.drop_index("ix_declaration_dsf_dossier", table_name="declaration_dsf")
    op.drop_table("declaration_dsf")
