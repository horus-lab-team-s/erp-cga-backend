"""Table de lecture des notifications : jusqu'où chaque compte a lu (pas 94).

⚠️ **Les notifications elles-mêmes n'ont pas de table.** Elles sont lues au journal
d'audit à travers les abonnements du référentiel. Seule la position de lecture est une
donnée propre au compte : un rang du journal, unique et croissant.

La clé est composite, `(locataire, compte)` : un identifiant de compte et un rang sont
propres au cabinet. Sixième application de la règle établie au pas 17.

Revision ID: a4b9d2e63f10
Revises: f3a8c5d71e29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a4b9d2e63f10"
down_revision: str | None = "f3a8c5d71e29"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "lecture_des_notifications",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("compte", sa.String(length=64), nullable=False),
        sa.Column("lu_jusqu_au_rang", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "compte", name="pk_lecture_des_notifications"),
    )
    for instruction in instructions_activation_pour("lecture_des_notifications"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON lecture_des_notifications")
    op.drop_table("lecture_des_notifications")
