"""Table des écarts de constats : ce que le cabinet a décidé d'un rapport (pas 92).

⚠️ **Première table de la conformité.** Un rapport ne se conserve pas, il se recalcule
sur le référentiel daté. Une décision d'écart, elle, ne se recalcule pas : elle engage
la personne qui l'a posée, et elle doit se relire des années plus tard.

⚠️ La clé primaire est composite, `(locataire, identifiant)` : l'identifiant dérive de
la référence de la facture, que le fournisseur choisit. Cinquième application de la
règle établie au pas 17.

Une ligne par décision : un écart refusé puis reproposé fait deux lignes, et le refus
reste lisible.

Revision ID: f3a8c5d71e29
Revises: e1f7a4c92db6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "f3a8c5d71e29"
down_revision: str | None = "e1f7a4c92db6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ecart_de_constat",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=200), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("reference_document", sa.String(length=128), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("propose_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_ecart_de_constat"),
    )
    # « Les écarts de cette pièce », lu à chaque ouverture de l'écran E02.
    op.create_index(
        "ix_ecart_piece", "ecart_de_constat", ["locataire", "dossier", "reference_document"]
    )
    # « Ce qui attend un second regard », du plus ancien au plus récent.
    op.create_index("ix_ecart_statut", "ecart_de_constat", ["locataire", "statut", "propose_le"])
    for instruction in instructions_activation_pour("ecart_de_constat"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON ecart_de_constat")
    op.drop_index("ix_ecart_statut", table_name="ecart_de_constat")
    op.drop_index("ix_ecart_piece", table_name="ecart_de_constat")
    op.drop_table("ecart_de_constat")
