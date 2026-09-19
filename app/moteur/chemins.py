"""Les faits qu'un prédicat cite, extraits sans l'évaluer.

À quoi cela sert : une règle qui interroge un fait inexistant ne se signale, sans cela,
qu'au moment d'évaluer un sujet réel. Elle passe la revue, elle passe le déploiement, et
elle échoue en silence sur la première facture — ou pire, elle rend faux au lieu
d'échouer et produit un constat imaginaire.

Extraire les chemins cités permet de confronter la règle au schéma de faits **au
chargement**, c'est-à-dire au seul moment où l'erreur coûte encore zéro.

⚠️ LA PORTÉE CHANGE À L'INTÉRIEUR DES OPÉRATEURS ITÉRATIFS

`some`, `none`, `all`, `map` et `filter` prennent une collection en premier argument et
évaluent leur second argument **dans le contexte d'un élément**. Un `var` qui s'y trouve
ne désigne donc pas la racine :

    {"some": [{"var": "lignes"}, {"==": [{"var": "designation"}, ""]}]}

cite `lignes` à la racine, et `designation` **dans un élément de lignes**. Un extracteur
naïf rendrait `designation` comme un fait racine, que le schéma déclarerait inconnu : le
garde-fou refuserait alors des règles correctes, et on finirait par le désactiver.

La convention retenue pour nommer un fait d'élément est le suffixe `[]` sur la
collection : `lignes[].designation`. C'est la même convention que celle employée dans le
schéma de faits, de sorte que les deux se comparent directement.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ITERATIFS", "chemins_cites"]

#: Opérateurs dont le second argument s'évalue dans le contexte d'un élément.
#: Sous-ensemble de ce que l'évaluateur accepte : voir `OPERATEURS_AUTORISES`.
ITERATIFS: frozenset[str] = frozenset({"some", "none", "all", "map", "filter"})


def _chemin(argument: Any) -> str | None:
    """Le chemin désigné par un `var`, quand il est littéral.

    Un `var` dont l'argument est lui-même calculé — `{"var": {"cat": [...]}}` — n'a pas
    de chemin connaissable avant l'évaluation. On rend None plutôt que de deviner : le
    garde-fou préfère ignorer un cas qu'il ne comprend pas à refuser une règle valable.
    """
    if isinstance(argument, str):
        return argument or None
    if isinstance(argument, list) and argument and isinstance(argument[0], str):
        return argument[0] or None
    return None


def chemins_cites(predicat: Any, prefixe: str = "") -> set[str]:
    """Tous les faits cités par un prédicat, en chemins racine.

    `prefixe` sert à la récursion dans les opérateurs itératifs et n'a pas vocation à
    être fourni par l'appelant.
    """
    if isinstance(predicat, list):
        trouves: set[str] = set()
        for element in predicat:
            trouves |= chemins_cites(element, prefixe)
        return trouves

    if not isinstance(predicat, dict) or len(predicat) != 1:
        return set()

    ((operateur, arguments),) = predicat.items()

    if operateur == "var":
        chemin = _chemin(arguments)
        # `{"var": ""}` désigne l'élément courant lui-même, pas un fait : à l'intérieur
        # d'une itération il vaut la collection, hors itération il vaut le sujet entier.
        return {prefixe + chemin} if chemin else set()

    if operateur == "missing":
        noms = arguments if isinstance(arguments, list) else [arguments]
        return {prefixe + nom for nom in noms if isinstance(nom, str) and nom}

    if operateur in ITERATIFS and isinstance(arguments, list) and len(arguments) >= 2:
        collection, corps = arguments[0], arguments[1]
        trouves = chemins_cites(collection, prefixe)
        # Le corps se lit dans un élément de la collection. Si celle-ci n'est pas un
        # `var` littéral, on ne sait pas nommer l'élément : on n'extrait rien du corps
        # plutôt que de produire des chemins faux.
        racine = _chemin(collection.get("var")) if isinstance(collection, dict) else None
        if racine:
            trouves |= chemins_cites(corps, f"{prefixe}{racine}[].")
        trouves |= chemins_cites(arguments[2:], prefixe)
        return trouves

    return chemins_cites(arguments, prefixe)
