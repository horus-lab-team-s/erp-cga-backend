"""Table des mandats, et le mandat porté par le journal d'audit (pas 127).

Un mandat autorise les comptes d'un locataire à agir dans le périmètre d'un autre. Il est
rangé **chez le mandant**, celui qui l'accorde et dont les données sont en jeu : c'est dans
son périmètre que la vérification a lieu.

La colonne `mandat` du journal d'audit dit au titre de quoi une action venue d'ailleurs a
été écrite. Elle est facultative : la très grande majorité des actions sont exercées par un
locataire chez lui, et « aucun mandat » n'est pas une valeur, c'est une absence.

⚠️ **Ajouter cette colonne ne casse aucune empreinte déjà écrite.** Le corps canonique que
le journal hache ne porte la clé `mandat` que lorsqu'un mandat existe : les entrées
antérieures se recalculent exactement comme avant.

Revision ID: c4e9a2b73d61
Revises: b3d8f1a26c59
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c4e9a2b73d61"
down_revision: str | None = "b3d8f1a26c59"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mandat",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("mandataire", sa.String(length=64), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("comptes", sa.JSON(), nullable=True),
        sa.Column("debut", sa.Date(), nullable=False),
        sa.Column("fin", sa.Date(), nullable=True),
        sa.Column("motif", sa.String(length=32), nullable=False),
        sa.Column("accorde_par", sa.String(length=200), nullable=False),
        sa.Column("revoque_le", sa.Date(), nullable=True),
        sa.Column("revoque_par", sa.String(length=200), nullable=True),
        sa.Column("precision", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("identifiant", name="pk_mandat"),
    )
    op.create_index("ix_mandat_mandataire", "mandat", ["locataire", "mandataire"])
    for instruction in instructions_activation_pour("mandat"):
        op.execute(instruction)

    op.add_column("journal_audit", sa.Column("mandat", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("journal_audit", "mandat")
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON mandat")
    op.drop_index("ix_mandat_mandataire", table_name="mandat")
    op.drop_table("mandat")
