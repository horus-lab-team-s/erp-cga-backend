"""${message}

Révision : ${up_revision}
Précédente : ${down_revision | comma,n}
Créée le : ${create_date}
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """⚠️ Une migration qui supprime des données ne se défait pas : `downgrade`
    rétablit le schéma, jamais le contenu. Sur les tables append-only — journal
    d'audit, accusés de réception —, il n'y a **rien** à défaire."""
    ${downgrades if downgrades else "pass"}
