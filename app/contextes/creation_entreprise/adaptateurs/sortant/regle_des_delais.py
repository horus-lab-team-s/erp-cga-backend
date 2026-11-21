"""Le dépôt de la règle des délais : en base dans une requête, mémoire sinon (lot 4)."""

from __future__ import annotations

from app.contextes.creation_entreprise.adaptateurs.sortant.depots import DepotRegleDesDelaisSql
from app.contextes.creation_entreprise.adaptateurs.sortant.magasins_memoire import (
    regle_des_delais_en_memoire,
)
from app.contextes.creation_entreprise.domaine.delais import (
    REGLE_SANS_DELAIS,
    RegleDesDelaisDeCreation,
)
from app.contextes.transverse.api import session_de_travail
from app.partage.locataire import courant

__all__ = ["depot_de_la_regle_des_delais", "regle_des_delais_en_vigueur"]


def depot_de_la_regle_des_delais():
    session = session_de_travail()
    if session is None:
        return regle_des_delais_en_memoire()
    return DepotRegleDesDelaisSql(session, courant())


def regle_des_delais_en_vigueur() -> RegleDesDelaisDeCreation:
    return depot_de_la_regle_des_delais().lire() or REGLE_SANS_DELAIS
