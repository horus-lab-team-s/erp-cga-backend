"""Table de la proforma : le document qui vaut contrat.

⚠️ **C'est la table dont la durée de conservation est la plus longue du contexte
commercial.** Ce qui a été envoyé à un client doit ressortir à l'identique dix ans plus
tard, y compris devant un tribunal.

Rien ne s'y met à jour en place. Une modification de montant produit une **nouvelle
ligne**, avec un nouveau numéro, et l'ancienne passe à l'état `REMPLACEE` en restant
consultable. Le chaînage se lit par la colonne `remplace`.

LA CLÉ PRIMAIRE EST COMPOSITE, ET ELLE PORTE LA NUMÉROTATION

`(locataire, numero)`. Elle empêche deux documents **du même cabinet** de porter le même
numéro, et laisse deux cabinets distincts avoir chacun leur `PRO-2026-0001`, ce qui est le
cas normal : un numéro de proforma est séquentiel par cabinet, lisible, et cité au
téléphone par le client.

C'est la base qui arbitre deux émissions simultanées, jamais une lecture suivie d'une
écriture : entre les deux, l'autre a émis.

Le conflit est **attendu** et se traite par une relecture suivie d'une nouvelle tentative.
Verrouiller la série entière sérialiserait toutes les émissions du cabinet pour une
garantie identique.

⚠️ **ELLE EST CLOISONNÉE, ET SA POLITIQUE EST POSÉE ICI**

La migration d'origine `7c31af5b904e` a été figée à ses dix-sept tables d'époque : Alembic
ne la rejoue pas, et une table créée après elle n'aurait aucune politique sans qu'aucun
message ne le signale.

Revision ID: e2a4f7c81b56
Revises: c58d0a91e7b4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "e2a4f7c81b56"
down_revision: str | None = "c58d0a91e7b4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "proforma",
        sa.Column("numero", sa.String(length=32), nullable=False),
        sa.Column("dossier", sa.String(length=64), nullable=False),
        sa.Column("etat", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("remplace", sa.String(length=32), nullable=True),
        sa.Column("montant", sa.Numeric(18, 2), nullable=False),
        sa.Column("emise_le", sa.DateTime(), nullable=False),
        sa.Column("transmise_le", sa.DateTime(), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        # ⚠️ Composite. Un numéro de proforma est séquentiel **par cabinet** :
        # `PRO-2026-0001` existe chez chacun d'eux, et c'est normal. Une clé sur
        # le seul numéro ferait échouer la première émission du second cabinet.
        sa.PrimaryKeyConstraint("locataire", "numero", name=op.f("pk_proforma")),
    )
    # Le calendrier de relance à 3, 7 et 14 jours, balayé par l'ordonnanceur.
    op.create_index(
        "ix_proforma_relance", "proforma", ["locataire", "etat", "transmise_le"]
    )
    op.create_index(
        "ix_proforma_dossier", "proforma", ["locataire", "dossier", "emise_le"]
    )

    for instruction in instructions_activation_pour("proforma"):
        op.execute(instruction)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON proforma")
    op.drop_index("ix_proforma_dossier", table_name="proforma")
    op.drop_index("ix_proforma_relance", table_name="proforma")
    op.drop_table("proforma")
