"""Les dépôts du social en vigueur : SQL dans une requête, mémoire sinon.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Le choix des dépôts vivait dans les routes du social. Tant que personne d'autre ne
lisait le personnel, c'était sans conséquence. Depuis le pas 56, l'échéancier des
obligations demande si un dossier a employé quelqu'un : s'il construisait ses
propres dépôts, il lirait en mémoire un personnel de démonstration pendant que les
routes du social en écrivent un autre, le défaut exact du pas 52.

Le contexte propriétaire tient donc le seul choix, et ses voisins le lisent par
l'`api`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from functools import lru_cache

from app.contextes.social.adaptateurs.sortant.depots import (
    DepotContratsMemoire,
    DepotContratsSql,
    DepotSalariesMemoire,
    DepotSalariesSql,
)
from app.contextes.social.adaptateurs.sortant.donnees_demo import (
    CONTRATS_DEMO,
    RATTACHEMENTS_DEMO,
    SALARIES_DEMO,
)
from app.contextes.social.domaine.ports import DepotContrats, DepotSalaries
from app.contextes.transverse.api import session_de_travail
from app.partage.locataire import courant

__all__ = ["depots_du_social", "vider_les_magasins_du_social"]


def depots_du_social() -> tuple[DepotSalaries, DepotContrats]:
    """Les dépôts en vigueur, **toujours ensemble**.

    Le dépôt de contrats a besoin du rattachement des salariés à leur dossier, et
    les séparer laisserait un appelant en composer une paire incohérente : contrats
    en base, rattachements en mémoire.
    """
    session = session_de_travail()
    if session is None:
        return _depots_memoire()
    salaries = DepotSalariesSql(session, courant())
    contrats = DepotContratsSql(session, courant(), RATTACHEMENTS_DEMO)
    return salaries, contrats


@lru_cache
def _depots_memoire() -> tuple[DepotSalariesMemoire, DepotContratsMemoire]:
    salaries = DepotSalariesMemoire(list(SALARIES_DEMO))
    contrats = DepotContratsMemoire(list(CONTRATS_DEMO))
    for matricule, entreprise in RATTACHEMENTS_DEMO.items():
        contrats.rattacher(matricule, entreprise)
    return salaries, contrats


def vider_les_magasins_du_social() -> None:
    """Remet le personnel en mémoire à son état de démonstration. Destinée aux tests."""
    _depots_memoire.cache_clear()
