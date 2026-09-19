"""Socle d'orchestration : la boîte d'envoi et les exécutions de saga.

Ce qui relie les étapes du parcours. Jusqu'ici, les pièces existaient et rien ne les
reliait.

⚠️ **LES DEUX TABLES SONT CLOISONNÉES, ET LEURS POLITIQUES SONT POSÉES ICI**

Voir l'en-tête de `20260909_1500_securite_au_niveau_des_lignes.py` : la migration
d'origine a été figée à ses dix-sept tables d'époque, et chaque migration qui crée une
table cloisonnée pose sa politique elle-même.

CE QUE LA CONTRAINTE D'UNICITÉ PROTÈGE

`uq_saga_cle` empêche deux exécutions concurrentes de la même saga sur la même clé. Sans
elle, un double déclenchement — un rappel rejoué, deux instances derrière un répartiteur —
ouvrirait deux préfixes de stockage et enverrait deux liens d'activation. C'est la base
qui arbitre, jamais une lecture suivie d'une écriture : entre les deux, l'autre a écrit.

L'INDEX QUI FAIT VIVRE LE SYSTÈME

`ix_boite_a_publier` est interrogé en boucle par le passage de publication, toutes les
quelques secondes. Il doit rendre en temps constant même avec un an d'historique en table.
Il porte `publie_le` et `en_quarantaine` parce que c'est exactement la requête : « ce qui
n'est ni publié ni mis de côté, du plus ancien au plus récent ».

Revision ID: c58d0a91e7b4
Revises: b41e7a05c9d2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "c58d0a91e7b4"
down_revision: str | None = "b41e7a05c9d2"
branch_labels: str | None = None
depends_on: str | None = None

#: En toutes lettres dans `create_table` : `test_tables.py` lit l'arbre syntaxique et
#: cherche le nom littéral. Une constante le lui cacherait, et la vérification passerait
#: au vert sur une table qui n'existerait nulle part.
TABLES = ("boite_d_envoi", "execution_saga")


def upgrade() -> None:
    op.create_table(
        "boite_d_envoi",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("nom", sa.String(length=80), nullable=False),
        sa.Column("cle", sa.String(length=120), nullable=False),
        sa.Column("cree_le", sa.DateTime(), nullable=False),
        sa.Column("publie_le", sa.DateTime(), nullable=True),
        sa.Column("tentatives", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "en_quarantaine", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("dernier_echec", sa.Text(), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name=op.f("pk_boite_d_envoi")),
    )
    op.create_index(
        "ix_boite_a_publier",
        "boite_d_envoi",
        ["locataire", "publie_le", "en_quarantaine", "cree_le"],
    )
    op.create_index("ix_boite_cle", "boite_d_envoi", ["locataire", "cle", "cree_le"])

    op.create_table(
        "execution_saga",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("saga", sa.String(length=60), nullable=False),
        sa.Column("cle", sa.String(length=120), nullable=False),
        sa.Column("etat", sa.String(length=30), nullable=False),
        sa.Column("tentatives", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("demarree_le", sa.DateTime(), nullable=True),
        sa.Column("terminee_le", sa.DateTime(), nullable=True),
        sa.Column("dernier_echec", sa.Text(), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name=op.f("pk_execution_saga")),
        # Voir l'en-tête : c'est elle qui empêche le double provisionnement.
        sa.UniqueConstraint("locataire", "saga", "cle", name="uq_saga_cle"),
    )
    op.create_index(
        "ix_saga_reprise", "execution_saga", ["locataire", "etat", "demarree_le"]
    )

    for table in TABLES:
        for instruction in instructions_activation_pour(table):
            op.execute(instruction)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {table}")
    op.drop_index("ix_saga_reprise", table_name="execution_saga")
    op.drop_table("execution_saga")
    op.drop_index("ix_boite_cle", table_name="boite_d_envoi")
    op.drop_index("ix_boite_a_publier", table_name="boite_d_envoi")
    op.drop_table("boite_d_envoi")
