"""Table des décisions de direction sur un dossier à risque (pas 100).

Le score de risque se calcule et ne se stocke pas. Cette table porte ce que la direction a
décidé devant lui : la mesure, le motif, l'échéance, la personne, et l'instantané du score
sur lequel la décision s'est fondée. Une décision close reste une ligne.

Clé composite `(locataire, identifiant)` : une décision ne vaut que pour son cabinet.

Revision ID: d8e3a6b27c14
Revises: c5d2f9a38b61
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "d8e3a6b27c14"
down_revision: str | None = "c5d2f9a38b61"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decision_de_direction",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=120), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("prise_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_decision_de_direction"),
    )
    op.create_index(
        "ix_decision_direction_dossier",
        "decision_de_direction",
        ["locataire", "dossier", "prise_le"],
    )
    op.create_index(
        "ix_decision_direction_statut", "decision_de_direction", ["locataire", "statut"]
    )
    for instruction in instructions_activation_pour("decision_de_direction"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON decision_de_direction")
    op.drop_index("ix_decision_direction_statut", table_name="decision_de_direction")
    op.drop_index("ix_decision_direction_dossier", table_name="decision_de_direction")
    op.drop_table("decision_de_direction")
