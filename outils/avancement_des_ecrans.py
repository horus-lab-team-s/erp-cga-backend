"""À combien de pour cent sont les écrans ? Une mesure contre une grille, pas une impression.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE SCRIPT EXISTE (pas 80)

La question « nous sommes à combien de pour cent ? » n'avait pas de réponse mesurée.
La couverture des routes (`couverture_des_ecrans.py`) dit quelle part du backend un
écran appelle ; elle ne dit pas si les écrans attendus sont faits, et elle ignore la
méthode : lire les pièces (GET) y comptait comme les déposer (POST).

La cible est déclarée dans `Docs/architecture/avancement/grille-des-ecrans.yaml` :
pour chaque écran, les gestes qu'il doit permettre, chacun avec sa route. Ce script
confronte la grille à la réalité.

    BRANCHÉ       la route existe au backend, et une page de l'écran atteint un appel
                  qui la désigne avec cette méthode
    À BRANCHER    la route existe, aucun appel ne la désigne
    SANS BACKEND  la grille déclare `route: null` : aucun backend ne le permet encore

Pourcentage d'un écran : gestes branchés / gestes. Pourcentage global : la même chose
sur tous les gestes, chacun pesant autant.

⚠️ LA GRILLE EST CONTRÔLÉE AVANT D'ÊTRE MESURÉE

Une grille fausse donnerait un pourcentage faux avec l'air d'une mesure. Le script
refuse (code de sortie 1) :

- une route citée qui n'existe pas au backend, avec cette méthode ;
- une page citée qui n'existe pas au frontend ;
- un geste « sans backend » sans note qui dise pourquoi.

Il **signale** aussi les routes du backend qu'aucun geste ne cite : soit la grille est
incomplète, soit la route n'a pas d'usage à l'écran, et quelqu'un doit le dire.

⚠️ CE QUE CE POURCENTAGE NE DIT PAS

Qu'un geste branché est bien fait, ni que les états vide, erreur et hors ligne existent.
Il suit la grille : ajouter un geste attendu fait baisser le chiffre, et c'est voulu.

USAGE (depuis `Backend_erp_cga`)

    CGA_PERSISTANCE=memoire python -m outils.avancement_des_ecrans           # par écran
    CGA_PERSISTANCE=memoire python -m outils.avancement_des_ecrans --detail  # et chaque geste
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

import yaml

from outils.contrat_des_ecrans import RACINE_FRONTEND, _route_de

GRILLE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "Docs"
    / "architecture"
    / "avancement"
    / "grille-des-ecrans.yaml"
)

#: Les routes d'infrastructure, qu'aucun écran n'a à appeler : elles ne sont pas
#: signalées comme absentes de la grille.
HORS_ECRANS = {
    ("GET", "/sante"),
    ("GET", "/transverse/courriels"),
    ("POST", "/souscription/notification/tara"),
    ("POST", "/souscription/paiements/{identifiant}/simulation"),
    ("POST", "/acquisition/demandes"),
    ("GET", "/acquisition/questionnaires/{service}"),
    ("GET", "/acquisition/dossiers/{reference}/qualification"),
    ("GET", "/acquisition/motifs-de-classement"),
    ("GET", "/acquisition/rappels"),
    ("GET", "/acquisition/proformas/{numero}/consultation"),
    ("GET", "/conformite/demonstration"),
    ("GET", "/collecte/pieces/{identifiant}"),
    ("GET", "/comptabilite/dossiers/{entreprise}/ecritures/{exercice}/{journal}/{numero}"),
    ("GET", "/comptabilite/journaux"),
    ("GET", "/comptabilite/plan-comptable"),
    ("GET", "/transverse/roles"),
    ("GET", "/vitrine/annonce"),
    ("GET", "/vitrine/articles"),
    ("GET", "/vitrine/articles/{slug}"),
    ("GET", "/vitrine/institutions"),
}


def routes_du_backend() -> set[tuple[str, str]]:
    """Chaque couple (méthode, chemin) déclaré par l'application."""
    from tests.test_surface_des_permissions import _routes

    return {(m, chemin) for chemin, route in _routes() for m in route.methods if m != "HEAD"}


def pages_du_frontend() -> set[str]:
    """Les pages, sans le préfixe de langue ni les groupes de routes « (…) »."""
    pages = set()
    for fichier in (RACINE_FRONTEND / "app" / "[locale]").rglob("page.tsx"):
        parties = [
            p
            for p in fichier.parent.relative_to(RACINE_FRONTEND / "app" / "[locale]").parts
            if not (p.startswith("(") and p.endswith(")"))
        ]
        pages.add("/" + "/".join(parties))
    return pages


def routes_atteintes_par_page(routes: set[tuple[str, str]]) -> dict[str, set[tuple[str, str]]]:
    """Pour chaque page du frontend, les couples (méthode, route) qu'elle atteint.

    ⚠️ La première version comptait un geste branché dès qu'**un appel quelconque** du
    frontend désignait la route. L'accueil adhérent sortait à 100 % parce que les écrans
    des collaborateurs lisent les mêmes routes. Le script Node suit désormais, depuis
    chaque page et ses gabarits, les symboles réellement employés jusqu'aux appels.
    """
    sortie = subprocess.run(
        ["node", "outils/types-des-appels.mjs", "--pages"],
        cwd=RACINE_FRONTEND,
        capture_output=True,
        text=True,
        check=True,
    )
    releve = json.loads(sortie.stdout)
    chemins: dict[str, dict[str, Any]] = {}
    for methode, chemin in routes:
        chemins.setdefault(chemin, {})[methode.lower()] = {}
    route_de_l_appel: dict[str, tuple[str, str]] = {}
    for appel in releve["appels"]:
        if not appel["chemin"]:
            continue
        route = _route_de(appel["chemin"], appel["methode"], chemins)
        if route is not None:
            route_de_l_appel[appel["fichier"]] = (appel["methode"], route)
    return {
        page: {route_de_l_appel[cle] for cle in cles if cle in route_de_l_appel}
        for page, cles in releve["parPage"].items()
    }


def mesurer() -> tuple[list[dict[str, Any]], list[str], list[tuple[str, str]]]:
    grille = yaml.safe_load(GRILLE.read_text(encoding="utf-8"))
    routes = routes_du_backend()
    pages = pages_du_frontend()
    par_page = routes_atteintes_par_page(routes)

    erreurs: list[str] = []
    citees: set[tuple[str, str]] = set()
    ecrans = []
    for ecran in grille["ecrans"]:
        atteintes: set[tuple[str, str]] = set()
        for page in ecran.get("pages") or []:
            if page not in pages:
                erreurs.append(f"{ecran['ref']} : page « {page} » absente du frontend")
            atteintes |= par_page.get(page, set())
        gestes = []
        for g in ecran["gestes"]:
            if g.get("route") is None:
                if not g.get("note"):
                    erreurs.append(f"{ecran['ref']} : « {g['geste']} » sans backend et sans note")
                gestes.append({**g, "etat": "SANS BACKEND"})
                continue
            methode, chemin = g["route"].split(" ", 1)
            if (methode, chemin) not in routes:
                erreurs.append(f"{ecran['ref']} : route « {g['route']} » inconnue du backend")
                gestes.append({**g, "etat": "ROUTE INCONNUE"})
                continue
            citees.add((methode, chemin))
            etat = "BRANCHÉ" if (methode, chemin) in atteintes else "À BRANCHER"
            # ⚠️ Un geste « à brancher » ici peut être branché sur une autre page : la grille
            # l'a alors rattaché au mauvais écran. Le dire évite de le compter manquant à
            # tort, et de le reconstruire en double.
            ailleurs = sorted(
                page for page, r in par_page.items()
                if (methode, chemin) in r and page not in (ecran.get("pages") or [])
            ) if etat == "À BRANCHER" else []
            gestes.append({**g, "etat": etat, "ailleurs": ailleurs})
        branches = sum(1 for g in gestes if g["etat"] == "BRANCHÉ")
        ecrans.append({**ecran, "gestes": gestes, "branches": branches})
    orphelines = sorted(routes - citees - HORS_ECRANS)
    return ecrans, erreurs, orphelines


def _pourcent(partie: int, tout: int) -> str:
    return f"{round(100 * partie / tout)} %" if tout else "—"


def main() -> None:
    ecrans, erreurs, orphelines = mesurer()
    if erreurs:
        print("GRILLE REFUSÉE :")
        for e in erreurs:
            print(f"  {e}")
        sys.exit(1)

    total = sum(len(e["gestes"]) for e in ecrans)
    branches = sum(e["branches"] for e in ecrans)
    sans_backend = sum(1 for e in ecrans for g in e["gestes"] if g["etat"] == "SANS BACKEND")
    a_brancher = total - branches - sans_backend
    print(
        f"AVANCEMENT DES ÉCRANS : {_pourcent(branches, total)} "
        f"({branches} gestes branchés sur {total} ; {a_brancher} à brancher ; "
        f"{sans_backend} sans backend)"
    )
    avec_backend = total - sans_backend
    print(
        f"  dont, sur les gestes que le backend permet déjà : "
        f"{_pourcent(branches, avec_backend)} ({branches} sur {avec_backend})"
    )
    # La lecture par priorité de l'inventaire : ce qui compte d'abord, c'est où en sont
    # les écrans que le dossier de design demande en premier.
    priorites = sorted({e["priorite"] for e in ecrans if e.get("priorite") is not None})
    for rang in priorites:
        du_rang = [e for e in ecrans if e.get("priorite") == rang]
        n = sum(len(e["gestes"]) for e in du_rang)
        b = sum(e["branches"] for e in du_rang)
        refs = ", ".join(e["ref"] for e in du_rang)
        print(f"  priorité {rang} ({refs}) : {_pourcent(b, n)} ({b} sur {n})")
    hors = [e for e in ecrans if e.get("priorite") is None]
    if hors:
        n = sum(len(e["gestes"]) for e in hors)
        b = sum(e["branches"] for e in hors)
        print(f"  hors inventaire ({len(hors)} espaces) : {_pourcent(b, n)} ({b} sur {n})")
    print()
    largeur = max(len(e["libelle"]) for e in ecrans)
    for e in ecrans:
        n = len(e["gestes"])
        sb = sum(1 for g in e["gestes"] if g["etat"] == "SANS BACKEND")
        print(
            f"  {e['ref']:18} {e['libelle']:{largeur}}  {e['branches']:>2}/{n:<2} "
            f"{_pourcent(e['branches'], n):>5}" + (f"   ({sb} sans backend)" if sb else "")
        )
        if "--detail" in sys.argv:
            for g in e["gestes"]:
                if g["etat"] != "BRANCHÉ":
                    print(f"      {g['etat']:12} {g['geste']}  [{g.get('route') or g.get('note')}]")
                    if g.get("ailleurs"):
                        print(f"      {'':12} ⚠️ atteint ailleurs : {', '.join(g['ailleurs'])}")
    if orphelines:
        print()
        print(f"  Routes du backend qu'aucun geste ne cite ({len(orphelines)}) :")
        for methode, chemin in orphelines:
            print(f"      {methode:6} {chemin}")


if __name__ == "__main__":
    main()

