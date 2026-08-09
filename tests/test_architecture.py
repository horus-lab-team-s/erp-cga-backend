"""Garde-fous d'architecture.

Un monolithe modulaire ne tient pas par la bonne volonté : il tient parce qu'une
dépendance interdite fait échouer la CI. Ces tests sont la seule chose qui empêche les
onze contextes de redevenir un plat de spaghettis en dix-huit mois.

Référence : Docs/architecture/01-contextes-bornes.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
CONTEXTES_DIR = RACINE / "app" / "contexts"

#: Les onze contextes bornés. La clé est le nom du paquet, la valeur sa lettre au schéma.
CONTEXTES: dict[str, str] = {
    "referentiel": "A",
    "portefeuille": "B",
    "collecte": "C",
    "conformite": "D",
    "comptabilite": "E",
    "obligations": "F",
    "social": "G",
    "cloture": "H",
    "creation_entreprise": "I",
    "pilotage": "J",
    "transverse": "K",
}

#: Dépendances autorisées entre contextes, en plus de `core` et `shared` qui sont
#: transverses et accessibles à tous.
#:
#: A · Référentiel est le PIVOT : il alimente tout le monde et ne dépend de personne.
#: Toute arête ajoutée ici doit être justifiée dans 01-contextes-bornes.md.
ARETES_AUTORISEES: dict[str, set[str]] = {
    "referentiel": set(),
    "portefeuille": {"referentiel"},
    "collecte": {"referentiel", "portefeuille"},
    "conformite": {"referentiel"},
    "comptabilite": {"referentiel", "portefeuille", "conformite"},
    "obligations": {"referentiel", "portefeuille", "comptabilite"},
    "social": {"referentiel", "portefeuille"},
    "cloture": {"referentiel", "portefeuille", "comptabilite", "conformite"},
    "creation_entreprise": {"referentiel", "portefeuille"},
    "pilotage": {"referentiel", "portefeuille", "conformite", "obligations", "comptabilite"},
    "transverse": {"referentiel"},
}


def _modules(contexte: str) -> list[Path]:
    return sorted((CONTEXTES_DIR / contexte).rglob("*.py"))


def _imports(fichier: Path) -> set[str]:
    """Modules importés par un fichier, en chemin absolu `app.…`.

    Les imports relatifs sont résolus par rapport au paquet du fichier, sans quoi
    `from ..referentiel.service import …` passerait inaperçu.
    """
    arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
    paquet = fichier.relative_to(RACINE).with_suffix("").parts
    if paquet[-1] == "__init__":
        paquet = paquet[:-1]

    trouves: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            trouves.update(alias.name for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.level == 0:
                if noeud.module:
                    trouves.add(noeud.module)
            else:
                base = paquet[: len(paquet) - noeud.level] if noeud.level <= len(paquet) else ()
                cible = ".".join([*base, noeud.module]) if noeud.module else ".".join(base)
                trouves.add(cible)
    return trouves


def _contexte_cible(module: str) -> str | None:
    prefixe = "app.contexts."
    if not module.startswith(prefixe):
        return None
    return module[len(prefixe) :].split(".")[0]


class TestStructure:
    def test_les_onze_contextes_existent(self):
        presents = {
            d.name for d in CONTEXTES_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")
        }
        manquants = set(CONTEXTES) - presents
        assert not manquants, f"contextes absents de l'arborescence : {sorted(manquants)}"

    def test_aucun_paquet_hors_nomenclature(self):
        """Un dossier non prévu au schéma est soit un contexte oublié dans la
        documentation, soit un fourre-tout qui va tout absorber."""
        presents = {
            d.name for d in CONTEXTES_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")
        }
        inconnus = presents - set(CONTEXTES)
        assert not inconnus, (
            f"contextes non documentés : {sorted(inconnus)}. "
            "Les déclarer dans Docs/architecture/01-contextes-bornes.md et ici."
        )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_chaque_contexte_documente_sa_portee(self, contexte: str):
        init = CONTEXTES_DIR / contexte / "__init__.py"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert docstring and CONTEXTES[contexte] in docstring.split("\n")[0], (
            f"{contexte}/__init__.py doit ouvrir sur « Contexte {CONTEXTES[contexte]} · … »"
        )


class TestDependances:
    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_aucune_dependance_interdite(self, contexte: str):
        autorises = ARETES_AUTORISEES[contexte]
        for fichier in _modules(contexte):
            for module in _imports(fichier):
                cible = _contexte_cible(module)
                if cible is None or cible == contexte:
                    continue
                assert cible in autorises, (
                    f"{fichier.relative_to(RACINE)} importe le contexte « {cible} », "
                    f"non autorisé depuis « {contexte} ». "
                    f"Arêtes permises : {sorted(autorises) or 'aucune'}. "
                    "Si la dépendance est légitime, la justifier dans "
                    "Docs/architecture/01-contextes-bornes.md avant de l'ajouter ici."
                )

    def test_le_referentiel_ne_depend_d_aucun_contexte(self):
        """A est le pivot : il alimente tout le monde et ne connaît personne.

        Cette propriété est ce qui rend le noyau normatif réutilisable et testable
        isolément. Si elle se casse, la mise à jour annuelle de la loi de finances
        devient un chantier transverse.
        """
        for fichier in _modules("referentiel"):
            for module in _imports(fichier):
                assert _contexte_cible(module) in (None, "referentiel"), (
                    f"{fichier.relative_to(RACINE)} : le référentiel ne doit dépendre "
                    f"d'aucun autre contexte, or il importe « {module} »"
                )

    def test_aucun_contexte_n_importe_le_point_d_entree(self):
        """Les contextes sont montés par `app.main`, jamais l'inverse."""
        for contexte in CONTEXTES:
            for fichier in _modules(contexte):
                for module in _imports(fichier):
                    assert module != "app.main" and not module.startswith("app.main."), (
                        f"{fichier.relative_to(RACINE)} importe app.main : inversion de contrôle"
                    )

    def test_le_graphe_est_acyclique(self):
        """Un cycle entre contextes signifie que la frontière est mal placée."""
        vus: dict[str, int] = {}

        def visiter(contexte: str, chemin: list[str]) -> None:
            if vus.get(contexte) == 1:
                boucle = " → ".join([*chemin, contexte])
                raise AssertionError(f"cycle de dépendances : {boucle}")
            if vus.get(contexte) == 2:
                return
            vus[contexte] = 1
            for suivant in sorted(ARETES_AUTORISEES[contexte]):
                visiter(suivant, [*chemin, contexte])
            vus[contexte] = 2

        for contexte in sorted(CONTEXTES):
            visiter(contexte, [])
