"""Le dépôt d'entreprises en vigueur : SQL dans une requête, mémoire sinon (15 octobre 2026).

Dix-sept modules recopiaient `session_de_travail() … if session is None else
DepotEntreprisesSql(...)`. Les nouveaux passent par ici, via `portefeuille.api`.
"""

from __future__ import annotations

__all__ = ["depot_entreprises"]


def depot_entreprises():
    # Importés à l'appel : la session est celle de la requête en cours.
    from app.contextes.portefeuille.adaptateurs.sortant.depot_entreprises_sql import (
        DepotEntreprisesSql,
    )
    from app.contextes.portefeuille.adaptateurs.sortant.magasins_memoire import (
        entreprises_en_memoire,
    )
    from app.contextes.transverse.api import session_de_travail
    from app.partage.locataire import courant

    session = session_de_travail()
    if session is None:
        return entreprises_en_memoire()
    return DepotEntreprisesSql(session, courant())
