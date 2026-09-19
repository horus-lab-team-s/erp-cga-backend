"""Ports du contexte F · Obligations et déclarations.

**Inversion de dépendance.** Le cas d'usage a besoin du catalogue des obligations
et du registre des instances ; il ne doit pas savoir où ils sont rangés.

Note de conception : les `TypeObligation` appartiennent, selon le dossier
d'architecture, au contexte A · Référentiel — leur périodicité et leur formule
d'échéance viennent du CGI et changent avec lui. Ce contexte déclare malgré tout
son propre port, comme le contexte E le fait pour le plan comptable : le métier
exprime son besoin, l'adaptateur sait où le satisfaire.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.contextes.obligations.domaine.echeances import TypeObligation
from app.contextes.obligations.domaine.obligations import ObligationInstance

__all__ = ["DepotObligations", "DepotTypesObligation"]


class DepotTypesObligation(Protocol):
    """Catalogue des obligations déclaratives, quelle qu'en soit la source."""

    def charger(self, a_la_date: date) -> list[TypeObligation]:
        """Rend les types en vigueur à cette date.

        La date n'est pas décorative : une loi de finances peut créer une
        obligation, en supprimer une, ou changer un jour limite. Un échéancier
        régénéré pour 2024 doit employer le catalogue de 2024.
        """
        ...


class DepotObligations(Protocol):
    """Registre des obligations d'un portefeuille."""

    def enregistrer(self, obligation: ObligationInstance) -> None:
        ...

    def lister(
        self, entreprise: str | None = None, exercice: str | None = None
    ) -> list[ObligationInstance]:
        """Rend les obligations visibles par l'appelant.

        « Visibles » et non « toutes » : le cloisonnement par tenant et par
        portefeuille est appliqué par la réalisation, pas demandé par l'appelant.
        Voir le port `DepotEntreprises` du contexte B pour le raisonnement complet.
        """
        ...
