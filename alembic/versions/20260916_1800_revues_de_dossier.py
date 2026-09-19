"""Table des revues de mois transmis (pas 102).

Un mois d'un dossier transmis par le comptable au réviseur : l'échantillon figé à la
transmission, les remarques rattachées à leurs objets, l'histoire des passages de relais.
Une revue validée reste une ligne : c'est la trace du second regard.

Clé composite `(locataire, entreprise, identifiant)`.

Revision ID: f1a5c8d49e36
Revises: e9f4b7c38d25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "f1a5c8d49e36"
down_revision: str | None = "e9f4b7c38d25"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "revue_de_dossier",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("entreprise", sa.String(length=20), nullable=False),
        sa.Column("identifiant", sa.String(length=40), nullable=False),
        sa.Column("du", sa.Date(), nullable=False),
        sa.Column("au", sa.Date(), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(
            "locataire", "entreprise", "identifiant", name="pk_revue_de_dossier"
        ),
    )
    op.create_index("ix_revue_statut", "revue_de_dossier", ["locataire", "statut", "du"])
    for instruction in instructions_activation_pour("revue_de_dossier"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON revue_de_dossier")
    op.drop_index("ix_revue_statut", table_name="revue_de_dossier")
    op.drop_table("revue_de_dossier")
