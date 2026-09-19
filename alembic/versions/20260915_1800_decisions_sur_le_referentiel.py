"""Table des décisions d'un cabinet sur le référentiel (pas 95).

⚠️ **Le référentiel commun reste un fichier.** Cette table porte la surcouche de chaque
cabinet : validations de versions « à valider » et nouvelles versions datées, appliquées
à la lecture pour ce cabinet seul. Voir `referentiel/domaine/surcouche.py`.

La clé est composite, `(locataire, identifiant)` : c'est elle qui garantit qu'une décision
ne vaut que pour le cabinet qui l'a prise.

Revision ID: b7c3e8f14a52
Revises: a4b9d2e63f10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "b7c3e8f14a52"
down_revision: str | None = "a4b9d2e63f10"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decision_referentiel",
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("identifiant", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("statut", sa.String(length=16), nullable=False),
        sa.Column("propose_le", sa.DateTime(), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("locataire", "identifiant", name="pk_decision_referentiel"),
    )
    op.create_index(
        "ix_decision_referentiel_code", "decision_referentiel", ["locataire", "code", "propose_le"]
    )
    op.create_index(
        "ix_decision_referentiel_statut", "decision_referentiel", ["locataire", "statut"]
    )
    for instruction in instructions_activation_pour("decision_referentiel"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON decision_referentiel")
    op.drop_index("ix_decision_referentiel_statut", table_name="decision_referentiel")
    op.drop_index("ix_decision_referentiel_code", table_name="decision_referentiel")
    op.drop_table("decision_referentiel")
