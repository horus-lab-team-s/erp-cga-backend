"""Ports du contexte D · Conformité documentaire.

Le moteur a besoin d'un catalogue de règles ; il ne doit pas savoir où il est rangé.
Le port est déclaré dans le cercle interne, l'adaptateur qui le réalise vit dans le
cercle 3 et dépend de cette interface — jamais l'inverse.

Aujourd'hui les règles sont des fichiers YAML versionnés. Demain elles seront en base,
éditées par le fiscaliste via l'écran E11. Seul l'adaptateur changera.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.conformite.domaine.entites import Regle

__all__ = ["DepotRegles"]


class DepotRegles(Protocol):
    """Catalogue des règles de conformité, quelle qu'en soit la source."""

    def charger(self) -> list[Regle]:
        """Rend toutes les règles connues, y compris celles hors de leur période de
        validité : c'est le moteur qui filtre à la date de l'opération, afin qu'un
        contrôle rétroactif reste possible."""
        ...
