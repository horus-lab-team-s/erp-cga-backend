"""Table de la qualification : ce qu'on a appris d'un prospect.

⚠️ **Ce qui manquait n'était pas la table, c'était l'écriture.** La route construisait
la qualification, validait chaque réponse au type de sa question, rendait l'avancement
et les faits, puis la jetait. Un responsable qui répondait à cinq questions sur douze
et revenait le lendemain recommençait à zéro, sans qu'aucune erreur ne se produise.

⚠️ La clé primaire est composite, `(locataire, dossier)` : une référence de dossier est
propre au cabinet qui l'a émise. Quatrième application de la règle établie au pas 17,
après la proforma, le suivi de relance et le carnet des rappels.

`version_questionnaire` est promue parce qu'elle rend une qualification relisible : un
questionnaire évolue, et une question ajoutée rendrait « incomplètes » toutes les
qualifications déjà closes si l'on comparait à la version du jour.

Revision ID: d5e9b3f21c84
Revises: c1d8f6a30e57
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "d5e9b3f21c84"
down_revision: str | None = "c1d8f6a30e57"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "qualification",
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("version_questionnaire", sa.String(length=32), nullable=False),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "dossier", name="pk_qualification"),
    )
    # « Les qualifications de ce service », pour mesurer ce qu'un questionnaire
    # laisse en plan avant de le réviser.
    op.create_index(
        "ix_qualification_service", "qualification", ["locataire", "service"]
    )
    for instruction in instructions_activation_pour("qualification"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON qualification")
    op.drop_index("ix_qualification_service", table_name="qualification")
    op.drop_table("qualification")
