"""Ports du contexte B · Portefeuille adhérents.

**Inversion de dépendance.** Le cas d'usage a besoin des dossiers ; il ne doit pas
savoir où ils sont rangés. Le port est déclaré ici, dans le cercle le plus
interne ; l'adaptateur qui le réalise vit dans le cercle 3 et dépend de cette
interface — jamais l'inverse.

⚠️ **LE CLOISONNEMENT N'APPARAÎT PAS DANS CETTE INTERFACE, ET C'EST VOULU.**

Chaque table métier porte un identifiant de tenant, et un comptable ne voit que
les dossiers de son portefeuille. Mais ce filtrage est appliqué **dans la couche
de persistance**, systématiquement, et jamais laissé à la charge du développeur
qui écrit la requête : un oubli exposerait les données fiscales d'un autre
adhérent.

Le faire remonter jusqu'ici, sous forme d'un paramètre `tenant` sur chaque
méthode, donnerait l'illusion d'une sécurité tout en la rendant contournable —
il suffirait d'oublier l'argument une fois. Voir
`Docs/architecture/05-securite-multitenant.md` et `10-flux-fonctionnels.md` § 5.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.portefeuille.domaine.entites import Entreprise

__all__ = ["DepotEntreprises"]


class DepotEntreprises(Protocol):
    """Source des dossiers du portefeuille, quelle qu'elle soit."""

    def lire(self, niu: str) -> Entreprise:
        """Rend le dossier portant ce NIU, ou lève.

        Jamais `None` : un dossier désigné par son identité fiscale et introuvable
        est une rupture, pas un cas limite. Rendre `None` obligerait chaque
        appelant à s'en souvenir, et le premier qui l'oublierait produirait une
        erreur incompréhensible trois appels plus loin.
        """
        ...

    def lister(self) -> list[Entreprise]:
        """Rend les dossiers visibles par l'appelant.

        « Visibles » et non « tous » : le cloisonnement par tenant et par
        portefeuille est appliqué par la réalisation, pas demandé par l'appelant.
        """
        ...

    def enregistrer(self, entreprise: Entreprise) -> None:
        """Écrit un dossier, création ou mise à jour.

        ⚠️ Les statuts datés ne se **modifient** pas : on ferme la période en
        cours et on en ouvre une nouvelle. Écraser un statut effacerait l'histoire
        du dossier, et avec elle la capacité de contrôler une facture ancienne
        avec le régime qui était alors le sien.
        """
        ...
