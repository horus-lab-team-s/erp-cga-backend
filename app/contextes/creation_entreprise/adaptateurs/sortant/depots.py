"""Les dépôts du contexte I — mémoire et PostgreSQL.

Les deux réalisations vivent dans le même module parce qu'elles doivent rester
d'accord : le filtre `immobiles_avant` a un sens métier — « les dossiers qui
dorment » — et deux modules séparés le laisseraient diverger sans que rien ne le
signale. `tests/test_conformite_des_ports.py` vérifie qu'elles répondent pareil.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import and_

from app.contextes.creation_entreprise.adaptateurs.sortant.tables import TableDossierCreation
from app.contextes.creation_entreprise.domaine.entites import DossierCreation, EtapeCreation
from app.contextes.creation_entreprise.domaine.ports import DossierCreationIntrouvable
from app.infrastructure.depot_document import DepotDocument

__all__ = ["DepotDossiersCreationMemoire", "DepotDossiersCreationSql"]


class DepotDossiersCreationMemoire:
    """Réalisation en mémoire, pour les tests et le mode de démonstration."""

    def __init__(self, dossiers: list[DossierCreation] | None = None) -> None:
        self._dossiers: dict[str, DossierCreation] = {
            d.reference: d for d in (dossiers or [])
        }

    def lire(self, reference: str) -> DossierCreation:
        try:
            return self._dossiers[reference]
        except KeyError as absence:
            raise DossierCreationIntrouvable(
                f"aucun dossier de création portant la référence {reference}"
            ) from absence

    def lister(
        self, *, etape: EtapeCreation | None = None, immobiles_avant: date | None = None
    ) -> list[DossierCreation]:
        retenus = list(self._dossiers.values())
        if etape is not None:
            retenus = [d for d in retenus if d.etape is etape]
        if immobiles_avant is not None:
            retenus = [d for d in retenus if d.immobile_depuis < immobiles_avant]
        # Le plus ancien mouvement d'abord : le pipeline se lit par urgence, pas
        # par ordre d'arrivée. Un dossier qui dort depuis trois semaines doit se
        # présenter avant celui d'hier.
        return sorted(retenus, key=lambda d: (d.immobile_depuis, d.reference))

    def enregistrer(self, dossier: DossierCreation) -> None:
        self._verifier_les_jalons(dossier)
        self._dossiers[dossier.reference] = dossier

    def _verifier_les_jalons(self, dossier: DossierCreation) -> None:
        """Refuse une écriture qui perdrait des jalons.

        Le même garde-fou que l'histoire amputée du portefeuille, pour la même
        raison : un jalon effacé, c'est la date d'un dépôt perdue, donc le délai
        que le guichet a tenu ou non — le seul chiffre que le cabinet puisse lui
        opposer.
        """
        ancien = self._dossiers.get(dossier.reference)
        if ancien is not None and len(dossier.jalons) < len(ancien.jalons):
            raise ValueError(
                f"le dossier {dossier.reference} perdrait "
                f"{len(ancien.jalons) - len(dossier.jalons)} jalon(s) : un jalon "
                "s'ajoute, il ne se réécrit pas."
            )


class DepotDossiersCreationSql(DepotDocument[DossierCreation]):
    """Réalisation PostgreSQL de `DepotDossiersCreation`."""

    _table = TableDossierCreation
    _entite = DossierCreation

    def _cle(self, entite: DossierCreation) -> dict[str, Any]:
        return {"reference": entite.reference}

    def _colonnes(self, entite: DossierCreation) -> dict[str, Any]:
        return {
            "etape": entite.etape.value,
            # Recalculée, jamais reprise : voir l'en-tête de `tables.py`.
            "immobile_depuis": entite.immobile_depuis,
            "denomination_souhaitee": entite.denomination_souhaitee,
            "converti_en": entite.converti_en,
        }

    def lire(self, reference: str) -> DossierCreation:
        trouve = self._premier(
            self._requete().where(TableDossierCreation.reference == reference)
        )
        if trouve is None:
            raise DossierCreationIntrouvable(
                f"aucun dossier de création {reference} chez le locataire {self.locataire}"
            )
        return trouve

    def lister(
        self, *, etape: EtapeCreation | None = None, immobiles_avant: date | None = None
    ) -> list[DossierCreation]:
        conditions = []
        if etape is not None:
            conditions.append(TableDossierCreation.etape == etape.value)
        if immobiles_avant is not None:
            conditions.append(TableDossierCreation.immobile_depuis < immobiles_avant)
        requete = self._requete()
        if conditions:
            requete = requete.where(and_(*conditions))
        return self._tous(
            requete.order_by(
                TableDossierCreation.immobile_depuis, TableDossierCreation.reference
            )
        )

    def enregistrer(self, dossier: DossierCreation) -> None:
        self._verifier_les_jalons(dossier)
        self._poser(dossier)

    def _verifier_les_jalons(self, dossier: DossierCreation) -> None:
        ancien = self._premier(
            self._requete().where(TableDossierCreation.reference == dossier.reference)
        )
        if ancien is not None and len(dossier.jalons) < len(ancien.jalons):
            raise ValueError(
                f"le dossier {dossier.reference} perdrait "
                f"{len(ancien.jalons) - len(dossier.jalons)} jalon(s) : un jalon "
                "s'ajoute, il ne se réécrit pas."
            )
