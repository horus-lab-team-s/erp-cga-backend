"""Le recensement des tables — un seul endroit, et il doit être complet.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Les métadonnées SQLAlchemy ne connaissent que les modules **chargés**. Une table
dont le module n'a pas été importé n'existe pas de leur point de vue, et trois
conséquences en découlent, toutes silencieuses :

* `create_all` ne la crée pas — le code échoue plus tard sur « relation does not
  exist » ;
* `autogenerate` ne la voit pas — la migration ne la crée pas non plus ;
* pire, `autogenerate` propose de **supprimer** une table déjà en base dont il ne
  voit plus la déclaration.

Le piège est documenté dans toute la littérature d'Alembic. Je m'y suis pris
trois fois : à l'amorçage, à la première migration métier, puis dans les tests.
Chaque fois, l'oubli était le même — un import manquant à un endroit différent.

Un recensement unique, importé partout où les métadonnées comptent, remplace
trois oublis possibles par un seul. Et `test_tables.py` vérifie qu'il n'a pas été
oublié : il compare ce module aux fichiers `tables.py` présents dans les
contextes.

POURQUOI ICI ET NON DANS `app/infrastructure/`

Parce qu'il connaît tous les contextes, et que le garde-fou refuse au cercle
externe de connaître le métier. C'est de la composition, comme `app/main.py` et
`app/amorcage.py`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.collecte.adaptateurs.sortant import tables as collecte
from app.contextes.comptabilite.adaptateurs.sortant import tables as comptabilite
from app.contextes.conformite.adaptateurs.sortant import tables as conformite
from app.contextes.creation_entreprise.adaptateurs.sortant import (
    tables as creation_entreprise,
)
from app.contextes.pilotage.adaptateurs.sortant import tables as pilotage
from app.contextes.portefeuille.adaptateurs.sortant import tables as portefeuille
from app.contextes.referentiel.adaptateurs.sortant import tables as referentiel
from app.contextes.social.adaptateurs.sortant import tables as social
from app.contextes.souscription.adaptateurs.sortant import tables as souscription
from app.contextes.tenants.adaptateurs.sortant import tables as tenants
from app.contextes.transverse.adaptateurs.sortant import tables as transverse
from app.infrastructure import tables_orchestration as orchestration
from app.infrastructure.base_de_donnees import METADONNEES

__all__ = ["METADONNEES", "MODULES", "noms_des_tables"]

#: Les modules de tables, un par contexte qui persiste, plus le socle
#: d'orchestration — dont les tables ne sont le métier de personne.
#:
#: ⚠️ Toute nouvelle table s'ajoute ici — et nulle part ailleurs.
MODULES = (
    collecte,
    comptabilite,
    conformite,
    creation_entreprise,
    pilotage,
    portefeuille,
    referentiel,
    social,
    souscription,
    tenants,
    transverse,
    orchestration,
)


def noms_des_tables() -> list[str]:
    """Les tables recensées, triées. Sert au diagnostic et aux tests."""
    return sorted(METADONNEES.tables)
