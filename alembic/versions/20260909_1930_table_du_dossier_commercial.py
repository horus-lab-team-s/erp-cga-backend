"""Table du parcours d'acquisition · le dossier commercial.

Le premier objet du parcours client : ce qu'un visiteur dépose depuis la vitrine
publique, et ce que le cabinet en fait ensuite, de son dépôt à son paiement.

⚠️ **ELLE EST CLOISONNÉE, ET SA POLITIQUE EST POSÉE ICI**

La migration `7c31af5b904e` dérive ses politiques des métadonnées, et couvre donc ce qui
existait le jour où elle a été jouée. Elle a déjà tourné : Alembic ne la rejoue pas. Une
table cloisonnée créée après elle n'aurait **aucune politique**, sans qu'aucun message ne
le signale, et le cloisonnement reposerait alors sur le seul filtre ORM.

Chaque migration qui crée une table cloisonnée pose donc sa politique elle-même, par
`instructions_activation_pour`, plutôt que de recopier le SQL. Une politique recopiée sans
`WITH CHECK` protégerait les lectures et laisserait écrire chez le voisin, ce qui est plus
rare qu'une lecture fautive et bien plus difficile à défaire.

POURQUOI CETTE TABLE EST CLOISONNÉE ALORS QUE LE VISITEUR EST ANONYME

Il n'y a pas de contradiction. Un nom d'hôte qui ne désigne aucun tenant retombe sur le
locataire du centre : c'est le cabinet qui reçoit la demande, pas un client. La ligne
appartient donc au centre, et se cloisonne comme les autres.

TROIS INDEX, TROIS REQUÊTES RÉELLES

* `ix_dossier_telephone` — « ce numéro a-t-il déjà écrit depuis hier ? », posée à chaque
  dépôt de formulaire, robots compris ;
* `ix_dossier_veille` — « qui est resté trop longtemps dans cet état ? », balayée état par
  état par l'ordonnanceur, avec un délai propre à chacun ;
* `ix_dossier_responsable` — « mes dossiers en cours », l'écran du matin.

Revision ID: b41e7a05c9d2
Revises: 3d90b2ec1148
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "b41e7a05c9d2"
down_revision: str | None = "3d90b2ec1148"
branch_labels: str | None = None
depends_on: str | None = None

#: Écrit en toutes lettres à chaque appel, et non par une constante.
#:
#: `test_tables.py` vérifie qu'aucune table déclarée n'est sans migration, et il le fait
#: en lisant l'arbre syntaxique : il cherche le nom littéral passé à `create_table`. Une
#: constante le lui cacherait, et la vérification passerait au vert sur une table qui
#: n'existerait nulle part. Un garde-fou qu'on peut contourner sans le vouloir n'en est
#: pas un.
TABLE = "dossier_commercial"


def upgrade() -> None:
    op.create_table(
        "dossier_commercial",
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("telephone", sa.String(length=20), nullable=False),
        sa.Column("etat", sa.String(length=20), nullable=False),
        sa.Column("depuis_le", sa.DateTime(), nullable=False),
        sa.Column("deposee_le", sa.DateTime(), nullable=False),
        sa.Column("responsable", sa.String(length=64), nullable=True),
        sa.Column("locataire", sa.String(length=64), nullable=False),
        sa.Column("donnees", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("reference", name=op.f("pk_dossier_commercial")),
    )
    op.create_index(
        "ix_dossier_telephone", "dossier_commercial", ["locataire", "telephone", "deposee_le"]
    )
    op.create_index("ix_dossier_veille", "dossier_commercial", ["locataire", "etat", "depuis_le"])
    op.create_index(
        "ix_dossier_responsable", "dossier_commercial", ["locataire", "responsable", "etat"]
    )

    # Voir l'en-tête : la politique ne vient pas toute seule.
    for instruction in instructions_activation_pour(TABLE):
        op.execute(instruction)


def downgrade() -> None:
    # La politique d'abord : `DROP TABLE` l'emporterait avec elle, mais l'écrire rend le
    # retour en arrière lisible et le laisse correct si la table venait à survivre.
    op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {TABLE}")
    op.drop_index("ix_dossier_responsable", table_name="dossier_commercial")
    op.drop_index("ix_dossier_veille", table_name="dossier_commercial")
    op.drop_index("ix_dossier_telephone", table_name="dossier_commercial")
    op.drop_table("dossier_commercial")
