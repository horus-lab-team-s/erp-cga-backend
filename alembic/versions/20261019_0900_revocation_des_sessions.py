"""Révoque toutes les sessions ouvertes (audit de sécurité du 19 octobre 2026).

Jusqu'ici, le journal d'audit écrivait l'identifiant de chaque session ouverte, fermée ou
renforcée. Cet identifiant est le secret porteur : quiconque lisait le journal pouvait
rejouer la session d'un autre, celle du super administrateur comprise. Le journal n'en
garde désormais qu'une empreinte (`reference_de_session`).

Les entrées déjà écrites ne se réécrivent pas : le journal est chaîné, et le modifier
casserait sa vérification. On rend donc les identifiants qu'elles portent inutilisables,
en fermant toutes les sessions encore ouvertes. Chacun se reconnecte une fois.

Revision ID: b8d4f6a02c35
Revises: a7c3e5f91b24
"""

from __future__ import annotations

from alembic import op

revision: str = "b8d4f6a02c35"
down_revision: str | None = "a7c3e5f91b24"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE session_ouverte "
        "SET revoquee_le = (now() AT TIME ZONE 'utc'), "
        "motif_revocation = 'REVOCATION_ADMINISTRATIVE' "
        "WHERE revoquee_le IS NULL"
    )


def downgrade() -> None:
    # Une révocation ne se défait pas : rouvrir des sessions dont l'identifiant a pu
    # être lu au journal reviendrait à rouvrir la faille.
    pass
