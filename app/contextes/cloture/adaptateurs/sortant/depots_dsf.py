"""Les versions de la DSF : en base dans une requête, en mémoire sinon."""

from __future__ import annotations

from typing import Any

from app.contextes.cloture.adaptateurs.sortant.tables import TableDeclarationDSF
from app.contextes.cloture.domaine.declaration_dsf import DeclarationDSF
from app.infrastructure.depot_document import DepotDocument

__all__ = ["DepotDSFMemoire", "DepotDSFSql", "dsf_en_memoire"]


class DepotDSFSql(DepotDocument[DeclarationDSF]):
    _table = TableDeclarationDSF
    _entite = DeclarationDSF

    def _cle(self, entite: DeclarationDSF) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: DeclarationDSF) -> dict[str, Any]:
        return {
            "entreprise": entite.entreprise,
            "exercice": entite.exercice,
            "version": entite.version,
            "etat": entite.etat.value,
            "preparee_le": entite.preparee_le,
        }

    def de_l_exercice(self, entreprise: str, exercice: str) -> list[DeclarationDSF]:
        """Les versions, de la plus ancienne à la plus récente."""
        return sorted(
            self._tous(
                self._requete().where(
                    TableDeclarationDSF.entreprise == entreprise,
                    TableDeclarationDSF.exercice == exercice,
                )
            ),
            key=lambda d: d.version,
        )

    def enregistrer(self, dsf: DeclarationDSF) -> None:
        self._poser(dsf)


class DepotDSFMemoire:
    def __init__(self) -> None:
        self._versions: dict[str, DeclarationDSF] = {}

    def de_l_exercice(self, entreprise: str, exercice: str) -> list[DeclarationDSF]:
        return sorted(
            (
                d
                for d in self._versions.values()
                if d.entreprise == entreprise and d.exercice == exercice
            ),
            key=lambda d: d.version,
        )

    def enregistrer(self, dsf: DeclarationDSF) -> None:
        self._versions[dsf.identifiant] = dsf


_MEMOIRE = DepotDSFMemoire()


def dsf_en_memoire() -> DepotDSFMemoire:
    return _MEMOIRE
