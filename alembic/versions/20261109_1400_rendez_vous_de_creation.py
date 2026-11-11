"""Rendez-vous de création, agenda du cabinet, codes de dossier.

9 novembre 2026 : le client qui ne sait pas remplir seul le formulaire prend rendez-vous
(présentiel ou en ligne) sur les créneaux proposés ; le code de dossier, envoyé par
courriel, rattache sa demande suivante au même dossier.

Revision ID: f4b8d2e06c73
Revises: e3a7c1d95b62
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "f4b8d2e06c73"
down_revision: str | None = "e3a7c1d95b62"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("agenda_de_creation", "rendez_vous", "code_de_dossier")


def upgrade() -> None:
    op.create_table(
        "agenda_de_creation",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("fixe_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", name="pk_agenda_de_creation"),
    )
    op.create_table(
        "rendez_vous",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("creneau", sa.String(length=64), nullable=False),
        sa.Column("debut", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name="pk_rendez_vous"),
        sa.UniqueConstraint("locataire", "creneau", name="uq_rendez_vous_creneau"),
    )
    op.create_index("ix_rendez_vous_dossier", "rendez_vous", ["locataire", "dossier"])
    op.create_table(
        "code_de_dossier",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_code_de_dossier"),
        sa.UniqueConstraint("locataire", "dossier", name="uq_code_de_dossier_dossier"),
    )
    for table in _TABLES:
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_table("code_de_dossier")
    op.drop_index("ix_rendez_vous_dossier", table_name="rendez_vous")
    op.drop_table("rendez_vous")
    op.drop_table("agenda_de_creation")
