"""Le recensement des tables est-il complet ?

Ce test existe parce que l'oubli s'est produit **trois fois** : à l'amorçage, à
la première migration des contextes métier, puis dans les tests de persistance.
Chaque fois, une table déclarée n'était pas chargée, et chaque fois l'échec
survenait loin de la cause — « relation does not exist », ou une migration vide.

Il ne suffit pas de documenter un piège pour le refermer.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.tables import METADONNEES, MODULES, noms_des_tables

RACINE = Path(__file__).resolve().parents[1]
CONTEXTES = RACINE / "app" / "contextes"


def _modules_de_tables_sur_disque() -> set[str]:
    """Les fichiers `adaptateurs/sortant/tables.py` réellement présents."""
    return {
        chemin.relative_to(RACINE).with_suffix("").as_posix().replace("/", ".")
        for chemin in CONTEXTES.glob("*/adaptateurs/sortant/tables.py")
    }


def test_aucun_module_de_tables_n_est_oublie():
    """Un fichier de tables non recensé donnerait une migration qui ne le crée
    pas — et, pire, un `autogenerate` qui proposerait de supprimer ses tables si
    elles existaient déjà en base."""
    recenses = {module.__name__ for module in MODULES}
    manquants = _modules_de_tables_sur_disque() - recenses
    assert not manquants, (
        f"modules de tables absents de app/tables.py : {sorted(manquants)}. "
        "Les y ajouter, sinon ni les migrations ni les tests ne les verront."
    )


def test_chaque_module_recense_declare_au_moins_une_table():
    """Un module recensé mais vide signale un renommage inachevé."""
    for module in MODULES:
        declarees = [nom for nom in getattr(module, "__all__", []) if nom.startswith("Table")]
        assert declarees, f"{module.__name__} ne déclare aucune table"


def test_toutes_les_tables_declarees_sont_dans_les_metadonnees():
    """Le pont entre la déclaration et les métadonnées.

    Une classe qui hériterait de `Base` sans `__tablename__`, ou qui serait
    déclarée dans un module non importé, n'apparaîtrait pas ici.
    """
    attendues = sum(
        len([nom for nom in getattr(module, "__all__", []) if nom.startswith("Table")])
        for module in MODULES
    )
    assert len(METADONNEES.tables) == attendues, (
        f"{attendues} tables déclarées, {len(METADONNEES.tables)} dans les "
        f"métadonnées : {noms_des_tables()}"
    )


#: Les tables qui n'ont **pas** à porter la colonne de cloisonnement, chacune avec sa
#: raison. Toute exemption doit être nommée ici, jamais silencieuse : c'est le seul
#: endroit où l'on puisse relire, d'un coup d'œil, ce qui échappe au cloisonnement.
EXEMPTIONS: dict[str, str] = {
    "tenant": (
        "La table du plan de contrôle. Elle est lue **avant** qu'on sache de quel "
        "locataire il s'agit — c'est elle qui le dit. La cloisonner créerait une "
        "dépendance circulaire à l'exécution, et sa politique refuserait chaque requête "
        "faute de variable posée : le répertoire ne verrait plus rien et tout "
        "sous-domaine rendrait 404."
    ),
    "passage_ordonnance": (
        "La mémoire de l'ordonnanceur : quand chaque travail périodique est passé "
        "pour la dernière fois. Un travail n'appartient à aucun cabinet — le relais "
        "vide la boîte d'envoi de tous les locataires, le balayage de relance les "
        "parcourt tous. Lui donner un `locataire` obligerait à en choisir un au "
        "hasard, et la réponse serait fausse quel que soit le choix. La cadence et "
        "le compteur d'échecs d'un travail ne sont l'affaire de personne en "
        "particulier."
    ),
}


def test_toute_table_cloisonnee_porte_sa_colonne():
    """Le mixin `Cloisonne` porte la colonne ; une table qui l'oublierait ne
    serait pas filtrée, et la fuite ne se verrait pas — la requête rendrait
    simplement les lignes d'un autre cabinet.

    ⚠️ Une exemption non nommée dans `EXEMPTIONS` fait échouer ce test. C'est la
    différence entre une table que l'on a décidé d'exempter et une table dont on a
    oublié le mixin : rien ne les distingue à la lecture du code.
    """
    sans_locataire = {
        nom for nom, table in METADONNEES.tables.items() if "locataire" not in table.c
    }
    non_justifiees = sorted(sans_locataire - set(EXEMPTIONS))
    assert not non_justifiees, (
        f"tables sans colonne `locataire` : {non_justifiees}. Hériter de "
        "`Cloisonne`, ou nommer l'exemption dans `EXEMPTIONS` avec sa raison."
    )


def test_aucune_exemption_ne_survit_a_sa_table():
    """Une exemption qui ne correspond plus à rien laisse croire qu'une table échappe au
    cloisonnement alors qu'elle a disparu ou qu'elle porte désormais la colonne."""
    perimees = sorted(set(EXEMPTIONS) - set(METADONNEES.tables))
    assert not perimees, f"exemptions sans table : {perimees}"


def test_chaque_exemption_porte_sa_raison():
    for nom, raison in EXEMPTIONS.items():
        assert len(raison) > 60, (
            f"l'exemption de « {nom} » n'est pas justifiée. Une exemption sans raison "
            "est une fuite qu'on a cessé de voir."
        )


def test_les_migrations_couvrent_les_tables_declarees():
    """Une table déclarée sans migration se créerait par `create_all` en test et
    manquerait en production — le pire des deux mondes, parce que la suite
    resterait verte."""
    versions = (RACINE / "alembic" / "versions").glob("*.py")
    creees: set[str] = set()
    for fichier in versions:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr == "create_table"
                and noeud.args
                and isinstance(noeud.args[0], ast.Constant)
            ):
                creees.add(noeud.args[0].value)

    manquantes = set(METADONNEES.tables) - creees
    assert not manquantes, (
        f"tables sans migration : {sorted(manquantes)}. Lancer "
        "`alembic revision --autogenerate`."
    )
