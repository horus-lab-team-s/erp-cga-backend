"""Garde-fous d'architecture.

Deux disciplines cohabitent, et l'une comme l'autre est vérifiée à chaque exécution
des tests.

**Clean Architecture — le sens des dépendances.** Quatre cercles, du plus interne au
plus externe. Une flèche ne va jamais de l'intérieur vers l'extérieur.

    ┌───────────────────────────────────────────────────────────┐
    │ 4 · app/infrastructure/   Frameworks & Drivers            │
    │   ┌───────────────────────────────────────────────────┐   │
    │   │ 3 · <contexte>/adaptateurs/   Interface Adapters  │   │
    │   │   ┌───────────────────────────────────────────┐   │   │
    │   │   │ 2 · <contexte>/application/   Use Cases   │   │   │
    │   │   │   ┌───────────────────────────────────┐   │   │   │
    │   │   │   │ 1 · <contexte>/domaine/  Entities │   │   │   │
    │   │   │   └───────────────────────────────────┘   │   │   │
    │   │   └───────────────────────────────────────────┘   │   │
    │   └───────────────────────────────────────────────────┘   │
    └───────────────────────────────────────────────────────────┘

**Contextes bornés — les frontières métier.** Douze contextes autonomes. On n'entre
chez un autre que par une surface publique déclarée, jamais par ses entrailles.

Les couches vivent *à l'intérieur* de chaque contexte : cela donne douze modules
métier complets, plutôt que quatre grands sacs techniques où le référentiel fiscal
et la comptabilité se mélangeraient.

Références : Docs/architecture/01-contextes-bornes.md et 10-flux-fonctionnels.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
CONTEXTES_DIR = RACINE / "app" / "contextes"

# ── Les douze contextes bornés ────────────────────────────────────────────────
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
    "vitrine": "L",
}

# ── Les couches, du plus interne au plus externe ──────────────────────────────
#: Une couche n'importe que son propre rang ou un rang inférieur.
RANG_COUCHE: dict[str, int] = {"domaine": 1, "application": 2, "adaptateurs": 3}

#: Surfaces publiques d'un contexte, à sa racine.
#: `contrats` n'expose que des entités — rang 1, importable depuis un domaine.
#: `api` expose aussi les cas d'usage — rang 2, interdit à un domaine.
RANG_SURFACE: dict[str, int] = {"contrats": 1, "api": 2}


def autorises_pour(contexte: str) -> set[str]:
    """Cibles métier permises depuis un contexte : ses arêtes, plus le socle.

    L'expansion du socle ne vaut que pour les contextes métier : appliquée au socle
    lui-même, elle créerait un cycle referentiel ↔ transverse.
    """
    if contexte in SOCLE:
        return set(ARETES_AUTORISEES[contexte])
    return ARETES_AUTORISEES[contexte] | (SOCLE - {contexte})


# ── Le graphe métier, établi flux par flux dans 10-flux-fonctionnels.md ───────
SOCLE: frozenset[str] = frozenset({"referentiel", "transverse"})

ARETES_AUTORISEES: dict[str, set[str]] = {
    "referentiel": set(),
    "transverse": {"referentiel"},
    "portefeuille": set(),
    "conformite": set(),
    "collecte": {"portefeuille", "conformite"},
    "comptabilite": {"portefeuille", "conformite", "collecte"},
    "obligations": {"portefeuille", "comptabilite", "conformite", "collecte"},
    "social": {"portefeuille"},
    "cloture": {"portefeuille", "comptabilite", "conformite", "collecte", "obligations"},
    "creation_entreprise": {"portefeuille", "conformite"},
    "pilotage": {
        "portefeuille",
        "conformite",
        "collecte",
        "comptabilite",
        "obligations",
        "cloture",
        "creation_entreprise",
        "social",
    },
    # La vitrine ne lit aucun contexte métier, et c'est structurant : du contenu
    # éditorial qui aurait besoin d'un paramètre légal ne serait plus du contenu,
    # ce serait un calcul — et il appartiendrait au contexte qui le porte. Le jour
    # où l'on voudrait afficher un barème sur le site, la bonne réponse sera une
    # route du Référentiel appelée par le site, pas une arête ajoutée ici.
    "vitrine": set(),
}


# ── Lecture des imports ───────────────────────────────────────────────────────


def _fichiers(contexte: str) -> list[Path]:
    return sorted((CONTEXTES_DIR / contexte).rglob("*.py"))


def _imports(fichier: Path) -> set[str]:
    """Modules importés, en chemin absolu `app.…`.

    Les imports relatifs sont résolus par rapport au paquet du fichier, sans quoi
    `from ..domaine.entites import …` passerait inaperçu.
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
                trouves.add(".".join([*base, noeud.module]) if noeud.module else ".".join(base))
    return trouves


def _decomposer(module: str) -> tuple[str, str] | None:
    """`app.contextes.<ctx>.<partie>…` → (ctx, partie). None si hors contextes."""
    prefixe = "app.contextes."
    if not module.startswith(prefixe):
        return None
    morceaux = module[len(prefixe) :].split(".")
    return (morceaux[0], morceaux[1] if len(morceaux) > 1 else "")


def _couche(fichier: Path) -> str | None:
    """Couche du fichier, ou None s'il s'agit d'une surface publique."""
    parties = fichier.relative_to(CONTEXTES_DIR).parts
    return parties[1] if len(parties) > 1 and parties[1] in RANG_COUCHE else None


# ── Structure ─────────────────────────────────────────────────────────────────


class TestStructure:
    def test_les_onze_contextes_existent(self):
        presents = {
            d.name for d in CONTEXTES_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")
        }
        manquants = set(CONTEXTES) - presents
        assert not manquants, f"contextes absents de l'arborescence : {sorted(manquants)}"

    def test_aucun_paquet_hors_nomenclature(self):
        """Un dossier non prévu est soit un contexte oublié dans la documentation, soit
        un fourre-tout qui finira par tout absorber."""
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

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_aucun_module_hors_des_couches(self, contexte: str):
        """À la racine d'un contexte, seules les surfaces publiques sont admises.

        Un module métier posé là échapperait au contrôle de couche. C'est par là que la
        Clean Architecture se défait : un fichier « utils.py » à la fois.
        """
        admis = set(RANG_SURFACE) | {"__init__"}
        for fichier in (CONTEXTES_DIR / contexte).glob("*.py"):
            assert fichier.stem in admis, (
                f"{fichier.relative_to(RACINE)} est à la racine du contexte. Le placer "
                f"dans domaine/, application/ ou adaptateurs/ — seuls {sorted(admis)} "
                "sont admis à ce niveau."
            )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_les_dossiers_presents_sont_des_couches(self, contexte: str):
        for dossier in (CONTEXTES_DIR / contexte).iterdir():
            if not dossier.is_dir() or dossier.name.startswith("_"):
                continue
            assert dossier.name in RANG_COUCHE, (
                f"{dossier.relative_to(RACINE)} n'est pas une couche. "
                f"Couches admises : {sorted(RANG_COUCHE)}."
            )


# ── Clean Architecture : le sens des dépendances ──────────────────────────────


class TestCouches:
    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_aucune_dependance_vers_un_cercle_plus_externe(self, contexte: str):
        """Le cœur ne connaît pas la périphérie.

        Une entité important un cas d'usage, ou un cas d'usage important un routeur
        HTTP, rendraient la règle métier inséparable du framework — précisément ce que
        la Clean Architecture évite.
        """
        for fichier in _fichiers(contexte):
            couche = _couche(fichier)
            if couche is None:
                continue
            rang_source = RANG_COUCHE[couche]
            for module in _imports(fichier):
                decompose = _decomposer(module)
                if decompose is None:
                    continue
                rang_cible = RANG_COUCHE.get(decompose[1]) or RANG_SURFACE.get(decompose[1])
                if rang_cible is None:
                    continue
                assert rang_cible <= rang_source, (
                    f"{fichier.relative_to(RACINE)} — couche « {couche} » (cercle "
                    f"{rang_source}) importe « {module} » (cercle {rang_cible}). "
                    "Une dépendance ne va jamais de l'intérieur vers l'extérieur : "
                    "inverser par une interface, ou déplacer le code."
                )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_le_domaine_ne_connait_pas_l_infrastructure(self, contexte: str):
        """Le cercle 1 ignore jusqu'à l'existence du cercle 4.

        `app.partage` reste admis : fonctions pures de formatage, sans entrée-sortie ni
        configuration.
        """
        for fichier in _fichiers(contexte):
            if _couche(fichier) != "domaine":
                continue
            for module in _imports(fichier):
                assert not module.startswith("app.infrastructure"), (
                    f"{fichier.relative_to(RACINE)} est une entité et importe "
                    f"« {module} ». La configuration se passe en argument, elle ne se "
                    "lit pas depuis le domaine."
                )

    def test_les_paquets_techniques_ignorent_le_metier(self):
        """`partage` et `infrastructure` sont sous les contextes, pas au-dessus."""
        for paquet in ("app/partage", "app/infrastructure"):
            for fichier in sorted((RACINE / paquet).rglob("*.py")):
                for module in _imports(fichier):
                    assert not module.startswith("app.contextes"), (
                        f"{fichier.relative_to(RACINE)} importe « {module} ». Le cercle "
                        "externe est appelé par le métier, il ne l'appelle pas."
                    )


# ── Contextes bornés : les frontières métier ──────────────────────────────────


class TestDependancesEntreContextes:
    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_aucune_dependance_metier_interdite(self, contexte: str):
        autorises = autorises_pour(contexte)
        for fichier in _fichiers(contexte):
            for module in _imports(fichier):
                decompose = _decomposer(module)
                if decompose is None or decompose[0] == contexte:
                    continue
                assert decompose[0] in autorises, (
                    f"{fichier.relative_to(RACINE)} importe le contexte "
                    f"« {decompose[0]} », non autorisé depuis « {contexte} ». "
                    f"Arêtes permises : {sorted(autorises) or 'aucune'}. Justifier dans "
                    "Docs/architecture/10-flux-fonctionnels.md avant de l'ajouter ici."
                )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_on_n_entre_chez_l_autre_que_par_sa_surface_publique(self, contexte: str):
        """`contrats` ou `api`, jamais un module interne.

        C'est cette règle qui permet de réorganiser l'intérieur d'un contexte — changer
        de persistance, découper une couche — sans casser les dix autres.
        """
        for fichier in _fichiers(contexte):
            for module in _imports(fichier):
                decompose = _decomposer(module)
                if decompose is None or decompose[0] == contexte:
                    continue
                cible, partie = decompose
                assert partie in RANG_SURFACE, (
                    f"{fichier.relative_to(RACINE)} importe « {module} », un module "
                    f"interne de « {cible} ». Passer par app.contextes.{cible}.contrats "
                    "pour une entité, ou .api pour un cas d'usage."
                )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_un_domaine_n_emprunte_que_des_contrats(self, contexte: str):
        """Importer l'`api` d'un autre contexte depuis une entité tirerait sa couche
        application : le cercle 1 dépendrait du cercle 2, par la bande."""
        for fichier in _fichiers(contexte):
            if _couche(fichier) != "domaine":
                continue
            for module in _imports(fichier):
                decompose = _decomposer(module)
                if decompose is None or decompose[0] == contexte:
                    continue
                assert decompose[1] == "contrats", (
                    f"{fichier.relative_to(RACINE)} est une entité et importe "
                    f"« {module} ». Une entité n'emprunte que les contrats d'un autre "
                    f"contexte : app.contextes.{decompose[0]}.contrats."
                )

    def test_aucun_contexte_n_importe_le_point_d_entree(self):
        """Les contextes sont montés par `app.main`, jamais l'inverse."""
        for contexte in CONTEXTES:
            for fichier in _fichiers(contexte):
                for module in _imports(fichier):
                    assert module != "app.main" and not module.startswith("app.main."), (
                        f"{fichier.relative_to(RACINE)} importe app.main : inversion de contrôle"
                    )

    def test_le_graphe_est_acyclique(self):
        """Deux contextes qui s'appellent mutuellement n'en font qu'un, mal découpé. Le
        remède est un événement ou un déplacement de responsabilité, jamais une arête."""
        vus: dict[str, int] = {}

        def visiter(contexte: str, chemin: list[str]) -> None:
            if vus.get(contexte) == 1:
                raise AssertionError("cycle de dépendances : " + " → ".join([*chemin, contexte]))
            if vus.get(contexte) == 2:
                return
            vus[contexte] = 1
            for suivant in sorted(autorises_pour(contexte)):
                visiter(suivant, [*chemin, contexte])
            vus[contexte] = 2

        for contexte in sorted(CONTEXTES):
            visiter(contexte, [])

    def test_le_socle_ne_depend_d_aucun_contexte_metier(self):
        for contexte in sorted(SOCLE):
            interdites = ARETES_AUTORISEES[contexte] - SOCLE
            assert not interdites, (
                f"« {contexte} » appartient au socle, lisible par tous, et ne peut "
                f"dépendre d'aucun métier, or il déclare : {sorted(interdites)}"
            )

    def test_le_pilotage_n_est_lu_par_personne(self):
        """J agrège et n'est lu par personne. Si un contexte venait à le lire, c'est
        qu'un indicateur y aurait pris une valeur métier, à redescendre chez son
        responsable."""
        lecteurs = [c for c, cibles in ARETES_AUTORISEES.items() if "pilotage" in cibles]
        assert not lecteurs, f"le pilotage est lu par : {sorted(lecteurs)}"
