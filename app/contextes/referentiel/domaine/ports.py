"""Ports du contexte A · Référentiel normatif.

**Inversion de dépendance.** Le cas d'usage a besoin de paramètres ; il ne doit pas
savoir d'où ils viennent. Le port est déclaré ici, dans le cercle le plus interne ;
l'adaptateur qui le réalise vit dans le cercle 3 et dépend de cette interface —
jamais l'inverse.

Concrètement : aujourd'hui les paramètres sont lus dans des fichiers YAML versionnés
en Git ; demain ils vivront dans une table PostgreSQL éditable par le fiscaliste.
Ce jour-là, seul l'adaptateur change. Le service de lecture, les règles et les tests
ne bougent pas d'une ligne.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.referentiel.domaine.entites import Parametre

__all__ = ["DepotParametres"]


class DepotParametres(Protocol):
    """Source des paramètres fiscaux, quelle qu'elle soit."""

    def charger(self) -> list[Parametre]:
        """Rend l'intégralité des paramètres connus.

        Le chargement est global et non paginé : le référentiel compte quelques
        dizaines d'entrées et il est lu à chaque contrôle de facture. Le garder en
        mémoire coûte moins qu'un aller-retour par lecture.
        """
        ...
