"""Un entrepôt en mémoire, générique, sur lequel s'appuient les dépôts des contextes.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE PERSISTANCE EN MÉMOIRE, ET POURQUOI CE N'EST PAS UN PIS-ALLER

La cible est PostgreSQL, et le dossier d'architecture la nomme depuis le premier
jour. Cet entrepôt-ci n'est pas la cible : c'est ce qui **prouve que les ports
sont utilisables** avant qu'un schéma de base ne les fige.

Un port qu'aucun adaptateur ne réalise est une hypothèse. Le réaliser une première
fois, même en mémoire, révèle immédiatement ce qui manque à l'interface — une
méthode absente, un tri implicite, un identifiant qu'on croyait unique. Écrire
d'abord les tables SQL, c'est découvrir ces manques après la migration, quand ils
coûtent une migration de plus.

Ce que cette implémentation permet dès aujourd'hui :

* les routes HTTP existent réellement et se testent de bout en bout ;
* le frontend a des données à afficher, sans base à installer ;
* les quatre contextes récents — B, C, E, F — sont exerçables ensemble.

Ce qu'elle ne fait pas, et qu'il faut savoir avant de la brancher ailleurs :
rien n'est durable, rien n'est transactionnel, et la numérotation d'écriture n'est
pas protégée d'un accès concurrent — le port `DepotEcritures` l'exige et seule une
séquence de base de données le garantira.

LE CLOISONNEMENT, ICI, EST LA FRONTIÈRE DE L'INSTANCE

Aucun port ne prend de paramètre `locataire` — c'est une décision documentée du
contexte B : un filtre qu'on doit penser à passer est un filtre qu'on oubliera.
En mémoire, le cloisonnement est donc porté par l'instance elle-même : un entrepôt
appartient à un locataire, et n'a aucun moyen d'en voir un autre. En base, ce sera
un filtre appliqué à la session — même règle, autre mécanique.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

__all__ = ["EntrepotMemoire"]

T = TypeVar("T")


class EntrepotMemoire(Generic[T]):
    """Une collection indexée par clé, appartenant à un locataire."""

    def __init__(self, locataire: str, cle: Callable[[T], str]) -> None:
        self.locataire = locataire
        self._cle = cle
        self._elements: dict[str, T] = {}

    # ── Écriture ────────────────────────────────────────────────────────────

    def poser(self, element: T) -> None:
        """Insère ou remplace. Les règles métier du remplacement — écriture
        validée immuable, statut daté qu'on n'écrase pas — appartiennent au dépôt
        du contexte, qui les applique avant d'appeler cette méthode."""
        self._elements[self._cle(element)] = element

    def poser_tout(self, elements: list[T]) -> None:
        for element in elements:
            self.poser(element)

    # ── Lecture ─────────────────────────────────────────────────────────────

    def prendre(self, cle: str) -> T | None:
        return self._elements.get(cle)

    def contient(self, cle: str) -> bool:
        return cle in self._elements

    def tout(self) -> list[T]:
        return list(self._elements.values())

    def filtrer(self, predicat: Callable[[T], bool]) -> list[T]:
        return [element for element in self._elements.values() if predicat(element)]

    def __iter__(self) -> Iterator[T]:
        return iter(self._elements.values())

    def __len__(self) -> int:
        return len(self._elements)

    def __repr__(self) -> str:
        return f"EntrepotMemoire(locataire={self.locataire!r}, {len(self)} élément(s))"
