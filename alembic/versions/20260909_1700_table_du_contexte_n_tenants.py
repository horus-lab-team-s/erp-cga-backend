"""Table du contexte N · Tenants.

Le plan de contrôle. Elle dit quels sous-domaines répondent, et à qui.

⚠️ **Elle n'est pas cloisonnée**, contrairement à toutes les autres tables métier. Elle
est lue avant qu'on sache de quel locataire il s'agit : c'est elle qui le dit. La
cloisonner créerait une dépendance circulaire à l'exécution, et sa politique de sécurité
refuserait chaque requête faute de variable posée — le répertoire ne verrait plus rien et
tout sous-domaine rendrait 404. L'exemption est nommée et justifiée dans `test_tables.py`.

L'INDEX D'UNICITÉ EST POSÉ SUR UNE EXPRESSION

Un nom d'hôte est insensible à la casse : « Station » et « station » désignent le même
serveur. Une contrainte d'unicité ordinaire les laisserait coexister, et deux tenants se
disputeraient alors le même sous-domaine — le premier trouvé gagnerait, et lequel dépendrait
du plan d'exécution.

L'index porte donc sur `lower(slug)`. C'est aussi lui qui **arbitre deux souscriptions
simultanées** : la base tranche, jamais une lecture suivie d'une écriture, entre lesquelles
l'autre a eu le temps d'écrire.

Revision ID: 3d90b2ec1148
Revises: 7c31af5b904e
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "3d90b2ec1148"
down_revision: str | None = "7c31af5b904e"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("identifiant", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("statut", sa.String(length=20), nullable=False),
        sa.Column("etape_atteinte", sa.String(length=30), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("identifiant", name=op.f("pk_tenant")),
    )
    # Voir l'en-tête : sur l'expression, pas sur la colonne.
    op.create_index(
        "uq_tenant_slug_insensible_casse",
        "tenant",
        [sa.text("lower(slug)")],
        unique=True,
    )
    # La reprise des ouvertures interrompues : « ce qui est en échec, et où ».
    op.create_index("ix_tenant_reprise", "tenant", ["statut", "etape_atteinte"])


def downgrade() -> None:
    op.drop_index("ix_tenant_reprise", table_name="tenant")
    op.drop_index("uq_tenant_slug_insensible_casse", table_name="tenant")
    op.drop_table("tenant")
