"""Table du suivi de relance : ce qui a déjà été envoyé, à part du document.

Sans elle, le balayage de relance ne peut pas être déclaré comme travail de
l'ordonnanceur : il n'aurait aucun moyen de savoir ce qu'il a déjà envoyé, et
relancerait le même client à chaque passage.

⚠️ **Le suivi ne va pas sur la proforma, et c'est une décision.** Celle-ci est figée
et vaut contrat : ce qui a été envoyé à un client doit ressortir à l'identique dix
ans plus tard. Y inscrire un compteur de relances ferait changer un document
contractuel pour une raison qui n'a rien de contractuel, et chaque balayage
réécrirait une ligne dont la stabilité est la propriété qu'on lui demande.

⚠️ **La clé primaire est composite**, `(locataire, proforma)`, pour la même raison
que celle de la proforma : un numéro est séquentiel par cabinet, et `PRO-2026-0001`
existe chez chacun d'eux. Une clé sur le seul numéro ferait que le premier cabinet à
relancer empêcherait tous les autres d'inscrire leur suivi.

Revision ID: a7c4e2b90f31
Revises: f3b91d0c7a24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "a7c4e2b90f31"
down_revision: str | None = "f3b91d0c7a24"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "suivi_de_relance",
        sa.Column("proforma", sa.String(length=32), nullable=False),
        # `NULL` tant qu'aucune relance n'est partie : la ligne peut exister sans
        # envoi, écrite dès qu'un balayage s'intéresse à la proforma.
        sa.Column("derniere_le", sa.DateTime(), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint(
            "locataire", "proforma", name="pk_suivi_de_relance"
        ),
    )
    # « Ce qui a été relancé récemment », du plus récent au plus ancien.
    op.create_index(
        "ix_suivi_relance_recent", "suivi_de_relance", ["locataire", "derniere_le"]
    )
    for instruction in instructions_activation_pour("suivi_de_relance"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON suivi_de_relance")
    op.drop_index("ix_suivi_relance_recent", table_name="suivi_de_relance")
    op.drop_table("suivi_de_relance")
