"""Où sont les autres dépôts.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le projet tenait dans un seul dépôt. Il en tient maintenant cinq, et trois
questions continuent de porter sur plusieurs à la fois :

  · l'invariant « tout ce que le serveur permet est à l'écran » compare les routes
    d'ici aux appels de la console **et** de la vitrine ;
  · les manifestes de déploiement décrivent la configuration de ce serveur, et ils
    vivent dans le dépôt de la plateforme ;
  · les illustrations des articles sont publiées par la vitrine, alors que le texte
    des articles est ici.

Chacune de ces questions pourrait chercher son dépôt à sa façon. Trois façons de
chercher, c'est trois façons de se tromper et trois messages d'erreur différents à
comprendre. Ce module en donne **une**, que tout le reste appelle.

⚠️ CE MODULE NE DIT JAMAIS « JE N'AI RIEN TROUVÉ, DONC TOUT VA BIEN ».

Il rend la liste de ce qu'il a trouvé, éventuellement vide, et laisse son appelant
décider. C'est délibéré, et la distinction est la suivante :

  · un **outil de vérification** qui ne trouve pas ce qu'il doit lire doit
    ÉCHOUER. S'il annonçait « zéro écart » faute de fichiers à comparer, il
    éteindrait l'alarme qu'il était censé porter ;
  · un **test** qui ne trouve pas un dépôt voisin doit être IGNORÉ, en disant
    lequel manque. Le faire échouer rendrait ce dépôt intestable seul, et une
    suite rouge par construction finit par ne plus être lue du tout.

La convention de réglage est la même partout : une variable d'environnement,
plusieurs chemins séparés comme un chemin de recherche (« : » sous Linux), et à
défaut les dépôts frères, qui sont la disposition habituelle d'un poste de travail
et de la chaîne d'intégration.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
from pathlib import Path

#: Le dossier qui contient ce dépôt. Les dépôts frères y sont cherchés.
VOISINAGE = Path(__file__).resolve().parents[3]


def depots_voisins(
    noms: tuple[str, ...],
    variable: str,
    repere: str,
) -> list[Path]:
    """Les dépôts voisins qui existent vraiment, dans l'ordre où on les a cherchés.

    `noms` : les noms de dossier à essayer, du plus attendu au moins attendu. Le
    dernier peut être la disposition d'avant la scission — un monorepo encore en
    place continue alors de fonctionner sans le moindre réglage.

    `variable` : la variable d'environnement qui détrompe. Quand elle est posée,
    elle remplace entièrement les noms : on ne mélange pas un chemin réglé à la
    main avec des devinettes, sinon l'outil lit un dépôt que personne ne lui a
    désigné et le dit à personne.

    `repere` : un chemin relatif dont la présence prouve que le dossier trouvé est
    bien le dépôt cherché et non un homonyme. Sans lui, un dossier vide portant le
    bon nom passerait pour le dépôt, et l'erreur n'apparaîtrait que bien plus loin,
    sous la forme d'un fichier introuvable.
    """
    reglee = os.environ.get(variable)
    if reglee:
        candidats = [Path(morceau) for morceau in reglee.split(os.pathsep) if morceau]
    else:
        candidats = [VOISINAGE / nom for nom in noms]
    return [candidat for candidat in candidats if (candidat / repere).exists()]


def un_depot_voisin(noms: tuple[str, ...], variable: str, repere: str) -> Path | None:
    """Le premier dépôt voisin trouvé, ou rien du tout."""
    trouves = depots_voisins(noms, variable, repere)
    return trouves[0] if trouves else None


def manque(noms: tuple[str, ...], variable: str, pour_quoi: str) -> str:
    """Le message à donner quand un dépôt voisin manque.

    Écrit une fois ici pour que la personne qui le lit apprenne toujours les trois
    mêmes choses : ce qui était cherché, pourquoi, et comment le lui dire.
    """
    return (
        f"dépôt voisin introuvable : {pour_quoi}\n"
        f"  Régler {variable} sur son emplacement, plusieurs chemins séparés par "
        f"« {os.pathsep} », ou le cloner à côté de celui-ci.\n"
        f"  Cherché dans : {', '.join(str(VOISINAGE / nom) for nom in noms)}"
    )


#: La console du cabinet et la vitrine publique. ⚠️ Les deux comptent : elles
#: appellent chacune leur part des routes du serveur, et n'en lire qu'une fait
#: passer les routes de l'autre pour des routes sans écran. Au premier essai après
#: la scission, ne lire que la console a fait tomber le compte de 226 à 222 sans
#: qu'aucune route n'ait été perdue.
INTERFACES = ("erp-cga-console", "erp-cga-vitrine", "Frontend_erp_cga")
VARIABLE_INTERFACES = "CGA_RACINE_CONSOLE"

#: La vitrine seule, pour ce qu'elle publie et que le serveur décrit : les
#: illustrations des articles.
VITRINE = ("erp-cga-vitrine", "Frontend_erp_cga")
VARIABLE_VITRINE = "CGA_RACINE_VITRINE"

#: La conception, la recette et les manifestes de déploiement.
PLATEFORME = ("erp-cga-plateforme", ".")
VARIABLE_PLATEFORME = "CGA_RACINE_PLATEFORME"
