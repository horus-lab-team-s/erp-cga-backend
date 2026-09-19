"""Table du passage d'ordonnance : la mémoire de l'ordonnanceur.

Sans elle, le compte à rebours de chaque travail périodique vit en mémoire, et un
redéploiement le remet à zéro. Un travail quotidien, sur une plateforme déployée
chaque matin, ne passerait jamais, et rien ne le signalerait puisque le processus
démarre correctement.

⚠️ **Cette table n'est pas cloisonnée, et n'a donc aucune politique de sécurité au
niveau des lignes.** Ce n'est pas un oubli. Un travail périodique n'appartient à
aucun cabinet : le relais vide la boîte de tous les locataires. Lui donner un
propriétaire obligerait à en choisir un au hasard, et la réponse serait fausse quel
que soit le choix. C'est la première table du système dans ce cas, et la raison est
écrite ici pour qu'une relecture ne la « corrige » pas.

Révision : f3b91d0c7a24
Révise : e2a4f7c81b56
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b91d0c7a24"
down_revision = "e2a4f7c81b56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "passage_ordonnance",
        # Le nom du travail est la clé : deux lignes pour « relais » n'auraient
        # aucun sens, et la contrainte l'empêche plutôt que de l'espérer.
        sa.Column("travail", sa.String(length=64), nullable=False),
        sa.Column("debute_le", sa.DateTime(), nullable=True),
        sa.Column("termine_le", sa.DateTime(), nullable=True),
        sa.Column(
            "echecs_consecutifs", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("dernier_echec", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("travail", name="pk_passage_ordonnance"),
    )


def downgrade() -> None:
    op.drop_table("passage_ordonnance")
