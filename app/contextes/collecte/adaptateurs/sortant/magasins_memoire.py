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

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import (
    DepotDemandesMemoire,
    DepotPiecesMemoire,
)

__all__ = [
    "demandes_en_memoire",
    "pieces_en_memoire",
    "vider_les_magasins_de_la_collecte",
]


@lru_cache
def pieces_en_memoire() -> DepotPiecesMemoire:
    """Les pièces déposées, en mémoire, pour tout le processus.

    ⚠️ Pas 88 : les documents des pièces de démonstration sont rangés au magasin en même
    temps, sans quoi chaque « Télécharger le document » rendait « introuvable ».
    """
    from pathlib import Path

    from app.contextes.collecte.adaptateurs.sortant.donnees_demo import semer_les_documents_demo
    from app.contextes.collecte.adaptateurs.sortant.magasin_local import MagasinFichiersLocal
    from app.infrastructure.config import configuration

    semer_les_documents_demo(
        MagasinFichiersLocal(
            Path(configuration().dossier_fichiers), configuration().locataire_par_defaut
        )
    )
    return DepotPiecesMemoire.avec_demonstration()


@lru_cache
def demandes_en_memoire() -> DepotDemandesMemoire:
    """Les demandes de pièces, en mémoire, pour tout le processus."""
    return DepotDemandesMemoire.avec_demonstration()


def vider_les_magasins_de_la_collecte() -> None:
    """Remet pièces et demandes à leur état de démonstration. Destinée aux tests.

    ⚠️ Les deux ensemble : une demande satisfaite pointe une pièce, et vider l'un sans
    l'autre laisserait une demande vers une pièce disparue.
    """
    pieces_en_memoire.cache_clear()
    demandes_en_memoire.cache_clear()
