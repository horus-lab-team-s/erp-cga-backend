"""Ports du contexte D · Conformité documentaire.

Le moteur a besoin d'un catalogue de règles ; il ne doit pas savoir où il est rangé.
Le port est déclaré dans le cercle interne, l'adaptateur qui le réalise vit dans le
cercle 3 et dépend de cette interface — jamais l'inverse.

Aujourd'hui les règles sont des fichiers YAML versionnés. Demain elles seront en base,
éditées par le fiscaliste via l'écran E11. Seul l'adaptateur changera.
"""

from __future__ import annotations

from typing import Protocol

from app.contextes.conformite.domaine.ecarts import EcartDeConstat
from app.contextes.conformite.domaine.entites import Regle
from app.contextes.conformite.domaine.regles_du_cabinet import PropositionDeRegle

__all__ = [
    "DepotEcarts",
    "DepotPropositions",
    "DepotRegles",
]


class DepotRegles(Protocol):
    """Catalogue des règles de conformité, quelle qu'en soit la source."""

    def charger(self) -> list[Regle]:
        """Rend toutes les règles connues, y compris celles hors de leur période de
        validité : c'est le moteur qui filtre à la date de l'opération, afin qu'un
        contrôle rétroactif reste possible."""
        ...


class DepotEcarts(Protocol):
    """Les décisions d'écart d'un cabinet (pas 92).

    ⚠️ On n'y supprime rien : un écart refusé ou levé reste lisible. Le port n'offre
    donc aucune méthode d'effacement, et c'est délibéré.
    """

    def pour_la_piece(self, dossier: str, reference_document: str) -> list[EcartDeConstat]:
        """Tous les écarts d'une pièce, toutes décisions confondues, du plus ancien au
        plus récent. La pièce est nommée **avec** son dossier : deux fournisseurs
        peuvent émettre la même référence de facture à deux clients différents."""
        ...

    def toutes(self) -> list[EcartDeConstat]:
        """Pas 99 : tous les écarts du cabinet, pour le journal des dérogations."""
        ...

    def en_attente(self) -> list[EcartDeConstat]:
        """Les écarts qui attendent un second regard, du plus ancien au plus récent."""
        ...

    def enregistrer(self, ecart: EcartDeConstat) -> None:
        """Insère ou remplace l'écart portant cet identifiant."""
        ...


#: ⚠️ Pas 104 : ce dépôt existait depuis le pas 97 **sans aucun port**. Rien ne confrontait
#: ses réalisations en mémoire et SQL.
class DepotPropositions(Protocol):
    """Les règles construites par le cabinet (pas 97), refusées et retirées comprises."""

    def toutes(self) -> list[PropositionDeRegle]:
        """De la plus ancienne à la plus récente."""
        ...

    def trouver(self, identifiant: str) -> PropositionDeRegle | None: ...

    def enregistrer(self, proposition: PropositionDeRegle) -> None: ...
