"""Table des missions de prestation ponctuelle : ouverte, à valider, livrée.

⚠️ CE QUI MANQUAIT (15 octobre 2026). Le catalogue vendait une « prestation
ponctuelle » ; après le paiement, rien ne s'ouvrait. La mission porte le document,
les propositions de l'agent et les décisions du juriste, jusqu'au livrable.

Revision ID: e3b9f6a24c18
Revises: d7a4c2e81b53
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e3b9f6a24c18"
down_revision: str | None = "d7a4c2e81b53"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mission",
        sa.Column("identifiant", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("etat", sa.String(length=16), nullable=False),
        sa.Column("ouverte_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_mission"),
    )
    op.create_index("ix_mission_etat", "mission", ["locataire", "etat", "ouverte_le"])
    for instruction in instructions_activation_pour("mission"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON mission")
    op.drop_index("ix_mission_etat", table_name="mission")
    op.drop_table("mission")
