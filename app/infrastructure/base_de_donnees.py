"""La base de données : moteur, session, et le cloisonnement qui s'applique tout seul.

─────────────────────────────────────────────────────────────────────────────────
LE CLOISONNEMENT EST POSÉ ICI, ET NULLE PART AILLEURS

`05-securite-multitenant.md` § 1 l'écrit : « Filtrage appliqué **dans la couche de
persistance** — session SQLAlchemy avec filtre systématique —, jamais laissé au
développeur qui écrit la requête. »

La raison tient en une observation : un filtre qu'il faut penser à écrire est un
filtre qu'on oubliera, et **l'oubli ne se voit pas**. La requête ne plante pas,
elle rend simplement des lignes en trop — celles d'un autre cabinet. Personne ne
s'en aperçoit avant qu'un adhérent ne reconnaisse le nom d'un concurrent sur son
écran.

Le filtre est donc greffé sur l'évènement `do_orm_execute` de SQLAlchemy : toute
requête ORM portant sur une table qui a une colonne `locataire` reçoit
automatiquement son critère. On ne peut pas l'oublier, parce qu'on ne l'écrit
jamais.

⚠️ Ce que cela ne couvre pas, et qu'il faut savoir : le SQL écrit à la main
— `session.execute(text(...))` — **échappe au filtre**. Le raccourci existe, il
est parfois nécessaire, et il doit alors porter son `WHERE locataire = …` en
toutes lettres. Toute requête textuelle touchant une table cloisonnée est à
relire à ce titre.

LA CONVENTION DE NOMMAGE N'EST PAS COSMÉTIQUE

Sans elle, PostgreSQL nomme lui-même les contraintes, et Alembic ne sait plus les
retrouver pour les modifier : une migration qui veut supprimer une clé étrangère
doit la nommer. On la fixe avant la première table, parce qu'après, il faut
renommer l'existant.

POURQUOI PAS D'`autoflush`

Il envoie des `INSERT` au moindre `SELECT` intercalé, ce qui rend l'ordre des
écritures imprévisible et fait échouer des contraintes à des endroits qui n'ont
rien à voir avec le code fautif. On écrit quand on le décide.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import JSON, MetaData, String, event, text
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    declared_attr,
    mapped_column,
    sessionmaker,
    with_loader_criteria,
)

from app.infrastructure.config import configuration
from app.infrastructure.securite_lignes import VARIABLE_SESSION

__all__ = [
    "METADONNEES",
    "Base",
    "Cloisonne",
    "Document",
    "creer_les_tables",
    "moteur",
    "session_courante",
    "session_du_locataire",
]

#: La session ouverte par `session_du_locataire` pour le bloc en cours (pas 95).
#:
#: ⚠️ POURQUOI ICI, ET NON DANS L'ATELIER DU TRANSVERSE
#:
#: Les contextes métier obtiennent leur session par `session_de_travail()`, qui vit dans le
#: transverse. Le **référentiel** ne le peut pas : il appartient au socle, le transverse le
#: lit, et l'inverse créerait un cycle. Or depuis le pas 95, le référentiel conserve les
#: décisions de chaque cabinet. La session est donc posée au niveau où elle naît, dans
#: l'infrastructure, que tout adaptateur peut lire sans dépendre d'aucun contexte.
_SESSION_COURANTE: ContextVar[Session | None] = ContextVar("session_courante", default=None)


def session_courante() -> Session | None:
    """La session du bloc `session_du_locataire` en cours, ou `None` hors base."""
    return _SESSION_COURANTE.get()

#: Nommage explicite des contraintes — voir l'en-tête.
#:
#: `ix` index, `uq` unicité, `ck` contrôle, `fk` clé étrangère, `pk` clé primaire.
_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

METADONNEES = MetaData(naming_convention=_CONVENTION)


class Base(DeclarativeBase):
    """Racine des tables. Une seule métadonnée pour tout le système.

    Une métadonnée par contexte aurait paru plus propre. Elle empêcherait de
    créer les tables en une passe, de vérifier les clés étrangères entre
    contextes, et surtout de faire migrer l'ensemble d'un coup — trois opérations
    que l'on fait constamment. Les frontières entre contextes sont tenues par le
    code et par `test_architecture.py`, pas par le schéma.
    """

    metadata = METADONNEES


@lru_cache
def moteur() -> Engine:
    """Le moteur, un par processus.

    `pool_pre_ping` : PostgreSQL ferme les connexions inactives, et une connexion
    morte reprise du bac produit une erreur au premier appel après une période
    creuse — typiquement le lundi matin. Le coût est un aller-retour par emprunt ;
    le bénéfice est de ne pas expliquer chaque semaine une panne qui se répare
    toute seule au second essai.
    """
    config = configuration()
    return create_engine(
        config.url_base_de_donnees,
        pool_pre_ping=True,
        # `echo` sur une variable et non sur un booléen en dur : lire le SQL émis
        # est le premier geste de diagnostic, et il ne doit pas demander un
        # changement de code.
        echo=config.tracer_le_sql,
    )


@lru_cache
def _fabrique() -> sessionmaker[Session]:
    return sessionmaker(
        bind=moteur(),
        # Voir l'en-tête.
        autoflush=False,
        # Les entités restent lisibles après `commit` : sans cela, tout accès à
        # un attribut après validation relance une requête, et un objet détaché
        # lève. Les dépôts rendent des modèles Pydantic, pas des entités ORM —
        # mais les construire suppose de lire les attributs après écriture.
        expire_on_commit=False,
    )


class Cloisonne:
    """Mixin des tables qui appartiennent à un locataire.

    ─────────────────────────────────────────────────────────────────────────
    IL PORTE LA COLONNE, ET C'EST INDISPENSABLE

    Un simple marqueur annoté ne suffit pas : `with_loader_criteria` inspecte
    l'expression `entite.locataire`, et il lui faut un attribut **mappé** sur le
    type visé. Une annotation nue lève au premier filtrage — ce qui s'est
    produit, et se voit tout de suite.

    Le déclarer ici a un second effet, meilleur : une table cloisonnée ne peut
    pas **oublier** la colonne, puisqu'elle en hérite. Le seul oubli possible
    devient l'oubli du mixin lui-même, qui se lit sur la ligne de déclaration.

    Les index composites restent portés par chaque table dans `__table_args__`,
    où ils commencent tous par `locataire`. Un index simple ici ferait double
    emploi avec eux.
    ─────────────────────────────────────────────────────────────────────────
    """

    @declared_attr
    @classmethod
    def locataire(cls) -> Mapped[str]:
        return mapped_column(String(64), nullable=False)


@contextmanager
def session_du_locataire(locataire: str) -> Iterator[Session]:
    """Une session dont **toute** requête ORM est filtrée sur ce locataire.

    Le filtre est posé par un écouteur `do_orm_execute`, avec
    `with_loader_criteria` : il s'applique aux entités chargées, y compris par
    relation, et il ne peut pas être oublié puisqu'il n'est jamais écrit.

    Validation et annulation sont portées ici : une session qui sort du bloc sans
    `commit` est annulée. Laisser une transaction ouverte tenir des verrous sur
    une table du journal d'audit bloquerait toutes les écritures suivantes.
    """
    session = _fabrique()()

    # La ceinture, sous les bretelles de l'ORM. `set_config(..., true)` limite la
    # portée à la transaction : posée pour la connexion, la variable survivrait à la
    # requête et serait héritée par la suivante, servie à un autre client. Le défaut
    # serait intermittent, dépendrait de la charge, et ne se reproduirait jamais en
    # développement.
    #
    # `set_config` et non `SET LOCAL` parce que `SET` n'accepte pas de paramètre lié.
    # Concaténer un nom de locataire dans du SQL serait une injection en attente.
    session.execute(
        text(f"SELECT set_config('{VARIABLE_SESSION}', :locataire, true)"),
        {"locataire": locataire},
    )

    @event.listens_for(session, "do_orm_execute")
    def _cloisonner(etat) -> None:  # pragma: no cover - couvert par les tests SQL
        if etat.is_select and not etat.is_column_load and not etat.is_relationship_load:
            etat.statement = etat.statement.options(
                with_loader_criteria(
                    Cloisonne,
                    lambda entite: entite.locataire == locataire,
                    include_aliases=True,
                )
            )

    jeton = _SESSION_COURANTE.set(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        _SESSION_COURANTE.reset(jeton)
        session.close()


class Document:
    """Mixin des tables qui conservent l'entité entière, sérialisée.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI UN DOCUMENT PLUTÔT QU'UN SCHÉMA NORMALISÉ

    Les entités de ce système sont des **agrégats immuables** : une entreprise
    porte ses régimes, ses rattachements, ses adhésions, ses exercices, ses
    mandats, ses dirigeants et ses associés. Une écriture porte ses lignes. Un
    devis porte ses lignes et son prospect.

    Trois faits décident :

    1. **On les lit toujours entiers.** Aucun cas d'usage ne charge « les lignes
       d'une écriture » sans l'écriture. Normaliser produirait une trentaine de
       tables et autant de jointures pour rendre exactement le même objet.
    2. **Elles sont immuables.** Un changement produit un nouvel exemplaire, pas
       une mise à jour de sous-ligne. Il n'y a donc rien à modifier finement.
    3. **Les calculs sont déjà en Python.** La balance et le grand livre sont
       calculés sur les écritures et **jamais stockés** — décision du contexte E.
       Rendre les lignes interrogeables en SQL ne servirait aujourd'hui personne.

    CE QUE CELA COÛTE, ET COMMENT ON EN SORT

    Aucune intégrité référentielle sur le contenu imbriqué, et aucune requête SQL
    directe dessus. Le jour où « quels dossiers étaient au réel en 2024 » devra
    se répondre en SQL, les périodes prendront leur table — et la colonne
    document restera, parce que `JSONB` se requête aussi.

    ⚠️ La contrepartie qui compte : **le document doit rester relisable**. Un
    champ retiré d'une entité fait échouer la validation des lignes anciennes.
    Une évolution se fait donc en ajoutant un champ facultatif, jamais en
    retirant — et une migration de données quand il faut vraiment retirer.

    LES COLONNES PROMUES

    Ce sur quoi on filtre, trie, joint ou pose une contrainte sort du document et
    devient une vraie colonne. Elle est alors écrite **deux fois** — dans la
    colonne et dans le document — et c'est la colonne qui sert aux requêtes, le
    document qui fait foi à la relecture.
    ─────────────────────────────────────────────────────────────────────────
    """

    @declared_attr
    @classmethod
    def donnees(cls) -> Mapped[dict]:
        return mapped_column(JSON, nullable=False)


def creer_les_tables() -> None:
    """Crée ce qui manque, sans rien modifier de l'existant.

    ⚠️ Ce n'est **pas** une migration. `create_all` ne modifie aucune table déjà
    présente : une colonne ajoutée au modèle n'apparaîtra pas, et le code
    échouera sur une colonne absente sans que rien n'explique pourquoi.

    Bon pour amorcer une base vide et pour les tests. Toute évolution de schéma
    passe par Alembic — voir `alembic/`.
    """
    Base.metadata.create_all(moteur())
