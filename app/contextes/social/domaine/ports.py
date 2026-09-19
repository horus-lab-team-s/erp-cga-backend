"""Ports du contexte G · Social et paie.

Deux dépôts et non un : les salariés et les contrats ont des durées de vie
distinctes. Un salarié reste au fichier après son départ — la CNPS peut le
réclamer des années plus tard — tandis que ses contrats se ferment. Les mêler
obligerait à charger l'histoire complète d'un salarié pour en lire le nom.

Comme partout, le cloisonnement par locataire n'apparaît pas dans l'interface :
il est appliqué par la réalisation, systématiquement.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.contextes.social.domaine.entites import Contrat, Salarie

__all__ = ["DepotContrats", "DepotSalaries", "SalarieIntrouvable"]


class SalarieIntrouvable(LookupError):
    """Aucun salarié ne porte ce matricule chez ce dossier."""


class DepotSalaries(Protocol):
    """Source des salariés, quelle qu'elle soit."""

    def lire(self, matricule: str) -> Salarie:
        """Rend le salarié portant ce matricule, ou lève."""
        ...

    def du_dossier(self, entreprise: str) -> list[Salarie]:
        """Les salariés d'un dossier, actifs ou non.

        « Actifs ou non » est délibéré : le fichier du personnel ne se purge pas.
        La sélection de ceux qui travaillent à une date se fait sur les contrats,
        parce que c'est le contrat qui porte l'intervalle — pas le salarié.
        """
        ...

    def enregistrer(self, salarie: Salarie) -> None: ...


class DepotContrats(Protocol):
    """Source des contrats de travail."""

    def du_salarie(self, matricule: str) -> list[Contrat]:
        """Tous les contrats d'un salarié, du plus ancien au plus récent."""
        ...

    def du_dossier(self, entreprise: str, a_la_date: date | None = None) -> list[Contrat]:
        """Les contrats d'un dossier, éventuellement filtrés à une date.

        ⚠️ `a_la_date` filtre sur l'intervalle `[debut, fin[`, jamais sur un
        drapeau « actif ». Un drapeau devrait être maintenu à jour par quelqu'un,
        et ce quelqu'un l'oublierait le mois où un salarié part — la paie
        continuerait de le payer.
        """
        ...

    def enregistrer(self, contrat: Contrat) -> None:
        """Écrit un contrat.

        ⚠️ Un contrat en vigueur ne se **modifie** pas quand les conditions
        changent : on le ferme et on en ouvre un nouveau. Écraser le salaire
        effacerait l'histoire, et la paie de mars se recalculerait avec le
        salaire d'avril — c'est la même discipline que les statuts datés du
        portefeuille.
        """
        ...
