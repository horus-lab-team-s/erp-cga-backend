"""Le répertoire des tenants, adossé à la base.

Il satisfait `RepertoireDesTenants` comme le répertoire en mémoire, et la passerelle ne
sait pas lequel elle emploie. C'est tout l'intérêt du port : la substitution ne touche pas
le chemin critique.

⚠️ **Il n'est pas destiné à être appelé à chaque requête.** La résolution d'un nom d'hôte
doit rester une lecture de dictionnaire ; ce dépôt sert à **garnir** le répertoire en
mémoire au démarrage et à le rafraîchir, pas à le remplacer. Le brancher directement sur
la passerelle ajouterait un aller-retour en base au chemin critique de tout le trafic.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contextes.tenants.adaptateurs.sortant.tables import TableTenant
from app.contextes.tenants.domaine.tenant import Tenant

__all__ = ["DepotTenantsSql"]


class DepotTenantsSql:
    """Lecture et écriture des tenants. Aucune notion de cloisonnement : voir `tables.py`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def par_slug(self, slug: str) -> Tenant | None:
        """Satisfait `RepertoireDesTenants`.

        La comparaison est faite en minuscules des deux côtés, comme l'unicité en base :
        un nom d'hôte est insensible à la casse, et résoudre « Station » autrement que
        « station » servirait un 404 à un client dont l'adresse est correcte.
        """
        if not slug or not slug.strip():
            return None
        ligne = self._session.scalars(
            select(TableTenant).where(TableTenant.slug == slug.strip().lower())
        ).one_or_none()
        return Tenant.model_validate(ligne.donnees) if ligne else None

    def tous(self) -> Iterator[Tenant]:
        """Tous les tenants, pour garnir le répertoire en mémoire au démarrage."""
        for ligne in self._session.scalars(select(TableTenant).order_by(TableTenant.slug)):
            yield Tenant.model_validate(ligne.donnees)

    def enregistrer(self, tenant: Tenant) -> None:
        """Écrit ou réécrit un tenant.

        Le document fait foi, les colonnes promues le résument. Elles sont donc réécrites
        à chaque fois depuis lui, jamais saisies à part : c'est ce qui les empêche de
        diverger de la vérité qu'elles résument.
        """
        ligne = self._session.get(TableTenant, tenant.identifiant)
        valeurs = {
            "slug": tenant.slug.lower(),
            "statut": tenant.statut.value,
            "etape_atteinte": tenant.etape_atteinte.value,
            "donnees": tenant.model_dump(mode="json"),
        }
        if ligne is None:
            self._session.add(TableTenant(identifiant=tenant.identifiant, **valeurs))
            return
        for nom, valeur in valeurs.items():
            setattr(ligne, nom, valeur)
