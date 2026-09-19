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

from app.registre import (
    ARETES_AUTORISEES,
    SERVICES,
    SOCLE,
    autorises_pour,
)

RACINE = Path(__file__).resolve().parents[1]
CONTEXTES_DIR = RACINE / "app" / "contextes"

# ── Les quatorze contextes bornés ─────────────────────────────────────────────
#: ⚠️ **Projection du registre, et non seconde déclaration.**
#:
#: Cette table vivait ici, avec le graphe des dépendances. Elle y était juste et
#: vérifiée, et **invisible à l'exécution** : aucune route ne pouvait dire quels
#: services existent, ni ce qui tombe avec l'un d'eux, et le tableau « ce qui tourne
#: aujourd'hui » du document de conception était de la prose recopiée à la main.
#:
#: Elle est désormais lue depuis `app/registre/`. Le même graphe interdit une arête
#: à la relecture et répond à la sonde : deux vérités recopiées finissent toujours
#: par diverger, et celle qui dérive est celle que personne ne relit.
#:
#: ⚠️ Le test ne perd rien à cette bascule. Il compare toujours la déclaration au
#: **système de fichiers** et aux **imports réels** ; c'est cette confrontation qui
#: protège, pas l'endroit où la déclaration est écrite.
CONTEXTES: dict[str, str] = {s.nom: s.lettre for s in SERVICES}

# ── Les couches, du plus interne au plus externe ──────────────────────────────
#: Une couche n'importe que son propre rang ou un rang inférieur.
RANG_COUCHE: dict[str, int] = {"domaine": 1, "application": 2, "adaptateurs": 3}

#: Surfaces publiques d'un contexte, à sa racine.
#: `contrats` n'expose que des entités — rang 1, importable depuis un domaine.
#: `api` expose aussi les cas d'usage — rang 2, interdit à un domaine.
RANG_SURFACE: dict[str, int] = {"contrats": 1, "api": 2}






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
    def test_les_quatorze_contextes_existent(self):
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


# ── Le noyau d'évaluation ─────────────────────────────────────────────────────


MOTEUR_DIR = RACINE / "app" / "moteur"
ORCHESTRATION_DIR = RACINE / "app" / "orchestration"
REGISTRE_DIR = RACINE / "app" / "registre"


class TestNoyauMoteur:
    """`app/moteur/` porte le mécanisme d'évaluation, pas le métier.

    Le même noyau sert à contrôler une pièce, à chiffrer une prestation, à évaluer une
    charge et à surveiller le fonctionnement interne. Il ne tient cette promesse que
    tant qu'il ignore ce qu'est une facture, un impôt ou une monnaie : la première
    importation d'un contexte métier le rendrait inutilisable par les trois autres
    usages, et personne ne s'en apercevrait avant d'essayer.

    Ces garde-fous sont posés avant la généralisation, et non après, parce qu'après il
    n'y a plus rien à protéger.
    """

    def test_le_paquet_existe_et_declare_sa_portee(self):
        init = MOTEUR_DIR / "__init__.py"
        assert init.exists(), "app/moteur/__init__.py doit exister"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert docstring, "app/moteur/__init__.py doit documenter la portée du noyau"

    def test_le_noyau_n_importe_aucun_contexte_metier(self):
        for fichier in sorted(MOTEUR_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert _decomposer(module) is None, (
                    f"{fichier.relative_to(RACINE)} importe « {module} ». Le noyau "
                    "d'évaluation ne connaît aucun contexte métier : ce dont il a besoin "
                    "lui est passé en argument, sous forme de faits et de règles."
                )

    def test_le_noyau_ne_depend_d_aucune_infrastructure(self):
        """Le noyau est du calcul pur. Une base, une horloge ou un réseau le rendraient
        impossible à tester sans montage, et impossible à exécuter par lots."""
        for fichier in sorted(MOTEUR_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert not module.startswith("app.infrastructure"), (
                    f"{fichier.relative_to(RACINE)} importe « {module} ». Le noyau ne "
                    "lit ni base ni horloge : la date lui est donnée."
                )

    def test_le_noyau_n_execute_aucun_code_arbitraire(self):
        """Un prédicat est une donnée, pas un programme. `eval`, `exec` et l'import
        dynamique ouvriraient au référentiel la possibilité d'exécuter n'importe quoi,
        alors qu'il est édité par des fiscalistes et stocké en base."""
        interdits = {"eval", "exec", "compile", "__import__"}
        for fichier in sorted(MOTEUR_DIR.rglob("*.py")):
            arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name):
                    assert noeud.func.id not in interdits, (
                        f"{fichier.relative_to(RACINE)} appelle « {noeud.func.id} ». "
                        "Un cas qui résiste à l'expression déclarative demande un "
                        "opérateur de plus dans le noyau, jamais une échappatoire."
                    )


# ── Le socle d'orchestration ──────────────────────────────────────────────────


class TestSocleOrchestration:
    """`app/orchestration/` porte la coordination, pas le métier.

    Même discipline que `app/moteur/`, et pour la même raison. Une saga qui
    saurait ce qu'est un tenant ne servirait plus à orchestrer un rapprochement
    bancaire ou une clôture d'exercice, et personne ne s'en apercevrait avant
    d'essayer.

    ⚠️ Ces garde-fous sont posés **avant** la deuxième saga, et non après. Après,
    il n'y a plus rien à protéger : le couplage est déjà écrit.
    """

    def test_le_paquet_existe_et_declare_sa_portee(self):
        init = ORCHESTRATION_DIR / "__init__.py"
        assert init.exists(), "app/orchestration/__init__.py doit exister"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert docstring, "app/orchestration/__init__.py doit documenter sa portée"

    def test_il_n_importe_aucun_contexte_metier(self):
        for fichier in sorted(ORCHESTRATION_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert _decomposer(module) is None, (
                    f"{fichier.relative_to(RACINE)} importe « {module} ». Le socle "
                    "d'orchestration ne connaît aucun contexte métier : les étapes "
                    "et leurs compensations lui sont passées en argument."
                )

    def test_il_ne_depend_d_aucune_infrastructure(self):
        """La saga est du calcul pur. Une base ou une horloge la rendraient
        impossible à éprouver sans montage, et impossible à rejouer à une date
        choisie."""
        for fichier in sorted(ORCHESTRATION_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert not module.startswith("app.infrastructure"), (
                    f"{fichier.relative_to(RACINE)} importe « {module} ». Le socle "
                    "ne lit ni base ni horloge : l'instant lui est donné, et la "
                    "persistance de l'avancement appartient à l'appelant."
                )

    def test_il_n_importe_pas_le_moteur_et_reciproquement(self):
        """Deux mécanismes, deux sujets : l'un évalue des règles, l'autre
        enchaîne des effets. Les lier ferait qu'un domaine qui n'a besoin que
        d'évaluer traînerait la machinerie de compensation, et l'inverse."""
        for fichier in sorted(ORCHESTRATION_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert not module.startswith("app.moteur"), (
                    f"{fichier.relative_to(RACINE)} importe « {module} »."
                )
        for fichier in sorted(MOTEUR_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert not module.startswith("app.orchestration"), (
                    f"{fichier.relative_to(RACINE)} importe « {module} »."
                )


# ── Le registre des services ──────────────────────────────────────────────────


class TestRegistreDesServices:
    """`app/registre/` décrit les services. Il n'en est aucun.

    ⚠️ Ces garde-fous existent parce que le registre est **lu par tout le monde** :
    le test d'architecture, la sonde de santé, une route. Un registre qui
    importerait un contexte métier créerait un cycle à l'exécution — le contexte
    serait décrit par un registre qui a besoin de lui pour se charger — et le jour
    où l'on voudrait le consulter avant le démarrage, il ne se chargerait plus.
    """

    def test_le_paquet_existe_et_declare_sa_portee(self):
        init = REGISTRE_DIR / "__init__.py"
        assert init.exists(), "app/registre/__init__.py doit exister"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert docstring, "app/registre/__init__.py doit documenter sa portée"

    def test_il_n_importe_aucun_contexte_metier(self):
        """Il ne connaît que des **noms**, jamais les objets qu'ils désignent."""
        for fichier in sorted(REGISTRE_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert _decomposer(module) is None, (
                    f"{fichier.relative_to(RACINE)} importe « {module} ». Le registre "
                    "décrit les services et n'en est aucun : il ne connaît que des noms."
                )

    def test_il_ne_depend_d_aucune_infrastructure(self):
        """Consulter le registre ne doit demander ni base, ni horloge, ni cadre web.

        C'est ce qui permet de le lire depuis un outil en ligne de commande, depuis
        un test unitaire, et depuis une sonde qui doit répondre **même quand la base
        est tombée** — c'est-à-dire précisément quand on la consulte.
        """
        for fichier in sorted(REGISTRE_DIR.rglob("*.py")):
            for module in _imports(fichier):
                assert not module.startswith(("app.infrastructure", "fastapi")), (
                    f"{fichier.relative_to(RACINE)} importe « {module} »."
                )

    def test_chaque_service_declare_a_son_repertoire(self):
        """Une déclaration sans répertoire est une place réservée qui n'existe pas.

        Le cas inverse — un répertoire non déclaré — est déjà couvert par
        `TestContextes` : les deux ensemble ferment la boucle.
        """
        for service in SERVICES:
            assert (CONTEXTES_DIR / service.nom).is_dir(), (
                f"« {service.nom} » est déclaré au registre sans répertoire."
            )

    def test_aucune_lettre_n_est_employee_deux_fois(self):
        """Les documents désignent les contextes par leur lettre depuis l'origine.

        Deux services sur la même lettre rendraient toute la documentation
        ambiguë, et c'est le genre de collision qu'une relecture ne voit pas.
        """
        lettres = [s.lettre for s in SERVICES]
        assert len(set(lettres)) == len(lettres), f"lettres en double : {lettres}"

    def test_aucun_prefixe_http_n_est_partage(self):
        """⚠️ Deux services sous le même préfixe rendraient l'état de joignabilité faux.

        Et surtout : le jour où la passerelle routera vers des services séparés,
        c'est sur ce préfixe qu'elle routera. Un préfixe partagé serait alors une
        ambiguïté de routage, pas seulement d'affichage.
        """
        prefixes = [p for s in SERVICES for p in s.prefixes]
        assert len(set(prefixes)) == len(prefixes), f"préfixes en double : {prefixes}"

    def test_le_graphe_couvre_exactement_les_services_declares(self):
        """Une arête déclarée pour un service inexistant ne serait jamais vérifiée."""
        assert set(ARETES_AUTORISEES) == {s.nom for s in SERVICES}

    def test_le_graphe_ne_pointe_que_vers_des_services_declares(self):
        for nom, cibles in ARETES_AUTORISEES.items():
            inconnues = cibles - {s.nom for s in SERVICES}
            assert not inconnues, f"« {nom} » dépend de services inconnus : {inconnues}"

    def test_le_socle_ne_depend_d_aucun_metier(self):
        """Un socle qui lirait le métier créerait un cycle à l'exécution : le métier
        a besoin de savoir qui parle avant de répondre, et l'identité aurait besoin
        du métier pour le dire."""
        for nom in sorted(SOCLE):
            assert not (autorises_pour(nom) - SOCLE), (
                f"« {nom} » appartient au socle et dépend de métier."
            )


# ── La suite se garde elle-même ───────────────────────────────────────────────


class TestAucunTestNEstVide:
    """Un test dont le corps n'est qu'une docstring passe pour toujours en ne
    prouvant rien.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **C'est la pire forme de test**, pire qu'un test absent : il occupe la place
    de celui qui aurait attrapé la faute, il compte dans le total, et son nom
    affirme une propriété que rien ne vérifie.

    Le cas s'est présenté en écrivant la persistance du suivi de relance : deux
    fonctions avaient été rédigées avec leur docstring et sans leur corps, et la
    suite est passée au vert.

    ⚠️ Ce garde-fou **n'exige pas d'assertion**. Une vingtaine de cas de cette suite
    vérifient qu'un appel *ne lève pas* — un slug valide qui passe, une double
    déconnexion qui ne tombe pas — et l'absence d'exception y est l'assertion. Leur
    nom le dit. Exiger un `assert` les obligerait à écrire `assert True`, ce qui
    n'ajouterait rien et masquerait la distinction.

    Ce qui est interdit est le corps **vide**, où rien n'est même appelé.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_aucun_corps_de_test_ne_se_reduit_a_sa_docstring(self):
        vides = []
        for fichier in sorted((RACINE / "tests").glob("test_*.py")):
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for noeud in ast.walk(arbre):
                if not (
                    isinstance(noeud, ast.FunctionDef)
                    and noeud.name.startswith("test_")
                ):
                    continue
                corps = [
                    e
                    for e in noeud.body
                    if not (
                        isinstance(e, ast.Expr)
                        and isinstance(e.value, ast.Constant)
                        and isinstance(e.value.value, str)
                    )
                ]
                if not corps or all(isinstance(e, ast.Pass) for e in corps):
                    vides.append(f"{fichier.name}::{noeud.name}")

        assert not vides, (
            "ces tests n'ont qu'une docstring et passent en ne prouvant rien : "
            f"{vides}"
        )
