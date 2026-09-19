"""Sécurité au niveau des lignes sur les tables cloisonnées.

La ceinture, sous les bretelles du filtre ORM. Celui-ci est solide mais le SQL textuel y
échappe — sa propre docstring le dit. Cette garantie-ci vit dans la base et s'applique à
toute requête, quelle qu'en soit l'origine.

⚠️ **Elle ne protège que les rôles non propriétaires.** Le propriétaire d'une table
contourne ses politiques, et c'est voulu : les migrations et les exports d'administration
doivent voir les données. L'application doit donc se connecter avec un rôle distinct :

    cga_migration   propriétaire, joue cette migration, contourne les politiques
    cga_app         non propriétaire, droits de manipulation seulement, y est soumis

Une installation qui emploierait le même rôle pour les deux aurait des politiques qui ne
s'appliquent jamais, sans qu'aucun message ne le signale.

─────────────────────────────────────────────────────────────────────────────────
LA LISTE EST FIGÉE ICI, ET ELLE L'EST APRÈS COUP

Cette migration dérivait sa liste de `app.tables.METADONNEES`. L'intention était bonne :
une liste écrite à la main serait juste le jour où on l'écrit et fausse à la table
suivante, et la table oubliée serait précisément celle qui fuit.

Elle était bonne et **elle était fausse**, pour une raison qui ne se voit qu'en jouant les
migrations sur une base neuve : `METADONNEES` décrit le code d'aujourd'hui, pas le schéma
tel qu'il était à cette révision. Le jour où une table cloisonnée est créée par une
migration **postérieure**, cette migration-ci tente de la protéger avant qu'elle existe, et
échoue :

    ProgrammingError: relation "dossier_commercial" does not exist
    [SQL: ALTER TABLE dossier_commercial ENABLE ROW LEVEL SECURITY]

Le défaut n'apparaît jamais sur une base déjà migrée, puisque Alembic ne rejoue pas ce qui
est passé. Il apparaît sur une installation neuve, c'est-à-dire chez le prochain
développeur et en production.

Une migration décrit un instant de l'histoire. Lire le présent la rend mutable, et une
histoire mutable ne se rejoue pas.

CE QUI REMPLACE LA DÉRIVATION, ET QUI EST MEILLEUR

La crainte d'origine — oublier une table — n'est pas traitée ici mais par
`tests/test_isolation.py`, qui boucle sur les métadonnées **courantes** et vérifie, dans
les deux sens, qu'aucune table cloisonnée ne laisse voir les lignes d'un autre locataire.
C'est le bon endroit : un test regarde le présent, c'est son métier ; une migration
regarde son époque, c'est le sien.

Chaque migration qui crée une table cloisonnée pose donc sa politique elle-même, par
`instructions_activation_pour`.
─────────────────────────────────────────────────────────────────────────────────

Revision ID: 7c31af5b904e
Revises: 438e6c427226
"""

from __future__ import annotations

from alembic import op

from app.infrastructure.securite_lignes import (
    NOM_POLITIQUE,
    instructions_activation_pour,
)

revision: str = "7c31af5b904e"
down_revision: str | None = "438e6c427226"
branch_labels: str | None = None
depends_on: str | None = None

#: Les tables cloisonnées **à cette révision**, et pas une de plus. Voir l'en-tête.
#: Cette liste ne s'allonge jamais : une table créée plus tard pose sa politique dans
#: sa propre migration.
TABLES = (
    "accuse_reception",
    "compte",
    "contrat_travail",
    "demande_piece",
    "devis",
    "dossier_creation",
    "ecriture",
    "entreprise",
    "habilitation",
    "jeton",
    "journal_audit",
    "paiement",
    "piece_justificative",
    "plan_imputation",
    "salarie",
    "session_ouverte",
    "souscription",
)


def upgrade() -> None:
    for nom in TABLES:
        for instruction in instructions_activation_pour(nom):
            op.execute(instruction)


def downgrade() -> None:
    """On retire la politique avant de désactiver.

    L'ordre inverse laisserait une politique orpheline, que la réactivation suivante
    ferait échouer sur un doublon de nom.
    """
    for nom in TABLES:
        op.execute(f"DROP POLICY IF EXISTS {NOM_POLITIQUE} ON {nom}")
        op.execute(f"ALTER TABLE {nom} DISABLE ROW LEVEL SECURITY")
