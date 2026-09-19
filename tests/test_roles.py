"""Le diagnostic du cloisonnement : quatre rôles, quatre verdicts, sur une vraie base.

⚠️ **Ce fichier ne peut pas être écrit en mémoire ni avec des doublures.** Il vérifie ce
que PostgreSQL fait de ses propres règles d'exemption : le propriétaire d'une table, un
rôle `BYPASSRLS` et un superutilisateur échappent aux politiques. Une doublure ne
répondrait que ce qu'on lui aurait appris, c'est-à-dire ce qu'on croit déjà.

CE QUE CE DIAGNOSTIC EMPÊCHE

Sans deux rôles distincts, l'application possède ses tables et contourne le cloisonnement
du multi-cabinet. Rien ne le signale : le service répond, la recette passe, et les données
de deux centres ne sont séparées que par le filtre applicatif. Voir
`outils/roles-postgresql.sql` pour la correction, et `app/main.py` pour le refus de
démarrage en production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, MetaData, String, Table, create_engine, text

from app.infrastructure.roles import EtatDuCloisonnement, diagnostiquer
from app.infrastructure.securite_lignes import (
    COLONNE_CLOISON,
    NOM_POLITIQUE,
    VARIABLE_SESSION,
)
from tests.conftest import URL_BASE_TEST, exige_postgresql

MOT_DE_PASSE = "essai-roles"
#: Un rôle par cas, plutôt qu'un rôle dont on change les attributs entre deux cas.
#: Un attribut oublié au nettoyage se propagerait au cas suivant, et l'ordre
#: d'exécution deviendrait significatif — ce qui est le début des tests qui ne
#: passent qu'ensemble.
ROLE_ORDINAIRE = "diag_ordinaire"
ROLE_PROPRIETAIRE = "diag_proprietaire"
ROLE_CONTOURNANT = "diag_contournant"
TABLE = "diag_cloisonnee"


def _url(role: str) -> str:
    return URL_BASE_TEST.replace("cga:cga@", f"{role}:{MOT_DE_PASSE}@")


@pytest.fixture(scope="module")
def proprietaire():
    moteur = create_engine(URL_BASE_TEST, pool_pre_ping=True)
    yield moteur
    moteur.dispose()


@pytest.fixture(scope="module")
def schema(proprietaire):
    """Une table cloisonnée à part, possédée par un rôle à part.

    ⚠️ Elle ne réemploie **aucune** table du schéma applicatif. Le diagnostic balaie
    toutes les tables portant une colonne `locataire` : le poser sur les tables réelles
    ferait dépendre le résultat de l'état où le fichier de test précédent les a laissées.
    """
    with proprietaire.begin() as connexion:
        for role, attributs in (
            (ROLE_ORDINAIRE, "NOSUPERUSER NOBYPASSRLS"),
            (ROLE_PROPRIETAIRE, "NOSUPERUSER NOBYPASSRLS"),
            (ROLE_CONTOURNANT, "NOSUPERUSER BYPASSRLS"),
        ):
            connexion.execute(
                text(
                    f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
                    f"'{role}') THEN CREATE ROLE {role} LOGIN PASSWORD "
                    f"'{MOT_DE_PASSE}'; END IF; END $$"
                )
            )
            # ⚠️ Hors du `IF NOT EXISTS` : un rôle laissé par une exécution précédente
            # doit retrouver ses attributs, sinon le cas d'avant contamine celui-ci.
            connexion.execute(text(f"ALTER ROLE {role} {attributs}"))

    metadonnees = MetaData()
    Table(
        TABLE,
        metadonnees,
        Column(COLONNE_CLOISON, String(64), nullable=False),
        Column("valeur", String(64), nullable=False),
    )
    metadonnees.drop_all(proprietaire)
    metadonnees.create_all(proprietaire)

    with proprietaire.begin() as connexion:
        connexion.execute(text(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY"))
        connexion.execute(
            text(
                f"CREATE POLICY {NOM_POLITIQUE} ON {TABLE} USING "
                f"({COLONNE_CLOISON} = current_setting('{VARIABLE_SESSION}', true))"
            )
        )
        connexion.execute(text(f"ALTER TABLE {TABLE} OWNER TO {ROLE_PROPRIETAIRE}"))
        # ⚠️ **Les trois rôles, propriétaire compris.** Il l'était implicitement, par
        # les privilèges que `PUBLIC` portait sur le schéma, et ces cas passaient
        # donc sur une base héritée sans rien exiger.
        #
        # Depuis PostgreSQL 15, un schéma `public` recréé n'accorde plus rien à
        # `PUBLIC` : le propriétaire ne voyait alors aucune table, le diagnostic
        # rendait « appliqué » là où il aurait dû nommer le contournement, et le
        # cas échouait sur une base neuve — donc en intégration continue, jamais
        # sur le poste où il a été écrit.
        #
        # C'est la seconde fois que ce défaut se présente au projet, après le
        # `GRANT CREATE ON SCHEMA public` du socle. *Un privilège hérité est un
        # privilège que la prochaine base n'aura pas.*
        for role in (ROLE_ORDINAIRE, ROLE_CONTOURNANT, ROLE_PROPRIETAIRE):
            connexion.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            connexion.execute(text(f"GRANT SELECT ON {TABLE} TO {role}"))

    yield metadonnees

    with proprietaire.begin() as connexion:
        connexion.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        for role in (ROLE_ORDINAIRE, ROLE_PROPRIETAIRE, ROLE_CONTOURNANT):
            connexion.execute(text(f"DROP OWNED BY {role}"))
            connexion.execute(text(f"DROP ROLE IF EXISTS {role}"))


def _diagnostic(role: str):
    moteur = create_engine(_url(role), pool_pre_ping=True)
    try:
        with moteur.connect() as connexion:
            return diagnostiquer(connexion)
    finally:
        moteur.dispose()


@exige_postgresql
class TestLeVerdict:
    """Chaque cause d'exemption a son verdict, et le verdict nomme sa cause."""

    def test_un_role_ordinaire_est_soumis_aux_politiques(self, schema):
        """L'état attendu en exploitation : ni propriétaire, ni exempté, ni superutilisateur."""
        diagnostic = _diagnostic(ROLE_ORDINAIRE)
        assert diagnostic.etat is EtatDuCloisonnement.APPLIQUE
        assert diagnostic.applique
        assert diagnostic.role == ROLE_ORDINAIRE

    def test_le_proprietaire_de_la_table_contourne_ses_propres_politiques(self, schema):
        """Le cas le plus fréquent, et le plus silencieux.

        Il arrive dès que les migrations et l'application partagent un rôle, ce qui est
        la configuration par défaut de toute installation faite sans y penser.
        """
        diagnostic = _diagnostic(ROLE_PROPRIETAIRE)
        assert diagnostic.etat is EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE
        assert not diagnostic.applique
        assert TABLE in diagnostic.tables_possedees

    def test_l_attribut_bypassrls_est_nomme_pour_lui_meme(self, schema):
        """Distinct du cas propriétaire, parce que la correction n'est pas la même.

        Le propriétaire se corrige en changeant de rôle applicatif ; celui-ci se corrige
        par un `ALTER ROLE … NOBYPASSRLS`. Un verdict unique enverrait l'exploitant vers
        le mauvais geste.
        """
        diagnostic = _diagnostic(ROLE_CONTOURNANT)
        assert diagnostic.etat is EtatDuCloisonnement.CONTOURNE_BYPASSRLS
        assert "NOBYPASSRLS" in diagnostic.explication

    def test_le_superutilisateur_passe_avant_tout_le_reste(self, schema, proprietaire):
        """L'ordre des vérifications compte, et ce cas le prouve.

        Le rôle des tests possède aussi des tables : il relève donc de deux causes à la
        fois. Le verdict doit nommer la plus grave, celle qu'aucune configuration ne
        corrige — un superutilisateur contourne même `FORCE ROW LEVEL SECURITY`.
        """
        with proprietaire.connect() as connexion:
            diagnostic = diagnostiquer(connexion)
        assert diagnostic.etat is EtatDuCloisonnement.CONTOURNE_SUPERUTILISATEUR
        assert "superutilisateur" in diagnostic.explication

    def test_une_table_cloisonnee_sans_politique_est_un_contournement(
        self, schema, proprietaire
    ):
        """Une politique absente et un rôle exempté ont la même conséquence.

        La cause diffère — migrations non jouées d'un côté, rôle mal choisi de l'autre —
        mais dans les deux cas les lignes d'un cabinet sont lisibles par un autre. Le
        diagnostic refuse donc de dire `APPLIQUE` tant qu'une table cloisonnée n'a pas
        la sienne, quel que soit le rôle.
        """
        with proprietaire.begin() as connexion:
            connexion.execute(text(f"DROP POLICY {NOM_POLITIQUE} ON {TABLE}"))
        try:
            diagnostic = _diagnostic(ROLE_ORDINAIRE)
            assert diagnostic.etat is EtatDuCloisonnement.POLITIQUES_ABSENTES
            assert TABLE in diagnostic.tables_sans_politique
        finally:
            with proprietaire.begin() as connexion:
                connexion.execute(
                    text(
                        f"CREATE POLICY {NOM_POLITIQUE} ON {TABLE} USING "
                        f"({COLONNE_CLOISON} = "
                        f"current_setting('{VARIABLE_SESSION}', true))"
                    )
                )

    def test_force_row_level_security_ferme_le_contournement_du_proprietaire(
        self, schema, proprietaire
    ):
        """La seule façon de rendre un propriétaire soumis à ses politiques.

        Elle est reconnue plutôt qu'imposée : une installation qui a choisi `FORCE` au
        lieu des deux rôles est correctement cloisonnée, et le diagnostic doit le dire
        au lieu de réclamer une configuration qu'elle n'a pas besoin d'adopter.
        """
        with proprietaire.begin() as connexion:
            connexion.execute(text(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY"))
        try:
            assert _diagnostic(ROLE_PROPRIETAIRE).applique
        finally:
            with proprietaire.begin() as connexion:
                connexion.execute(
                    text(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
                )
