"""Combien de routes du backend le frontend appelle-t-il ? Une mesure, pas une addition.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE SCRIPT EXISTE (pas 71)

Du pas 57 au pas 70, le journal et le document de conception annonçaient une
couverture des écrans (« 75 routes sur 134 ») obtenue en ajoutant à la main les
routes branchées à chaque pas. Rejouée pour de vrai, la mesure donnait 79 : une
action existante appelait déjà la validation et la contre-passation d'une écriture,
et l'addition l'ignorait. Une addition dérive ; une mesure se rejoue.

COMMENT IL MESURE

1. Les routes : celles que l'application FastAPI déclare, lues par la même aide que
   le contrôle de surface des permissions (`tests/test_surface_des_permissions.py`).
2. Les appels : toute chaîne du frontend qui commence par « / » suivi d'une lettre,
   dans les fichiers `.ts` et `.tsx` de `app/` du dépôt de la console.
3. Une route est couverte si l'un de ces appels peut la désigner.

⚠️ UN MORCEAU INTERPOLÉ PEUT PORTER PLUSIEURS SEGMENTS

La clé d'une écriture s'écrit `${cle}` dans le frontend et vaut « 2026/AC/12 » :
trois segments d'adresse. La première mesure faisait correspondre un `${…}` à un
seul segment, et comptait la validation comme non appelée. Ici, `${…}` correspond
à un ou plusieurs segments.

⚠️ CE QUE LA MESURE NE DIT PAS

Qu'une route soit appelée ne dit pas que l'écran est bon, ni même qu'il est
atteignable par un rôle qui a le droit de s'en servir. C'est un plancher de
travail restant, pas un certificat.

USAGE

    cd erp-cga-backend
    CGA_PERSISTANCE=memoire python -m outils.couverture_des_ecrans          # le total
    CGA_PERSISTANCE=memoire python -m outils.couverture_des_ecrans --liste  # et les absentes
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import pathlib
import re
import sys


def racines_des_interfaces() -> list[pathlib.Path]:
    """Où trouver le dépôt de la console, qui n'est plus dans celui-ci.

    ─────────────────────────────────────────────────────────────────────────────────
    ⚠️ **CET OUTIL LIT UN AUTRE DÉPÔT, ET C'EST LE PRIX DE LA SCISSION.**

    Il mesure l'invariant du projet : tout ce que le serveur permet est à l'écran. Cette
    question porte sur deux dépôts à la fois, et aucun des deux ne peut y répondre seul.

    ⚠️ **IL Y EN A DEUX, ET LES DEUX COMPTENT.** La console et la vitrine appellent
    chacune leur part des routes du serveur. N'en lire qu'une fait passer les routes de
    l'autre pour des routes sans écran : au premier essai après la scission, le compte est
    tombé de 226 à 222 sans qu'aucune route n'ait été perdue.

    Les chemins se règlent par `CGA_RACINE_CONSOLE`, plusieurs séparés comme un chemin de
    recherche. À défaut, on cherche les dépôts frères, ce qui est la disposition habituelle
    d'un poste de travail et de la chaîne d'intégration.

    ⚠️ **Absente, la console ne rend pas cet outil silencieux : elle le fait échouer.**
    Un outil de vérification qui, faute de trouver ce qu'il doit lire, annonce « zéro
    écart » est pire qu'un outil manquant : il éteint l'alarme qu'il était censé porter.
    C'est exactement le défaut que le pas 119 a corrigé sur la recette.
    ─────────────────────────────────────────────────────────────────────────────────
    """
    reglee = os.environ.get("CGA_RACINE_CONSOLE")
    voisins = pathlib.Path(__file__).resolve().parents[2]
    candidats = (
        [pathlib.Path(morceau) for morceau in reglee.split(os.pathsep) if morceau]
        if reglee
        else [
            voisins / "erp-cga-console",
            voisins / "erp-cga-vitrine",
            # La disposition d'avant la scission, pour qu'un monorepo encore en place
            # continue de fonctionner sans réglage.
            voisins / "Frontend_erp_cga",
        ]
    )
    trouvees = [c for c in candidats if (c / "app").is_dir()]
    if trouvees:
        return trouvees
    raise SystemExit(
        "console introuvable : cet outil compare les routes du serveur aux appels de "
        "l'interface, et l'interface vit dans le dépôt `erp-cga-console` depuis la "
        "scission.\n"
        "  Régler CGA_RACINE_CONSOLE sur une copie de ces dépôts, séparés comme un "
        "chemin de recherche, ou les placer à côté de celui-ci.\n"
        f"  Cherché dans : {', '.join(str(c) for c in candidats)}"
    )


RACINES_FRONTEND = [racine / "app" for racine in racines_des_interfaces()]

#: Une chaîne du frontend qui ressemble à un chemin d'API : « /quelque-chose… ».
APPEL = re.compile(r"[`\"'](/[a-z][^`\"'?]*)")
#: Un morceau interpolé, une fois le chemin échappé pour une expression régulière.
INTERPOLATION = re.compile(r"\\\$\\\{[^}]*\\\}")
#: Un paramètre de route FastAPI : « {niu} ».
PARAMETRE = re.compile(r"\{[^}]+\}")


def appels_du_frontend(
    racines: list[pathlib.Path] | None = None,
) -> list[re.Pattern[str]]:
    """Chaque chaîne-chemin des interfaces, transformée en motif qui accepte ses valeurs."""
    motifs = []
    fichiers = sorted(f for racine in (racines or RACINES_FRONTEND) for f in racine.rglob("*.ts*"))
    for fichier in fichiers:
        for chemin in APPEL.findall(fichier.read_text(errors="ignore")):
            # ⚠️ Pas 93 : une interpolation qui suit une barre est un segment (`/pieces/${id}`),
            # elle vaut au moins un caractère. Collée à un mot (`/conformite/controler${requete}`),
            # c'est une chaîne de requête construite à part, qui peut être vide : la compter comme
            # un segment faisait passer l'essai des règles (pas 90) pour un appel sans route.
            echappe = re.escape(chemin)
            echappe = re.sub(r"(?<=/)" + INTERPOLATION.pattern, ".+", echappe)
            motifs.append(re.compile("^" + INTERPOLATION.sub(".*", echappe) + "$"))
    return motifs


def mesurer() -> tuple[int, list[tuple[str, str]]]:
    """Le nombre total de routes, et celles qu'aucun appel ne désigne."""
    # Import tardif : charger l'application démarre le câblage, inutile pour `--help`.
    from tests.test_surface_des_permissions import _routes

    motifs = appels_du_frontend()
    routes = _routes()
    absentes = []
    for chemin, route in routes:
        # Un exemple concret de la route : chaque paramètre remplacé par « x ».
        exemple = PARAMETRE.sub("x", chemin)
        if not any(motif.match(exemple) for motif in motifs):
            absentes.append((sorted(route.methods)[0], chemin))
    return len(routes), absentes


def main() -> None:
    total, absentes = mesurer()
    appelees = total - len(absentes)
    print(f"{appelees} routes appelées par le frontend sur {total} ({appelees * 100 // total} %)")
    if "--liste" in sys.argv:
        for methode, chemin in absentes:
            print(f"  {methode:6} {chemin}")


if __name__ == "__main__":
    main()
