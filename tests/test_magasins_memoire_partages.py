"""Un seul magasin mémoire par sorte de données, pour toute l'application.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le pas 28 avait posé ce garde pour la seule Souscription. Au pas 52, un écran neuf,
la veille des seuils, a affiché zéro chiffre d'affaires pour un dossier dont la
comptabilité voyait 45 millions. En persistance mémoire, chaque contexte construisait
son propre dépôt : les écritures en quatre endroits, les entreprises en cinq, avec une
seule construction mémorisée par sorte, parfois deux mémorisées séparément.

Conséquences mesurées : une écriture saisie invisible des revues ; un régime inscrit
au portefeuille invisible des obligations ; un exercice fermé par la clôture écrit
dans un magasin jeté aussitôt ; la comptabilité vérifiant qu'un exercice est clos
contre un portefeuille neuf, alors que sa propre documentation disait que « deux
registres d'exercices divergeraient ».

⚠️ **Le mode mémoire est celui que l'on montre au cabinet**, et il mentait sans
erreur. En base PostgreSQL, la base unique masquait tout.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
from collections import defaultdict

from app.infrastructure.config import RACINE_DEPOT

CONTEXTES = RACINE_DEPOT / "Backend_erp_cga" / "app" / "contextes"

#: ⚠️ Figés, donc sans état à partager : le catalogue des types d'obligation est
#: écrit dans le code. Le construire deux fois ne fait diverger personne.
SANS_ETAT = {"DepotTypesObligationMemoire"}


def _constructions(source: str) -> list[tuple[str, int, str | None, bool]]:
    """(classe, ligne, fonction englobante, mémoïsée) pour chaque construction."""
    arbre = ast.parse(source)
    parents = {enfant: noeud for noeud in ast.walk(arbre) for enfant in ast.iter_child_nodes(noeud)}
    trouvees = []
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue
        appel = noeud.func
        if isinstance(appel, ast.Attribute) and isinstance(appel.value, ast.Name):
            classe = appel.value.id if appel.attr == "avec_demonstration" else None
        else:
            classe = getattr(appel, "id", None)
        if not classe or not (classe.startswith("Depot") and classe.endswith("Memoire")):
            continue
        englobant, fonction = noeud, None
        while englobant in parents:
            englobant = parents[englobant]
            if isinstance(englobant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                fonction = englobant
                break
        if isinstance(fonction, ast.ClassDef):
            continue  # la classe qui se construit elle-même, dans son propre module
        memoisee = bool(
            fonction and any("cache" in ast.unparse(d) for d in fonction.decorator_list)
        )
        trouvees.append((classe, noeud.lineno, fonction.name if fonction else None, memoisee))
    return trouvees


def test_chaque_sorte_n_a_qu_une_fabrique_dans_toute_l_application():
    par_classe: dict[str, list[str]] = defaultdict(list)
    fichiers = sorted(CONTEXTES.rglob("*.py"))
    # ⚠️ Le balayage doit prouver qu'il balaie.
    assert len(fichiers) > 200, len(fichiers)

    for fichier in fichiers:
        # Le jeu de démonstration et les dépôts eux-mêmes assemblent leurs données.
        if fichier.name in {"donnees_demo.py"} or fichier.name.startswith("depot"):
            continue
        for classe, ligne, fonction, memoisee in _constructions(fichier.read_text()):
            if classe in SANS_ETAT:
                continue
            par_classe[classe].append(
                f"{fichier.relative_to(CONTEXTES)}:{ligne} {fonction or '<module>'}"
                f"{' (mémoïsée)' if memoisee else ''}"
            )

    assert {"DepotEcrituresMemoire", "DepotEntreprisesMemoire", "DepotPiecesMemoire"} <= set(
        par_classe
    ), f"le balayage ne voit plus les dépôts qu'il doit garder : {sorted(par_classe)}"

    doublons = {classe: sites for classe, sites in par_classe.items() if len(sites) > 1}
    assert not doublons, (
        "ces dépôts mémoire sont construits en plusieurs endroits : chaque endroit tient "
        "ses propres données, et une écriture faite dans l'un est invisible des autres. "
        "Exposer une fabrique mémoïsée dans `magasins_memoire.py` du contexte propriétaire, "
        f"et la lire par son api : {doublons}"
    )

    # ⚠️ **Ce qui rend un magasin neuf à chaque appel** : une construction dans une
    # fonction ordinaire. Deux formes ne le font pas, et ne sont pas signalées :
    #
    #   au niveau du module       un singleton, construit une fois au chargement
    #   dans un `__init__`        un porteur, comme le `Comptoir` de la Souscription,
    #                             lui-même mémoïsé ; l'hypothèse est écrite ici plutôt
    #                             que vérifiée, et c'est la limite de ce contrôle
    rejouees = {
        classe: sites[0]
        for classe, sites in par_classe.items()
        if "(mémoïsée)" not in sites[0]
        and not sites[0].endswith((" <module>", " __init__"))
    }
    assert not rejouees, (
        "cette fabrique rend un magasin neuf à chaque appel : rien de ce qu'on y écrit "
        f"ne dure, et deux lectures successives ne voient pas la même chose : {rejouees}"
    )


def test_le_balayage_verrait_le_defaut_s_il_revenait():
    fautif = (
        "def depot_ecritures(entreprise):\n"
        "    return DepotEcrituresMemoire.avec_demonstration(entreprise)\n"
    )
    assert _constructions(fautif) == [("DepotEcrituresMemoire", 2, "depot_ecritures", False)]


def test_ce_qu_ecrit_la_comptabilite_se_lit_dans_les_obligations():
    """⚠️ **Le comportement, et pas seulement la forme du code.**

    Une écriture posée dans le magasin de la comptabilité doit se retrouver dans celui
    que lisent les obligations : c'est le défaut qui a fait afficher zéro à la veille
    des seuils.
    """
    from app.contextes.comptabilite.api import (
        ecritures_en_memoire,
        vider_les_ecritures_en_memoire,
    )
    from app.contextes.obligations.adaptateurs.entrant import routes_http as obligations
    from app.partage.locataire import etabli

    vider_les_ecritures_en_memoire()
    try:
        with etabli("CGA-BRCG"):
            ecrit = ecritures_en_memoire("P019876543210K")
            lu = obligations.depot_ecritures("P019876543210K")
            assert lu is ecrit, "les obligations lisent un autre magasin que la comptabilité"
    finally:
        vider_les_ecritures_en_memoire()
