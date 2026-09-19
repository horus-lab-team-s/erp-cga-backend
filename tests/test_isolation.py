"""Le test d'isolation : aucune table ne laisse voir les lignes d'un autre locataire.

Cinquième pas du socle multi-tenant, et le seul dont l'échec doit bloquer une livraison.

⚠️ **Il est écrit comme une boucle sur les métadonnées, jamais table par table.** C'est la
propriété qui compte : une table cloisonnée ajoutée demain sera couverte sans que personne
y pense. Écrit à la main, il ne couvrirait que ce qui existait le jour où on l'a écrit — et
la table oubliée serait précisément celle qui fuit.

CE QU'IL VÉRIFIE, ET CE QU'IL NE SUFFIT PAS DE VÉRIFIER

Constater qu'une requête ne rend rien ne prouve rien : une table vide donne le même
résultat qu'une politique qui fonctionne. Chaque cas vérifie donc **les deux sens** — la
ligne est invisible au locataire A, et visible au locataire B. Sans le second, un semis
défaillant ferait passer le test au vert en ne testant rien.

LA FIXTURE CONSTATE LE CLOISONNEMENT AVANT DE SEMER, ET CE N'EST PAS POUR ATTRAPER
UN FAUX VERT

L'ajout a d'abord été justifié par « sans lui, la suite passerait au vert avec un rôle
qui contourne ». **C'était faux, et l'épreuve l'a dit** : `ALTER ROLE … BYPASSRLS` fait
échouer vingt-deux cas sur vingt-quatre, parce que chaque cas vérifie les deux sens et
que le sens « invisible au locataire A » tombe aussitôt. La suite est saine sans ce
constat.

Ce qu'il apporte est plus modeste, et suffit à le garder : il change **vingt-deux échecs
en un diagnostic**. Sans lui, la sortie répète « 2 lignes attendues, 0 obtenues » vingt-
deux fois sans jamais nommer la cause, et il faut penser au rôle, ce qui vient tard. Avec
lui, la fixture s'arrête avant de semer sur une phrase qui nomme le rôle, l'attribut en
cause et le fichier qui le corrige.

Le cas n'est pas théorique : un rôle PostgreSQL est un objet de l'**instance** et non de
la base, et le `IF NOT EXISTS` ci-dessous réemploie un rôle déjà présent. Rien ne dit
qu'un administrateur ne lui a pas accordé `BYPASSRLS` entre-temps sur une instance
partagée. Voir `app/infrastructure/roles.py`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
    create_engine,
    text,
)

from app.infrastructure.roles import diagnostiquer
from app.infrastructure.securite_lignes import (
    COLONNE_CLOISON,
    VARIABLE_SESSION,
    instructions_activation,
    instructions_desactivation,
    tables_cloisonnees,
)
from app.tables import METADONNEES
from tests.conftest import URL_BASE_TEST, exige_postgresql

ROLE_APPLICATIF = "cga_app_isolation"
MOT_DE_PASSE = "essai"

LOCATAIRE_A = "CENTRE-A"
LOCATAIRE_B = "CENTRE-B"

#: `compte` d'abord : trois tables la référencent, et une clé étrangère non satisfaite
#: ferait échouer le semis pour une raison qui n'a rien à voir avec le cloisonnement.
TABLE_REFERENCEE = "compte"


def _valeur(colonne, locataire: str) -> object:
    """Une valeur acceptable pour cette colonne, dérivée de son type.

    Le contenu n'a aucune importance : on vérifie qui voit la ligne, pas ce qu'elle
    contient. Seule la colonne de cloisonnement doit porter la bonne valeur.
    """
    if colonne.name == COLONNE_CLOISON:
        return locataire
    if colonne.foreign_keys:
        # La valeur est **dérivée de la colonne visée**, pas devinée : c'est la seule
        # façon qu'elle corresponde à ce que le semis a écrit dans la table référencée.
        cible = next(iter(colonne.foreign_keys)).column
        return _valeur(cible, locataire)

    type_ = colonne.type
    if isinstance(type_, String):
        # Le locataire vient **en tête** : tronquer à la longueur déclarée ne doit pas
        # faire coïncider les valeurs de deux locataires, sinon une contrainte d'unicité
        # ferait échouer le semis pour une raison sans rapport avec le cloisonnement.
        return f"{locataire}-{colonne.name}"[: (type_.length or 64)]
    if isinstance(type_, (Integer, BigInteger)):
        # Dépendant du locataire : deux zéros se heurteraient à la moindre contrainte
        # d'unicité, et le semis échouerait pour une raison sans rapport avec le
        # cloisonnement. La dérivation est stable d'un processus à l'autre, contrairement
        # au hachage des chaînes, qui est aléatoirisé.
        return sum(map(ord, locataire)) % 900 + 1
    if isinstance(type_, Numeric):
        return Decimal(0)
    if isinstance(type_, Boolean):
        return False
    if isinstance(type_, DateTime):
        return datetime(2026, 1, 1, 12, 0, 0)
    if isinstance(type_, Date):
        return date(2026, 1, 1)
    if isinstance(type_, JSON):
        # Une chaîne, pas un dictionnaire : en SQL direct le pilote ne sait pas adapter
        # un `dict`, et le transtypage est ajouté au moment de composer l'insertion.
        return "{}"
    raise AssertionError(
        f"type non couvert par le semis : {type_!r} sur {colonne.table.name}.{colonne.name}. "
        "Ajouter le cas ici plutôt que d'exclure la table, sinon elle cesse d'être vérifiée."
    )


def _colonnes_a_fournir(table: Table) -> list:
    """Les colonnes qu'il faut renseigner en SQL direct.

    ⚠️ Seul `server_default` dispense de fournir une valeur. Un `default` déclaré côté
    Python est appliqué par l'ORM, **pas par la base** : une insertion en SQL textuel,
    comme celles de ce fichier, se heurte alors à la contrainte de non-nullité. La
    distinction est invisible tant qu'on écrit par l'ORM, et elle se paie au premier
    script de reprise ou de semis.
    """
    return [
        colonne
        for colonne in table.columns
        if not colonne.nullable
        and colonne.server_default is None
        # Voir `_attribuee_par_la_base`.
        and not _attribuee_par_la_base(table, colonne)
    ]


def _attribuee_par_la_base(table: Table, colonne) -> bool:
    """La base fournira-t-elle la valeur toute seule ?

    Seule une clé primaire entière **à colonne unique** devient une séquence. Une clé
    composite n'en reçoit pas : y renoncer laisserait une colonne non nulle sans valeur,
    et le semis échouerait pour une raison sans rapport avec le cloisonnement.
    """
    return (
        colonne.primary_key
        and colonne.autoincrement is not False
        and isinstance(colonne.type, (Integer, BigInteger))
        and len(table.primary_key.columns) == 1
    )


def _semer(connexion, table: Table, locataire: str) -> None:
    colonnes = _colonnes_a_fournir(table)
    valeurs = {colonne.name: _valeur(colonne, locataire) for colonne in colonnes}
    noms = ", ".join(colonne.name for colonne in colonnes)
    # Le transtypage est porté par la requête et non par la valeur : en SQL direct, un
    # paramètre lié arrive en texte, et PostgreSQL ne le convertit pas tout seul en json.
    #
    # `CAST(... AS json)` et non `::json` : la syntaxe abrégée emploie deux-points, que
    # SQLAlchemy prend pour le début d'un paramètre lié et laisse alors intact.
    parametres = ", ".join(
        f"CAST(:{colonne.name} AS json)" if isinstance(colonne.type, JSON) else f":{colonne.name}"
        for colonne in colonnes
    )
    connexion.execute(text(f"INSERT INTO {table.name} ({noms}) VALUES ({parametres})"), valeurs)


@exige_postgresql
class TestAucuneTableNeFuit:
    """La boucle. Un cas par table cloisonnée, engendré depuis les métadonnées."""

    @pytest.fixture(scope="class")
    def base(self):
        proprietaire = create_engine(URL_BASE_TEST, pool_pre_ping=True)
        METADONNEES.drop_all(proprietaire)
        METADONNEES.create_all(proprietaire)

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
            connexion.execute(text(f"GRANT USAGE ON SCHEMA public TO {ROLE_APPLICATIF}"))
            connexion.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                    f"TO {ROLE_APPLICATIF}"
                )
            )

        # Le semis : une ligne par table pour chaque locataire, `compte` en tête.
        tables = tables_cloisonnees(METADONNEES)
        ordre = sorted(tables, key=lambda t: (t.name != TABLE_REFERENCEE, t.name))
        with proprietaire.begin() as connexion:
            for locataire in (LOCATAIRE_A, LOCATAIRE_B):
                for table in ordre:
                    _semer(connexion, table, locataire)

        applicatif = create_engine(
            URL_BASE_TEST.replace("cga:cga@", f"{ROLE_APPLICATIF}:{MOT_DE_PASSE}@"),
            pool_pre_ping=True,
        )
        # Voir l'en-tête : ce constat ne rattrape pas un faux vert — les cas
        # échouent d'eux-mêmes — il remplace vingt-deux « 0 != 2 » par une phrase
        # qui nomme le rôle, l'attribut fautif et le fichier qui le corrige.
        with applicatif.connect() as connexion:
            diagnostic = diagnostiquer(connexion)
        assert diagnostic.applique, diagnostic.explication
        yield applicatif
        applicatif.dispose()
        with proprietaire.begin() as connexion:
            for instruction in instructions_desactivation(METADONNEES):
                connexion.execute(text(instruction))
            connexion.execute(text(f"DROP OWNED BY {ROLE_APPLICATIF}"))
        METADONNEES.drop_all(proprietaire)
        proprietaire.dispose()

    @staticmethod
    def _compter(applicatif, table: str, locataire: str) -> int:
        with applicatif.begin() as connexion:
            connexion.execute(
                text(f"SELECT set_config('{VARIABLE_SESSION}', :l, true)"), {"l": locataire}
            )
            return connexion.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

    @pytest.mark.parametrize(
        "nom_table",
        [table.name for table in tables_cloisonnees(METADONNEES)],
    )
    def test_une_table_ne_montre_que_les_lignes_de_son_locataire(self, base, nom_table: str):
        """Les deux sens, et c'est ce qui rend le cas probant.

        Constater qu'une requête ne rend rien ne prouve rien : une table vide donnerait le
        même résultat. On vérifie donc que la ligne existe pour B avant de constater
        qu'elle est invisible à A.
        """
        vues_par_b = self._compter(base, nom_table, LOCATAIRE_B)
        assert vues_par_b == 1, (
            f"le semis de {nom_table} a échoué : sans ligne, l'absence côté A ne prouve rien"
        )

        vues_par_a = self._compter(base, nom_table, LOCATAIRE_A)
        assert vues_par_a == 1, f"{nom_table} : le locataire A doit voir sa propre ligne"

        with base.begin() as connexion:
            connexion.execute(
                text(f"SELECT set_config('{VARIABLE_SESSION}', :l, true)"), {"l": LOCATAIRE_A}
            )
            locataires = (
                connexion.execute(text(f"SELECT {COLONNE_CLOISON} FROM {nom_table}"))
                .scalars()
                .all()
            )
        assert set(locataires) == {LOCATAIRE_A}, (
            f"{nom_table} laisse voir les lignes de {LOCATAIRE_B} au locataire {LOCATAIRE_A}"
        )

    def test_sans_locataire_annonce_aucune_table_ne_montre_rien(self, base):
        """Le défaut sûr, vérifié sur toutes les tables d'un coup."""
        fuites = []
        with base.begin() as connexion:
            for table in tables_cloisonnees(METADONNEES):
                nombre = connexion.execute(
                    text(f"SELECT count(*) FROM {table.name}")
                ).scalar_one()
                if nombre:
                    fuites.append(f"{table.name} ({nombre} ligne(s))")
        assert not fuites, (
            f"ces tables se laissent lire sans locataire annoncé : {fuites}. "
            "Une session qui n'a pas dit pour qui elle lit ne doit rien voir."
        )


class TestLaBoucleCouvreToutSansOubli:
    """Ce qui garantit qu'une table ajoutée demain sera vérifiée."""

    def test_le_cas_est_engendre_pour_chaque_table_cloisonnee(self):
        """La liste des cas se dérive des métadonnées. Une table ajoutée demain apparaît
        toute seule ; une table écrite à la main serait oubliée le jour où elle compte."""
        attendues = {table.name for table in tables_cloisonnees(METADONNEES)}
        marqueur = TestAucuneTableNeFuit.test_une_table_ne_montre_que_les_lignes_de_son_locataire
        engendres = {
            marque.args[1][i]
            for marque in marqueur.pytestmark
            if marque.name == "parametrize"
            for i in range(len(marque.args[1]))
        }
        assert engendres == attendues

    def test_toute_colonne_non_nulle_sait_se_semer(self):
        """Un type non couvert ferait échouer le semis, donc le test entier, sur une
        raison sans rapport avec le cloisonnement. Mieux vaut le savoir ici."""
        for table in tables_cloisonnees(METADONNEES):
            for colonne in _colonnes_a_fournir(table):
                _valeur(colonne, LOCATAIRE_A)
