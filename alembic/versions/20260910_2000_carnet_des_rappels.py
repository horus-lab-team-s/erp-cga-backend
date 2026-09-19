"""Carnet des rappels à passer : ce que la machine confie à un humain.

Sans lui, le repli par appel du plan de contact ne serait pas un repli mais un
silence : la relance serait comptée comme remise, et personne n'appellerait. Le
plancher du plan de contact deviendrait un trou.

⚠️ Distinct de la veille du dossier commercial, qui répond à « qu'est-ce qui est
bloqué ». Un rappel répond à « le système a décidé de vous confier ce contact-ci,
pour ce motif-là » : un client sans courriel ni consentement n'est joignable qu'au
téléphone, et son dossier peut n'être en souffrance nulle part.

Revision ID: c1d8f6a30e57
Revises: a7c4e2b90f31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c1d8f6a30e57"
down_revision: str | None = "a7c4e2b90f31"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "rappel_a_passer",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("cree_le", sa.DateTime(), nullable=False),
        # `NULL` tant que personne n'a rappelé. Une date et non un booléen : elle
        # mesure aussi le délai entre la décision de la machine et l'appel humain.
        sa.Column("fait_le", sa.DateTime(), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        # ⚠️ **Composite.** L'identifiant d'un rappel dérive du numéro de proforma,
        # séquentiel par cabinet : `rap-rel-PRO-2026-0001-2` existe chez chacun
        # d'eux. Troisième occurrence de ce piège après la proforma et le suivi de
        # relance, et même règle : toute clé dérivée d'une numérotation par cabinet
        # doit porter le locataire.
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_rappel_a_passer"),
    )
    # « Ce qui m'attend », du plus ancien au plus récent.
    op.create_index(
        "ix_rappel_en_attente", "rappel_a_passer", ["locataire", "fait_le", "cree_le"]
    )
    # « Qu'a-t-on déjà confié sur ce dossier », posée avant d'appeler.
    op.create_index(
        "ix_rappel_dossier", "rappel_a_passer", ["locataire", "dossier", "cree_le"]
    )
    for instruction in instructions_activation_pour("rappel_a_passer"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON rappel_a_passer")
    op.drop_index("ix_rappel_dossier", table_name="rappel_a_passer")
    op.drop_index("ix_rappel_en_attente", table_name="rappel_a_passer")
    op.drop_table("rappel_a_passer")
