"""Table des appels de fonds de tiers : ce que le cabinet détient pour l'administration.

⚠️ CE QUI MANQUAIT (14 octobre 2026). Décision du cabinet : l'adhérent qui a signé
un mandat de paiement remet au cabinet les fonds de sa TVA ou de sa CNPS, et le
cabinet reverse. Cet argent n'est pas au cabinet : il a sa table, à part des
honoraires, pour qu'aucune requête ne les additionne.

Revision ID: c5e82a1d4f07
Revises: b3d71f0c2e94
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c5e82a1d4f07"
down_revision: str | None = "b3d71f0c2e94"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "appel_de_fonds",
        sa.Column("identifiant", sa.String(length=96), nullable=False),
        sa.Column("niu", sa.String(length=64), nullable=False),
        sa.Column("etat", sa.String(length=20), nullable=False),
        sa.Column("arrete_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_appel_de_fonds"),
    )
    op.create_index("ix_appel_de_fonds_dossier", "appel_de_fonds", ["locataire", "niu", "etat"])
    for instruction in instructions_activation_pour("appel_de_fonds"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON appel_de_fonds")
    op.drop_index("ix_appel_de_fonds_dossier", table_name="appel_de_fonds")
    op.drop_table("appel_de_fonds")
