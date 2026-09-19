"""Le répertoire des tenants, en mémoire.

Ce n'est pas un pis-aller : c'est la forme définitive. La table des tenants tient
entièrement en mémoire dans chaque réplique de la passerelle — quelques centaines
d'entrées de moins de cent octets —, et la résolution est donc une lecture de
dictionnaire, pas un aller-retour en base sur le chemin critique de tout le trafic.

Ce qui manque encore est la **source** : aujourd'hui la configuration, demain la table des
tenants relue sur événement d'ouverture ou de suspension. La substitution ne touchera pas
la passerelle, qui ne connaît que le port.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.contextes.tenants.domaine.tenant import Tenant

__all__ = ["RepertoireEnMemoire"]


class RepertoireEnMemoire:
    """Satisfait `RepertoireDesTenants`. Insensible à la casse, comme un nom d'hôte."""

    def __init__(self, tenants: Iterable[Tenant] = ()) -> None:
        self._par_slug: dict[str, Tenant] = {}
        for tenant in tenants:
            self.inscrire(tenant)

    def inscrire(self, tenant: Tenant) -> None:
        self._par_slug[tenant.slug.lower()] = tenant

    def remplacer(self, tenants: Iterable[Tenant]) -> int:
        """Substitue tout le contenu d'un coup. Rend le nombre inscrit.

        ─────────────────────────────────────────────────────────────────────────────
        ⚠️ **REMPLACER, ET NON VERSER PAR-DESSUS.**

        Un rafraîchissement qui appellerait `inscrire` en boucle garderait les entrées
        que la table ne porte plus. Le cas n'est pas théorique : un tenant retiré de la
        table continuerait d'être résolu, donc servi, jusqu'au redémarrage — exactement
        le défaut que le rafraîchissement vient corriger, retourné dans l'autre sens.

        ⚠️ **La substitution est faite sur un dictionnaire neuf**, jamais par un
        `clear()` suivi d'un remplissage. Le répertoire est lu par le chemin critique de
        toutes les requêtes ; entre le vidage et la fin du remplissage, chaque lecture
        aurait rendu `None`, et un tenant parfaitement valide aurait reçu `404` pendant
        que son répertoire se reconstruisait. L'affectation d'un nom, elle, est atomique
        pour les lecteurs.
        ─────────────────────────────────────────────────────────────────────────────
        """
        neuf = {tenant.slug.lower(): tenant for tenant in tenants}
        self._par_slug = neuf
        return len(neuf)

    def par_slug(self, slug: str) -> Tenant | None:
        return self._par_slug.get(slug.strip().lower()) if slug else None

    def __len__(self) -> int:
        """Combien de tenants il tient.

        ⚠️ Ajouté pour la sonde du registre des services, et la première rédaction
        de cette sonde a levé faute de l'avoir : elle appelait une méthode `tous()`
        qui n'existait pas. Le registre l'a rattrapée et l'a marquée `EN_PANNE`
        plutôt que de tomber avec elle, ce qui est exactement son devoir — mais
        c'est bien ici que manquait quelque chose.

        `__len__` plutôt qu'un `nombre()` : un répertoire est une collection, et
        `len()` est ce qu'un lecteur essaie en premier. Une méthode nommée
        autrement se cherche.
        """
        return len(self._par_slug)


class RegistreEnMemoire(RepertoireEnMemoire):
    """Le répertoire, augmenté de l'écriture. Satisfait `RegistreDesTenants`.

    ⚠️ Il n'y a **pas** de `RepertoireEnMemoire.enregistrer`, et c'est délibéré :
    le répertoire est la vue en lecture que consulte la passerelle à chaque
    requête, et lui donner une écriture inviterait à provisionner depuis le
    chemin critique.

    Cette sous-classe existe pour le mode démonstration et pour les tests, où la
    même structure sert des deux côtés faute de base. En exploitation, le
    registre est le dépôt SQL, et le répertoire une projection en mémoire
    garnie au démarrage.
    """

    def enregistrer(self, tenant: Tenant) -> None:
        self.inscrire(tenant)
