"""Élargir `compte.secret_totp` pour qu'il porte un scellé chiffré.

Révision : 0b1987f66324
Précédente : 123ec75ca4b0
Créée le : 2026-08-16 08:09:33.478145

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

La colonne accueillait un secret TOTP en clair : 32 caractères en base32, et 64
suffisaient. Elle porte désormais un **scellé** AES-256-GCM — préfixe de version,
nonce et sceau d'authenticité compris —, soit 83 caractères. Voir
`adaptateurs/sortant/coffre.py`.

255 plutôt que 96 : la marge couvre un changement d'algorithme sans nouvelle
migration, et une colonne `varchar` plus large ne coûte rien en PostgreSQL — le
stockage suit la longueur réelle.

⚠️ CETTE MIGRATION NE CHIFFRE RIEN

Elle prépare la place, elle ne transforme pas les valeurs. Les secrets déjà en
base restent en clair et continuent de fonctionner : le coffre reconnaît une
valeur sans préfixe et la rend telle quelle. Chacun sera scellé à la prochaine
écriture de son compte.

**La conséquence est à connaître** : un compte dont le second facteur n'est
jamais retouché garde son secret en clair indéfiniment. Chiffrer l'existant
demande de réactiver le second facteur des comptes concernés — un geste
d'exploitation, pas de migration, parce qu'il exige la clé et qu'une migration
n'a pas à la connaître.

LA DESCENTE PEUT PERDRE DES DONNÉES

Revenir à 64 caractères **tronque** tout scellé déjà écrit, et un scellé tronqué
est définitivement indéchiffrable — GCM le rejettera. Une descente n'est donc
sûre que si aucun compte n'a été scellé depuis. PostgreSQL refusera de lui-même
si des valeurs dépassent, et c'est tant mieux.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0b1987f66324"
down_revision: str | None = "123ec75ca4b0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column(
        "compte",
        "secret_totp",
        existing_type=sa.VARCHAR(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    """⚠️ Tronque les scellés existants et les rend indéchiffrables.

    Voir l'en-tête. PostgreSQL refusera si des valeurs dépassent 64 caractères,
    ce qui est la protection qu'on veut : la descente n'aboutit que si elle est
    effectivement sans perte.
    """
    op.alter_column(
        "compte",
        "secret_totp",
        existing_type=sa.String(length=255),
        type_=sa.VARCHAR(length=64),
        existing_nullable=True,
    )
