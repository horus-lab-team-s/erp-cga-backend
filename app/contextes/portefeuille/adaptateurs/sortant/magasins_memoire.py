"""Le magasin mémoire du contexte, et le seul.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

En persistance mémoire, le mode de démonstration et de développement, chaque
contexte qui avait besoin de ces données construisait son propre dépôt. Mesuré au
pas 52 : les écritures étaient construites en quatre endroits, les entreprises en
cinq, et une seule construction était mémorisée par sorte, parfois deux mémorisées
séparément. Une écriture saisie par la comptabilité était donc invisible de la
revue des seuils ; un régime inscrit au portefeuille, invisible des obligations ; un
exercice fermé par la clôture, écrit dans un magasin jeté aussitôt.

En base PostgreSQL tout est juste, parce que la base est unique. C'est le mode que
l'on montre au cabinet qui mentait, et il mentait sans erreur.

⚠️ Le contexte propriétaire des données tient le magasin ; ses voisins le lisent par
son `api`. C'est le patron posé au pas 28 pour la Souscription, étendu aux contextes
dont les données sont lues ailleurs. Un contrôle balaie l'application entière.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from functools import lru_cache

from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_memoire import (
    DepotEntreprisesMemoire,
)

__all__ = ["entreprises_en_memoire", "vider_les_entreprises_en_memoire"]


@lru_cache
def entreprises_en_memoire() -> DepotEntreprisesMemoire:
    """Le portefeuille, en mémoire, pour tout le processus. Perdu au redémarrage."""
    return DepotEntreprisesMemoire.avec_demonstration()


def vider_les_entreprises_en_memoire() -> None:
    """Remet le portefeuille à son état de démonstration. Destinée aux tests."""
    entreprises_en_memoire.cache_clear()
