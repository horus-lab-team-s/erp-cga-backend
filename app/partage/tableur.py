"""Neutraliser les formules dans ce que le cabinet exporte en Excel ou en CSV.

⚠️ 19 octobre 2026 (audit de sécurité) : le journal de caisse écrivait le nom saisi par
un visiteur anonyme sur le site, tel quel. Un nom comme `=HYPERLINK("http://…")` devenait
une formule à l'ouverture du classeur, dans le poste de la caissière. C'est l'injection
de formule (« CSV injection ») : le tableur exécute ce que le fichier lui présente.

Deux parades, selon le format :
* **Excel** : la cellule est déclarée texte (`data_type = "s"`), ce qu'openpyxl ne fait
  pas seul pour une chaîne qui commence par « = » ;
* **CSV** : il n'y a pas de type ; on préfixe d'une apostrophe toute valeur qui commence
  par un caractère qui déclenche une formule, comme le recommande l'OWASP.

⚠️ Pas pour les fichiers d'échange lus par une machine (format d'échange comptable) :
l'apostrophe y serait une donnée, et fausserait l'import.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Les premiers caractères qui font d'une cellule une formule (OWASP).
_DECLENCHEURS = ("=", "+", "-", "@", "\t", "\r")


def texte_sur(valeur: Any) -> Any:
    """La valeur, rendue inoffensive pour un CSV ouvert dans un tableur."""
    if isinstance(valeur, str) and valeur.startswith(_DECLENCHEURS):
        return "'" + valeur
    return valeur


def ligne_sure(valeurs: Iterable[Any]) -> list[Any]:
    """Une ligne de CSV dont aucune cellule ne devient une formule."""
    return [texte_sur(v) for v in valeurs]


def forcer_le_texte(feuille: Any, rang: int, colonnes: Iterable[int]) -> None:
    """Déclare texte les cellules de ces colonnes, sur la ligne `rang` d'une feuille openpyxl."""
    for colonne in colonnes:
        cellule = feuille.cell(row=rang, column=colonne)
        if isinstance(cellule.value, str):
            cellule.data_type = "s"
