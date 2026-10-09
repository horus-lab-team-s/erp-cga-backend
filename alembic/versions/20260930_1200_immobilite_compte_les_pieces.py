"""Recalcul de `immobile_depuis` : une pièce reçue est un mouvement.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CETTE MIGRATION EXISTE

`DossierCreation.immobile_depuis` ne lisait que les **jalons**, c'est-à-dire les
franchissements d'étape, c'est-à-dire les gestes du cabinet. Depuis que la
cliente dépose elle-même par son lien scellé, elle peut envoyer cinq documents
un mardi sans qu'aucun jalon ne bouge : le pipeline annonçait « immobile depuis
trois semaines », et le chargé de formalités relançait en premier celle qui
venait de répondre.

La propriété lit désormais aussi les dates de pièces fournies.

⚠️ **CORRIGER LA PROPRIÉTÉ NE SUFFIT PAS.** La valeur est dénormalisée en
colonne, et `tables.py` le dit : « recalculée à chaque écriture ». À chaque
écriture, donc jamais pour un dossier que personne ne touche plus. Or c'est
exactement le dossier qui dort, celui que cette colonne sert à trouver.

Et la colonne n'est pas décorative : le dépôt SQL **filtre et trie dessus**
(`depots.py`), parce que « les dossiers qui n'ont pas bougé depuis quinze
jours » est la requête que le chargé de formalités lance tous les matins.

⚠️ **LA RÈGLE EST RECOPIÉE EN SQL, ET FIGÉE ICI.** On n'importe pas le domaine :
une migration rejouée dans six mois doit produire ce qu'elle produisait le jour
où elle a été écrite. Si la règle change encore, ce sera une autre migration.

⚠️ **AUCUNE DESCENTE.** La valeur d'avant était fausse ; la restaurer
réintroduirait le défaut. `downgrade` ne fait donc rien, et le dit.

Revision ID: a1c8e5f30927
Revises: e7a4c9d2b815
"""

from __future__ import annotations

from alembic import op

revision: str = "a1c8e5f30927"
down_revision: str | None = "e7a4c9d2b815"
branch_labels: str | None = None
depends_on: str | None = None

#: La date du dernier mouvement : l'ouverture, le dernier jalon, la dernière
#: pièce reçue. `json_array_elements` et non `jsonb_` : la colonne est de type
#: `JSON` (voir le mixin `Document`).
_DERNIER_MOUVEMENT = """
    GREATEST(
        (d.donnees->>'ouvert_le')::date,
        COALESCE(
            (SELECT max((j->>'survenu_le')::date)
             FROM json_array_elements(d.donnees->'jalons') j),
            (d.donnees->>'ouvert_le')::date
        ),
        COALESCE(
            (SELECT max((p->>'fournie_le')::date)
             FROM json_array_elements(d.donnees->'pieces') p
             WHERE p->>'fournie_le' IS NOT NULL),
            (d.donnees->>'ouvert_le')::date
        )
    )
"""


def upgrade() -> None:
    # ⚠️ `IS DISTINCT FROM` et non `<>` : l'un des deux côtés pourrait être nul
    # sur une donnée abîmée, et `<>` rendrait alors `NULL`, donc aucune ligne
    # mise à jour, sans que rien n'échoue.
    #
    # ⚠️ Toutes les lignes, tous locataires confondus : une migration s'exécute
    # sous le rôle propriétaire, hors du cloisonnement par ligne. C'est voulu,
    # c'est le seul endroit du système où ce soit le cas, et cette requête ne
    # lit ni ne déplace de données entre cabinets : elle recalcule une valeur à
    # partir de la ligne elle-même.
    op.execute(
        f"""
        UPDATE dossier_creation d
        SET immobile_depuis = {_DERNIER_MOUVEMENT}
        WHERE d.immobile_depuis IS DISTINCT FROM {_DERNIER_MOUVEMENT}
        """
    )


def downgrade() -> None:
    """Rien, volontairement.

    Remettre l'ancienne valeur serait remettre une valeur fausse. Une colonne
    dérivée se recalcule ; elle ne se restaure pas.
    """
