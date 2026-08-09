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

#: Contextes de socle, lisibles par tous sans arête explicite.
#:
#: A · Référentiel est le PIVOT normatif : il alimente tout le monde et ne connaît
#: personne. K · Transverse fournit des services techniques — identité, journal d'audit,
#: GED, notifications — que tout contexte métier consomme ; exiger une arête depuis
#: chacun des dix autres n'apprendrait rien et alourdirait le graphe pour rien.
SOCLE: frozenset[str] = frozenset({"referentiel", "transverse"})

#: Dépendances métier autorisées, en plus du socle et de `core` / `shared`.
#:
#: Chaque arête correspond à un flux identifié dans les maquettes. Toute arête ajoutée
#: ici doit être justifiée dans Docs/architecture/10-flux-fonctionnels.md.
ARETES_AUTORISEES: dict[str, set[str]] = {
    # Le pivot normatif ne dépend de rien, pas même du socle technique.
    "referentiel": set(),
    # Services techniques : ils lisent le référentiel, jamais le métier.
    "transverse": {"referentiel"},
    "portefeuille": set(),
    # Le contrôle d'une facture est une fonction pure de la facture et du droit.
    "conformite": set(),
    # Collecte possède le cycle de vie de la pièce et déclenche son contrôle.
    "collecte": {"portefeuille", "conformite"},
    # L'écriture hérite de l'attribut fiscal du rapport et porte l'id de la pièce.
    # Le rapprochement bancaire confronte les relevés importés par Collecte.
    "comptabilite": {"portefeuille", "conformite", "collecte"},
    # La déclaration de TVA rejette la TVA constatée non déductible (ligne L24 de la
    # maquette) et affiche la complétude du dossier.
    "obligations": {"portefeuille", "comptabilite", "conformite", "collecte"},
    "social": {"portefeuille"},
    # Le tableau de passage reçoit les réintégrations issues des constats ; la DSF est
    # elle-même une obligation.
    "cloture": {"portefeuille", "comptabilite", "conformite", "collecte", "obligations"},
    # Checklist de pièces par forme juridique : même moteur de règles, autre jeu.
    "creation_entreprise": {"portefeuille", "conformite"},
    # Pilotage lit tout le monde et n'est lu par personne : c'est un puits.
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


def autorises_pour(contexte: str) -> set[str]:
    """Cibles permises depuis un contexte : ses arêtes déclarées, plus le socle.

    L'expansion du socle ne vaut QUE pour les contextes métier. Appliquée aux membres du
    socle eux-mêmes, elle créerait un cycle referentiel ↔ transverse.
    """
    if contexte in SOCLE:
        return set(ARETES_AUTORISEES[contexte])
    return ARETES_AUTORISEES[contexte] | (SOCLE - {contexte})


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
        autorises = autorises_pour(contexte)
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
                    "Docs/architecture/10-flux-fonctionnels.md avant de l'ajouter ici."
                )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_les_contextes_ne_dialoguent_que_par_leur_surface_publique(self, contexte: str):
        """Un contexte n'importe que `<autre>.api`, jamais ses entrailles.

        C'est cette règle qui permet de réorganiser l'intérieur d'un contexte — renommer
        un service, découper un module, changer de persistance — sans casser les dix
        autres. Sans elle, « monolithe modulaire » n'est qu'une intention.
        """
        for fichier in _modules(contexte):
            for module in _imports(fichier):
                cible = _contexte_cible(module)
                if cible is None or cible == contexte:
                    continue
                chemin = module.removeprefix(f"app.contexts.{cible}")
                assert chemin in ("", ".api"), (
                    f"{fichier.relative_to(RACINE)} importe « {module} », un module interne "
                    f"de « {cible} ». Passer par app.contexts.{cible}.api, et l'y exporter "
                    "si le symbole doit devenir public."
                )

    @pytest.mark.parametrize("contexte", sorted(CONTEXTES))
    def test_tout_contexte_implemente_expose_une_surface_publique(self, contexte: str):
        """Un contexte qui porte du code sans `api.py` n'a pas de frontière : les
        suivants iront chercher ses modules internes, et la frontière n'existera plus."""
        dossier = CONTEXTES_DIR / contexte
        modules_metier = [
            f for f in dossier.glob("*.py") if f.name not in ("__init__.py", "api.py", "routes.py")
        ]
        if not modules_metier:
            return  # squelette : rien à exposer encore
        assert (dossier / "api.py").exists(), (
            f"« {contexte} » porte du code ({len(modules_metier)} modules) mais n'expose pas "
            "de api.py. Déclarer sa surface publique avant qu'un autre contexte n'en dépende."
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
        """Un cycle entre contextes signifie que la frontière est mal placée.

        Deux contextes qui s'appellent mutuellement n'en font qu'un, mal découpé : le
        remède est un événement ou un déplacement de responsabilité, jamais une arête
        de plus.
        """
        vus: dict[str, int] = {}

        def visiter(contexte: str, chemin: list[str]) -> None:
            if vus.get(contexte) == 1:
                boucle = " → ".join([*chemin, contexte])
                raise AssertionError(f"cycle de dépendances : {boucle}")
            if vus.get(contexte) == 2:
                return
            vus[contexte] = 1
            for suivant in sorted(autorises_pour(contexte)):
                visiter(suivant, [*chemin, contexte])
            vus[contexte] = 2

        for contexte in sorted(CONTEXTES):
            visiter(contexte, [])

    def test_le_socle_ne_depend_d_aucun_contexte_metier(self):
        """Le socle est lisible par tous : il ne doit donc dépendre d'aucun métier.

        Sinon toute évolution métier remonterait jusqu'à lui, et comme tout le monde le
        lit, le graphe deviendrait un cycle géant.
        """
        for contexte in sorted(SOCLE):
            interdites = ARETES_AUTORISEES[contexte] - SOCLE
            assert not interdites, (
                f"« {contexte} » appartient au socle et ne peut dépendre d'aucun contexte "
                f"métier, or il déclare : {sorted(interdites)}"
            )

    def test_le_pilotage_n_est_lu_par_personne(self):
        """J · Pilotage est un puits : il agrège, il ne sert personne en amont.

        Si un contexte métier venait à lire le pilotage, c'est qu'un indicateur y aurait
        pris une valeur métier — il faudrait alors le redescendre dans le contexte qui
        en est responsable.
        """
        lecteurs = [c for c, cibles in ARETES_AUTORISEES.items() if "pilotage" in cibles]
        assert not lecteurs, f"le pilotage est lu par : {sorted(lecteurs)}"
