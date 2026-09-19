"""Chaque réalisation doit couvrir toute la surface de son port.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Un `Protocol` de Python **ne vérifie rien à l'exécution**. C'est un contrat pour
l'analyse statique : une méthode oubliée dans une réalisation ne se voit qu'au
premier appel, en production, sur la route qui en avait besoin.

C'est arrivé. `DepotPiecesSql` ne réalisait ni `par_identifiant`, ni
`recues_entre`, ni `par_empreinte`. Rien ne le signalait, pour une raison
précise et instructive : **les tests tournaient sur les dépôts en mémoire**, où
la réalisation était complète. Les deux réalisations d'un même port avaient
divergé, et seule celle qui n'était pas exercée manquait de méthodes.

Le défaut est apparu en interrogeant l'application réelle sur PostgreSQL, dans
un conteneur : la route de téléchargement d'un justificatif rendait un `500`.

⚠️ LES CAS SONT DÉCOUVERTS, JAMAIS ÉNUMÉRÉS

Une première version de ce fichier supposait deux noms de module fixes,
`depots_sql` et `depots_memoire`. Le dépôt en emploie cinq — `depots_sql`,
`depots_memoire`, `depot_ecritures_memoire`, `depot_entreprises_sql`,
`depot_entreprises_memoire`. Quatre contextes sur six échappaient donc au
contrôle **en le laissant vert**, ce qui est exactement le défaut que ce fichier
prétend attraper. Un test qui énumère ses cas à la main finit par en oublier.

CE QU'IL ATTRAPE, ET CE QU'IL N'ATTRAPE PAS

Il compare des **noms de méthodes**. Il attrapera l'oubli pur — le cas qui s'est
produit — et rien d'autre : deux réalisations peuvent porter les mêmes noms et
se comporter différemment. C'est bon marché, entièrement automatique, et cela
couvre la faute la plus fréquente.

⚠️ Une divergence de comportement connue est consignée au journal : le dépôt SQL
des dossiers trie par dénomination, le dépôt mémoire par ordre d'insertion. Ce
test-ci ne la voit pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import ModuleType

import pytest

#: Les modules d'adaptateur sortant qui ne réalisent aucun port de dépôt :
#: catalogues de référentiel, plan comptable, jeux de démonstration, tables.
#: Les écarter évite d'importer pour rien, sans jamais masquer une réalisation —
#: le contrôle de couverture plus bas s'en assure.
_HORS_SUJET = frozenset({"__init__", "tables", "donnees_demo"})


def _cas() -> list[tuple[str, str]]:
    """Tous les couples (contexte, module d'adaptateur sortant) du dépôt."""
    racine = Path(__file__).resolve().parents[1] / "app" / "contextes"
    return [
        (chemin.parents[2].name, chemin.stem)
        for chemin in sorted(racine.glob("*/adaptateurs/sortant/*.py"))
        if chemin.stem not in _HORS_SUJET
    ]


def _module(contexte: str, nom: str) -> ModuleType:
    return importlib.import_module(f"app.contextes.{contexte}.adaptateurs.sortant.{nom}")


def _ports(contexte: str) -> ModuleType | None:
    try:
        return importlib.import_module(f"app.contextes.{contexte}.domaine.ports")
    except ModuleNotFoundError:
        return None


def _methodes_publiques(objet: type) -> frozenset[str]:
    return frozenset(
        nom for nom, _ in inspect.getmembers(objet, callable) if not nom.startswith("_")
    )


def _protocoles(module: ModuleType) -> list[type]:
    return [
        objet
        for _, objet in inspect.getmembers(module, inspect.isclass)
        if getattr(objet, "_is_protocol", False) and objet.__module__ == module.__name__
    ]


def _realisations(module: ModuleType, protocole: type) -> list[type]:
    """Les classes du module dont le nom dérive de celui du protocole.

    Convention du dépôt : `DepotPieces` → `DepotPiecesSql`, `DepotPiecesMemoire`.
    Une réalisation nommée autrement échappe à ce contrôle — c'est le prix d'un
    test qui ne demande aucune déclaration manuelle, et `test_le_controle_...`
    plus bas signale le jour où la convention se perd.
    """
    return [
        objet
        for _, objet in inspect.getmembers(module, inspect.isclass)
        if objet.__module__ == module.__name__
        and objet.__name__.startswith(protocole.__name__)
        and objet.__name__ != protocole.__name__
    ]


def _manques(contexte: str, module: str) -> list[str]:
    ports = _ports(contexte)
    if ports is None:
        return []
    adaptateur = _module(contexte, module)
    trouves: list[str] = []
    for protocole in _protocoles(ports):
        attendues = _methodes_publiques(protocole)
        for realisation in _realisations(adaptateur, protocole):
            absentes = attendues - _methodes_publiques(realisation)
            if absentes:
                trouves.append(
                    f"{realisation.__name__} ne réalise pas {sorted(absentes)} "
                    f"du port {protocole.__name__}"
                )
    return trouves


@pytest.mark.parametrize(("contexte", "module"), _cas())
def test_chaque_realisation_couvre_son_port(contexte: str, module: str):
    if _ports(contexte) is None:
        pytest.skip(f"{contexte} n'a pas de module `domaine/ports.py`")
    manques = _manques(contexte, module)
    assert not manques, (
        "Des réalisations sont incomplètes. Un Protocol ne vérifie rien à "
        "l'exécution : ces méthodes manqueront au premier appel, en "
        "production.\n  · " + "\n  · ".join(manques)
    )


def test_le_controle_couvre_reellement_des_realisations():
    """Le garde-fou du garde-fou.

    Un test qui découvre ses cas peut n'en découvrir aucun et rester vert. Si la
    convention de nommage change, ce contrôle-ci tombe avant que le silence ne
    s'installe.
    """
    rapprochees = 0
    for contexte, module in _cas():
        ports = _ports(contexte)
        if ports is None:
            continue
        adaptateur = _module(contexte, module)
        for protocole in _protocoles(ports):
            rapprochees += len(_realisations(adaptateur, protocole))
    assert rapprochees >= 15, (
        f"Seules {rapprochees} réalisations ont été rapprochées d'un port. "
        "La convention de nommage a probablement changé, et ce contrôle ne "
        "vérifie plus grand-chose."
    )
