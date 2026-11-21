"""La clé du second facteur attend son premier code (19 octobre 2026).

Le compte s'enrôlait à l'affichage de la clé : une page perdue avant que la clé soit
saisie dans l'application, et le titulaire portait un second facteur que personne ne
connaissait. La clé attend désormais dans cette colonne, scellée comme `secret_totp`,
jusqu'au premier code juste.

Revision ID: c4e7a9b13d58
Revises: b8d4f6a02c35
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c4e7a9b13d58"
down_revision: str | None = "b8d4f6a02c35"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "compte", sa.Column("secret_totp_en_attente", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("compte", "secret_totp_en_attente")
