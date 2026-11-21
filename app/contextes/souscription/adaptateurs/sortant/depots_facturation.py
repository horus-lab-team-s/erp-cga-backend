"""L'identité fiscale du cabinet et les factures émises : en base dans une requête, en
mémoire sinon (18 octobre 2026). Voir `domaine/facturation.py`."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select

from app.contextes.souscription.adaptateurs.sortant.tables import (
    TableFactureDHonoraires,
    TableIdentiteFiscaleDuCabinet,
)
from app.contextes.souscription.domaine.facturation import (
    FactureDHonoraires,
    IdentiteFiscaleDuCabinet,
)
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.depot_document import DepotDocument
from app.partage.locataire import courant

__all__ = ["depot_des_factures", "depot_de_l_identite_fiscale"]


class _IdentiteSql(DepotDocument[IdentiteFiscaleDuCabinet]):
    _table = TableIdentiteFiscaleDuCabinet
    _entite = IdentiteFiscaleDuCabinet

    def _cle(self, entite: IdentiteFiscaleDuCabinet) -> dict[str, Any]:
        return {"locataire": self.locataire}

    def _colonnes(self, entite: IdentiteFiscaleDuCabinet) -> dict[str, Any]:
        return {}

    def lire(self) -> IdentiteFiscaleDuCabinet:
        return self._premier(self._requete()) or IdentiteFiscaleDuCabinet()

    def enregistrer(self, identite: IdentiteFiscaleDuCabinet) -> None:
        self._poser(identite)


class _FacturesSql(DepotDocument[FactureDHonoraires]):
    _table = TableFactureDHonoraires
    _entite = FactureDHonoraires

    def _cle(self, entite: FactureDHonoraires) -> dict[str, Any]:
        return {"numero": entite.numero}

    def _colonnes(self, entite: FactureDHonoraires) -> dict[str, Any]:
        return {"niu": entite.niu, "periode": entite.periode, "annee": entite.emise_le.year}

    def de_l_echeance(self, niu: str, periode: date) -> FactureDHonoraires | None:
        return self._premier(
            self._requete().where(
                TableFactureDHonoraires.niu == niu, TableFactureDHonoraires.periode == periode
            )
        )

    def emises_en(self, annee: int) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(TableFactureDHonoraires)
                .where(
                    TableFactureDHonoraires.locataire == self.locataire,
                    TableFactureDHonoraires.annee == annee,
                )
            )
            or 0
        )

    def enregistrer(self, facture: FactureDHonoraires) -> None:
        self._poser(facture)


class _IdentiteMemoire:
    def __init__(self) -> None:
        self._identite = IdentiteFiscaleDuCabinet()

    def lire(self) -> IdentiteFiscaleDuCabinet:
        return self._identite

    def enregistrer(self, identite: IdentiteFiscaleDuCabinet) -> None:
        self._identite = identite


class _FacturesMemoire:
    def __init__(self) -> None:
        self._factures: dict[str, FactureDHonoraires] = {}

    def de_l_echeance(self, niu: str, periode: date) -> FactureDHonoraires | None:
        return next(
            (f for f in self._factures.values() if f.niu == niu and f.periode == periode), None
        )

    def emises_en(self, annee: int) -> int:
        return sum(1 for f in self._factures.values() if f.emise_le.year == annee)

    def enregistrer(self, facture: FactureDHonoraires) -> None:
        self._factures[facture.numero] = facture


_IDENTITE = _IdentiteMemoire()
_FACTURES = _FacturesMemoire()


def depot_de_l_identite_fiscale() -> _IdentiteSql | _IdentiteMemoire:
    session = session_de_travail()
    return _IDENTITE if session is None else _IdentiteSql(session, courant())


def depot_des_factures() -> _FacturesSql | _FacturesMemoire:
    session = session_de_travail()
    return _FACTURES if session is None else _FacturesSql(session, courant())
