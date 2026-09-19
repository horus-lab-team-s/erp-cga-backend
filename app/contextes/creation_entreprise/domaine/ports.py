"""Ports du contexte I · Création d'entreprise.

Le cas d'usage a besoin des dossiers de création ; il ne doit pas savoir où ils
sont rangés. Comme partout, le cloisonnement par locataire n'apparaît pas dans
l'interface : il est appliqué par la réalisation, systématiquement, et jamais
laissé à la charge de l'appelant.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.contextes.creation_entreprise.domaine.entites import DossierCreation, EtapeCreation

__all__ = ["DepotDossiersCreation", "DossierCreationIntrouvable"]


class DossierCreationIntrouvable(LookupError):
    """Aucun dossier de création ne porte cette référence.

    Une erreur, jamais `None` : un dossier désigné par sa référence et absent est
    une rupture. Rendre `None` obligerait chaque appelant à s'en souvenir, et le
    premier qui l'oublierait produirait une panne trois appels plus loin, là où
    plus personne ne pense à la création d'entreprise.
    """


class DepotDossiersCreation(Protocol):
    """Source des dossiers de création, quelle qu'elle soit."""

    def lire(self, reference: str) -> DossierCreation:
        """Rend le dossier portant cette référence, ou lève."""
        ...

    def lister(
        self, *, etape: EtapeCreation | None = None, immobiles_avant: date | None = None
    ) -> list[DossierCreation]:
        """Rend les dossiers visibles par l'appelant.

        Les deux filtres servent le même écran — le pipeline — et sont donc au
        port plutôt que dans l'appelant : filtrer en mémoire obligerait à charger
        tout le pipeline pour n'en afficher qu'une colonne, ce qui tient tant que
        le cabinet fait dix créations par an et cesse de tenir à cent.

        `immobiles_avant` rend les dossiers dont le dernier mouvement est
        antérieur à cette date. C'est la requête de la relance.
        """
        ...

    def enregistrer(self, dossier: DossierCreation) -> None:
        """Écrit un dossier, création ou mise à jour.

        ⚠️ Les jalons ne se **modifient** pas : on en ajoute. Réécrire un jalon
        effacerait la date d'un dépôt, donc le délai que le guichet a tenu ou non
        — le seul chiffre que le cabinet puisse lui opposer.
        """
        ...
