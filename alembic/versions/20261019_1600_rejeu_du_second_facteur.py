"""Un code du second facteur ne sert qu'une fois (19 octobre 2026).

Un code accepté restait valable quatre-vingt-dix secondes, sa tranche et ses deux
voisines : lu par-dessus une épaule, il renforçait une seconde session. La dernière
tranche acceptée est retenue sur le compte, et une tranche égale ou antérieure est
refusée.

Revision ID: d9b2c6e48f17
Revises: c4e7a9b13d58
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d9b2c6e48f17"
down_revision: str | None = "c4e7a9b13d58"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("compte", sa.Column("derniere_tranche_totp", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("compte", "derniere_tranche_totp")
