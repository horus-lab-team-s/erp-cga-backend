"""Ports du contexte L · Vitrine publique.

**Inversion de dépendance.** Le cas d'usage a besoin du contenu ; il ne doit pas
savoir d'où il vient. Le port est déclaré ici, dans le cercle le plus interne ;
l'adaptateur qui le réalise vit dans le cercle 3 et dépend de cette interface —
jamais l'inverse.

Concrètement : aujourd'hui le contenu est lu dans des fichiers YAML versionnés en
Git, que le cabinet corrige à la main. Demain il vivra dans une table PostgreSQL
éditée depuis un écran d'administration. Ce jour-là, seul l'adaptateur change ; le
service de lecture, les routes et les tests ne bougent pas d'une ligne.

C'est la même discipline que le contexte A · Référentiel, et pour la même raison :
la source d'une donnée est un détail technique, sa lecture est un cas d'usage.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.vitrine.domaine.entites import Annonce, Article, Institution

__all__ = ["DepotContenuVitrine"]


class DepotContenuVitrine(Protocol):
    """Source du contenu éditorial, quelle qu'elle soit.

    Les trois chargements sont **globaux et non paginés**. Le contenu se compte en
    dizaines d'entrées, il est lu à chaque rendu de page, et le garder en mémoire
    coûte moins qu'un aller-retour par lecture. Le jour où le blog compterait des
    milliers d'articles, ce port changerait de forme — c'est précisément ce qu'un
    port permet de faire sans toucher au reste.
    """

    def charger_articles(self) -> list[Article]:
        """Rend tous les articles connus, dans l'ordre du fichier source.

        Le tri chronologique n'est pas fait ici : c'est une décision de lecture,
        elle appartient au cas d'usage. Un dépôt qui trierait imposerait son ordre
        à tous les appelants.
        """
        ...

    def charger_annonces(self) -> list[Annonce]:
        """Rend toutes les annonces, y compris périmées et à venir.

        Le filtrage par date appartient au cas d'usage : le dépôt ne sait pas quel
        jour on est, et n'a pas à le savoir.
        """
        ...

    def charger_institutions(self) -> list[Institution]:
        """Rend les institutions, dans l'ordre d'affichage voulu par le cabinet."""
        ...
