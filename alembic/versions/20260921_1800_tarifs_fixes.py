"""Table des tarifs fixés : les décisions de prix du gérant.

⚠️ **LE PRIX EST UNE DÉCISION HUMAINE.** Le catalogue public portait des montants
recopiés de la maquette de la vitrine, affichés comme fermes et encaissés en ligne,
sans qu'aucun responsable du cabinet ne les ait jamais arrêtés. Ils n'étaient pas
non plus sur la fiche de contreseing : ils échappaient à toute validation.

Chaque ligne est une décision : tel prix, pour telle prestation, à partir de tel
jour, fixé par tel compte, à tel instant. Rien ne s'y met à jour : corriger un prix,
c'est en fixer un nouveau. Tant qu'aucune décision n'existe, les tarifs restent « à
valider » et ne se paient pas en ligne.

Revision ID: e7a4c9d2b815
Revises: c4e9a2b73d61
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e7a4c9d2b815"
down_revision: str | None = "c4e9a2b73d61"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "tarif_fixe",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("a_partir_du", sa.Date(), nullable=False),
        sa.Column("fixe_le", sa.DateTime(), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_tarif_fixe"),
    )
    # « Les décisions sur ce service », pour l'écran du gérant.
    op.create_index("ix_tarif_fixe_service", "tarif_fixe", ["locataire", "service"])
    for instruction in instructions_activation_pour("tarif_fixe"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON tarif_fixe")
    op.drop_index("ix_tarif_fixe_service", table_name="tarif_fixe")
    op.drop_table("tarif_fixe")
