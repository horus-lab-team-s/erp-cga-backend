"""La sécurité au niveau des lignes, éprouvée sur une vraie base.

Quatrième pas du socle multi-tenant. Le filtre ORM est solide mais le SQL textuel y
échappe — sa propre docstring le dit. Cette garantie-ci vit dans la base et s'applique à
toute requête, quelle qu'en soit l'origine.

⚠️ **Ces tests se connectent avec un rôle non propriétaire.** C'est indispensable : le
propriétaire d'une table contourne ses politiques par défaut, et un test exécuté avec lui
passerait au vert en ne testant rien. C'est le piège principal de ce mécanisme, et le
seul moyen de ne pas y tomber est de le reproduire ici.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from app.infrastructure.securite_lignes import (
    COLONNE_CLOISON,
    NOM_POLITIQUE,
    VARIABLE_SESSION,
    instructions_activation,
    instructions_desactivation,
    tables_cloisonnees,
)
from tests.conftest import URL_BASE_TEST, exige_postgresql

ROLE_APPLICATIF = "cga_app_essai"
MOT_DE_PASSE = "essai"


class TestGenerationDuSql:
    """Le SQL se dérive des métadonnées, il ne s'énumère pas à la main."""

    def test_toute_table_portant_la_colonne_est_couverte(self):
        """Une liste écrite serait juste le jour où on l'écrit, et fausse à la table
        suivante — et la table oubliée serait précisément celle qui fuit."""
        from app.tables import METADONNEES

        attendues = {
            table.name
            for table in METADONNEES.tables.values()
            if COLONNE_CLOISON in table.columns
        }
        couvertes = {table.name for table in tables_cloisonnees(METADONNEES)}
        assert couvertes == attendues
        assert len(couvertes) >= 15, "le projet compte dix-sept tables cloisonnées"

    def test_chaque_table_recoit_activation_et_politique(self):
        from app.tables import METADONNEES

        sql = list(instructions_activation(METADONNEES))
        for table in tables_cloisonnees(METADONNEES):
            assert any(f"ALTER TABLE {table.name} ENABLE ROW LEVEL" in i for i in sql)
            assert any(f"CREATE POLICY {NOM_POLITIQUE} ON {table.name}" in i for i in sql)

    def test_la_politique_porte_using_et_with_check(self):
        """Sans `WITH CHECK`, une insertion mal formée créerait une ligne au nom d'un
        autre client, que son propriétaire légitime verrait apparaître sans l'avoir
        demandée. Plus rare qu'une lecture fautive, et plus difficile à défaire."""
        from app.tables import METADONNEES

        politiques = [i for i in instructions_activation(METADONNEES) if "CREATE POLICY" in i]
        assert politiques
        for politique in politiques:
            assert " USING (" in politique
            assert " WITH CHECK (" in politique

    def test_la_variable_absente_ne_montre_rien(self):
        """`current_setting(..., true)` rend NULL plutôt que de lever. Une comparaison à
        NULL n'est jamais vraie : une session qui n'a pas dit son locataire ne voit rien.
        C'est le bon défaut, et l'inverse de celui qu'on obtiendrait sans ce soin."""
        from app.tables import METADONNEES

        politiques = [i for i in instructions_activation(METADONNEES) if "CREATE POLICY" in i]
        assert all(f"current_setting('{VARIABLE_SESSION}', true)" in p for p in politiques)

    def test_la_desactivation_retire_la_politique_avant_de_desactiver(self):
        """L'ordre inverse laisserait une politique orpheline que la réactivation
        suivante ferait échouer sur un doublon de nom."""
        from app.tables import METADONNEES

        sql = list(instructions_desactivation(METADONNEES))
        premiere = tables_cloisonnees(METADONNEES)[0].name
        rang_drop = next(i for i, s in enumerate(sql) if "DROP POLICY" in s and premiere in s)
        rang_disable = next(
            i for i, s in enumerate(sql) if "DISABLE ROW LEVEL" in s and premiere in s
        )
        assert rang_drop < rang_disable


@exige_postgresql
class TestLaPolitiqueMordVraiment:
    """Sur une vraie base, avec un rôle qui n'est pas propriétaire."""

    @pytest.fixture
    def base_cloisonnee(self):
        """Des tables, leurs politiques, et un rôle applicatif restreint.

        Le rôle est créé ici plutôt que supposé : un test qui dépendrait d'un rôle
        présent sur la machine passerait chez l'un et échouerait chez l'autre.
        """
        from app.tables import METADONNEES

        proprietaire = create_engine(URL_BASE_TEST, pool_pre_ping=True)
        METADONNEES.drop_all(proprietaire)
        METADONNEES.create_all(proprietaire)

        # Le rôle d'abord, ses droits ensuite : `DROP OWNED BY` échouerait sur un rôle
        # qui n'existe pas encore, c'est-à-dire au tout premier passage.
        with proprietaire.begin() as connexion:
            connexion.execute(
                text(
                    f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
                    f"'{ROLE_APPLICATIF}') THEN CREATE ROLE {ROLE_APPLICATIF} LOGIN "
                    f"PASSWORD '{MOT_DE_PASSE}'; END IF; END $$"
                )
            )

        with proprietaire.begin() as connexion:
            for instruction in instructions_activation(METADONNEES):
                connexion.execute(text(instruction))
            connexion.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                     f"TO {ROLE_APPLICATIF}")
            )
            connexion.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE_APPLICATIF}"))

        # ⚠️ Pas 84 : l'URL du rôle restreint se construisait en remplaçant « cga:cga@ ».
        # Avec l'URL que rend `outils/postgres-local.sh start` (« cga@ », sans mot de
        # passe), le remplacement ne trouvait rien : le « rôle applicatif » se connectait
        # en superutilisateur, qui contourne la sécurité des lignes, et cinq tests
        # échouaient comme si le cloisonnement était cassé. On pose l'utilisateur et le
        # mot de passe sur l'URL analysée, quelle que soit sa forme.
        applicatif = create_engine(
            make_url(URL_BASE_TEST).set(username=ROLE_APPLICATIF, password=MOT_DE_PASSE),
            pool_pre_ping=True,
        )
        yield proprietaire, applicatif
        applicatif.dispose()
        with proprietaire.begin() as connexion:
            for instruction in instructions_desactivation(METADONNEES):
                connexion.execute(text(instruction))
            # Les droits accordés survivraient aux tables et empêcheraient le prochain
            # `drop_all`. On les rend avant de détruire.
            connexion.execute(text(f"DROP OWNED BY {ROLE_APPLICATIF}"))
        METADONNEES.drop_all(proprietaire)
        proprietaire.dispose()

    @staticmethod
    def _semer(proprietaire) -> None:
        """Deux comptes, deux locataires. Semés par le propriétaire, qui contourne."""
        with proprietaire.begin() as connexion:
            for locataire, courriel in (("CENTRE-A", "a@a.cm"), ("CENTRE-B", "b@b.cm")):
                connexion.execute(
                    text(
                        "INSERT INTO compte (identifiant, locataire, courriel, nom, prenom, "
                        "etat, cree_le, tentatives_echouees) "
                        "VALUES (:i, :l, :c, 'Essai', 'Essai', 'ACTIF', now(), 0)"
                    ),
                    {"i": f"cpt-{locataire}", "l": locataire, "c": courriel},
                )

    def test_le_role_applicatif_ne_voit_que_son_locataire(self, base_cloisonnee):
        """La garantie principale. Une requête **en SQL textuel**, celle-là même qui
        échappe au filtre ORM, ne rend que les lignes du locataire annoncé."""
        proprietaire, applicatif = base_cloisonnee
        self._semer(proprietaire)

        with applicatif.begin() as connexion:
            connexion.execute(
                text(f"SELECT set_config('{VARIABLE_SESSION}', 'CENTRE-A', true)")
            )
            vus = connexion.execute(text("SELECT locataire FROM compte")).scalars().all()

        assert vus == ["CENTRE-A"], "le locataire B ne doit pas apparaître"

    def test_sans_variable_posee_il_ne_voit_rien(self, base_cloisonnee):
        """Le bon défaut : une session qui n'a pas dit pour qui elle lit ne lit rien."""
        proprietaire, applicatif = base_cloisonnee
        self._semer(proprietaire)

        with applicatif.begin() as connexion:
            vus = connexion.execute(text("SELECT locataire FROM compte")).scalars().all()

        assert vus == []

    def test_il_ne_peut_pas_ecrire_pour_un_autre_locataire(self, base_cloisonnee):
        """`WITH CHECK` à l'œuvre. Sans lui, on créerait une ligne au nom d'un autre
        client, que son propriétaire légitime verrait apparaître sans l'avoir demandée."""
        proprietaire, applicatif = base_cloisonnee
        self._semer(proprietaire)

        with pytest.raises(ProgrammingError, match="row-level security"):
            with applicatif.begin() as connexion:
                connexion.execute(
                    text(f"SELECT set_config('{VARIABLE_SESSION}', 'CENTRE-A', true)")
                )
                connexion.execute(
                    text(
                        "INSERT INTO compte (identifiant, locataire, courriel, nom, prenom, "
                        "etat, cree_le, tentatives_echouees) VALUES "
                        "('intrus', 'CENTRE-B', 'intrus@b.cm', 'X', 'X', 'ACTIF', now(), 0)"
                    )
                )

    def test_il_ne_peut_pas_supprimer_les_lignes_d_un_autre(self, base_cloisonnee):
        proprietaire, applicatif = base_cloisonnee
        self._semer(proprietaire)

        with applicatif.begin() as connexion:
            connexion.execute(
                text(f"SELECT set_config('{VARIABLE_SESSION}', 'CENTRE-A', true)")
            )
            connexion.execute(text("DELETE FROM compte WHERE locataire = 'CENTRE-B'"))

        with proprietaire.begin() as connexion:
            restants = connexion.execute(
                text("SELECT locataire FROM compte ORDER BY locataire")
            ).scalars().all()
        assert restants == ["CENTRE-A", "CENTRE-B"], "la ligne de B doit avoir survécu"

    def test_la_variable_ne_survit_pas_a_la_transaction(self, base_cloisonnee):
        """Le point qui rend le bac de connexions sûr. Posée pour la connexion, la
        variable serait héritée par la requête suivante, servie à un autre client — un
        défaut intermittent qui dépendrait de la charge."""
        proprietaire, applicatif = base_cloisonnee
        self._semer(proprietaire)

        with applicatif.connect() as connexion:
            with connexion.begin():
                connexion.execute(
                    text(f"SELECT set_config('{VARIABLE_SESSION}', 'CENTRE-A', true)")
                )
                assert connexion.execute(text("SELECT count(*) FROM compte")).scalar() == 1
            with connexion.begin():
                assert connexion.execute(text("SELECT count(*) FROM compte")).scalar() == 0

    def test_le_proprietaire_contourne_et_c_est_voulu(self, base_cloisonnee):
        """Les migrations et les exports d'administration doivent voir les données. C'est
        aussi pourquoi le rôle applicatif ne doit **jamais** être le propriétaire, et
        pourquoi ce fichier de test emploie deux rôles."""
        proprietaire, _ = base_cloisonnee
        self._semer(proprietaire)

        with proprietaire.begin() as connexion:
            vus = connexion.execute(text("SELECT locataire FROM compte")).scalars().all()
        assert sorted(vus) == ["CENTRE-A", "CENTRE-B"]
