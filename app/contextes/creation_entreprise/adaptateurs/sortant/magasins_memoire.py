"""Le magasin mémoire des dossiers de création. **Un seul, et il vit ici.**

En mémoire, ceci EST la persistance : deux instances seraient deux bases sans lien. Les
routes de la création et le pilotage (la vue des retards, lot 4) lisent donc la même
fabrique, par l'api du contexte. Voir `souscription/adaptateurs/sortant/magasins_memoire.py`.
"""

from __future__ import annotations

from functools import lru_cache

from app.contextes.creation_entreprise.adaptateurs.sortant.depots import (
    DepotDossiersCreationMemoire,
    DepotRegleDesDelaisMemoire,
)
from app.contextes.creation_entreprise.adaptateurs.sortant.donnees_demo import PIPELINE_DEMO

__all__ = ["creations_en_memoire", "regle_des_delais_en_memoire"]


@lru_cache
def creations_en_memoire() -> DepotDossiersCreationMemoire:
    return DepotDossiersCreationMemoire(list(PIPELINE_DEMO))


@lru_cache
def regle_des_delais_en_memoire() -> DepotRegleDesDelaisMemoire:
    return DepotRegleDesDelaisMemoire()
