"""Table des règles de conformité construites par un cabinet (pas 97).

Les règles du référentiel commun restent des fichiers. Cette table porte, par cabinet, les
règles construites à l'écran : leur construction, leur essai sur les factures, et leur
décision. Validées, elles entrent au moteur du seul cabinet qui les a validées.

Clé composite `(locataire, identifiant)` : une règle du cabinet ne vaut que pour lui.

Revision ID: c5d2f9a38b61
Revises: b7c3e8f14a52
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c5d2f9a38b61"
down_revision: str | None = "b7c3e8f14a52"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "proposition_de_regle",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=40), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("propose_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_proposition_de_regle"),
    )
    op.create_index(
        "ix_proposition_regle_statut",
        "proposition_de_regle",
        ["locataire", "statut", "propose_le"],
    )
    for instruction in instructions_activation_pour("proposition_de_regle"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON proposition_de_regle")
    op.drop_index("ix_proposition_regle_statut", table_name="proposition_de_regle")
    op.drop_table("proposition_de_regle")
