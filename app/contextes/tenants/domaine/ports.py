"""Ce que le contexte N attend du monde extérieur.

Un seul port pour l'instant : retrouver un tenant par son slug. C'est l'opération du
chemin critique, appelée à chaque requête, et c'est la seule dont la passerelle a besoin.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.contextes.tenants.domaine.tenant import Tenant

__all__ = ["RepertoireDesTenants"]


@runtime_checkable
class RepertoireDesTenants(Protocol):
    """Retrouve un tenant par son slug.

    Rend `None` plutôt que de lever : un slug inconnu est un cas ordinaire — une faute de
    frappe, un scanner, un ancien lien — et lever ferait de chacun une erreur à
    diagnostiquer.

    ⚠️ **L'implémentation doit être rapide.** Elle est appelée à chaque requête, avant
    tout le reste. Une lecture en base à chaque fois ajouterait un aller-retour au chemin
    critique de tout le trafic : la table des tenants tient en mémoire, quelques centaines
    d'entrées de moins de cent octets, et se rafraîchit sur événement.
    """

    def par_slug(self, slug: str) -> Tenant | None: ...
