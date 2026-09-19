"""Un paiement peut régler autre chose qu'une souscription.

⚠️ **Ce n'est pas un renommage de confort.** La colonne s'appelait `souscription`, et
le parcours d'acquisition allait y écrire un numéro de proforma. Un champ nommé
`souscription` qui porte une proforma est exactement le défaut du pas 21, où le
message libre d'un prospect voyageait dans un champ nommé `region_demande` et servait
à décider d'une affectation.

Un nom qui ment coûte plus cher qu'une migration : il se lit dans les journaux, dans
les exports, et dans la tête de celui qui écrira la requête suivante.

L'index suit le nom de la colonne. Le laisser s'appeler `ix_paiement_souscription`
aurait produit l'inverse de ce que cette migration cherche : une trace du mensonge,
là où personne ne pense à regarder.

⚠️ **`nature` n'est pas une colonne.** Rien ne l'interroge : elle est lue sur un
paiement déjà chargé, au moment de choisir la suite. Elle vit dans le document JSON,
et les lignes déjà écrites sont lues avec sa valeur par défaut, `SOUSCRIPTION` — qui
est ce qu'elles sont toutes.

Revision ID: e1f7a4c92db6
Revises: d5e9b3f21c84
"""

from __future__ import annotations

from alembic import op

revision: str = "e1f7a4c92db6"
down_revision: str | None = "d5e9b3f21c84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_paiement_souscription", table_name="paiement")
    op.alter_column("paiement", "souscription", new_column_name="reference_reglee")
    op.create_index(
        "ix_paiement_reference_reglee", "paiement", ["locataire", "reference_reglee"]
    )


def downgrade() -> None:
    op.drop_index("ix_paiement_reference_reglee", table_name="paiement")
    op.alter_column("paiement", "reference_reglee", new_column_name="souscription")
    op.create_index("ix_paiement_souscription", "paiement", ["locataire", "souscription"])
