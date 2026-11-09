"""Ce qui expire, et quand prévenir (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026, demande 15 : « signaler automatiquement au gestionnaire et au
client les documents ou abonnements expirés ou sur le point de l'être ».

Trois sortes de choses expirent au portefeuille, et seulement celles-là :
* un DOCUMENT à échéance (l'ACF, une attestation CNPS…), daté par le catalogue du cabinet ;
* l'ADHÉSION au centre, quand elle porte une date de fin (elle ouvre l'abattement fiscal) ;
* le MANDAT déclaratif, quand il porte une date de fin (il autorise le cabinet à déposer).

Ce module est PUR : il reçoit les entreprises, rend ce qui expire et le palier à signaler.
Les paliers sont des réglages (`Docs/referentiel/pilotage/expirations.yaml`).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Expiration",
    "ObjetQuiExpire",
    "ReglagesDesExpirations",
    "expirations_de",
    "palier_a_signaler",
]


class ReglagesDesExpirations(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    paliers_jours: tuple[int, ...] = (30, 7, 0)
    horizon_jours: int = Field(default=60, ge=0, le=365)
    expires_affiches_jours: int = Field(default=30, ge=0, le=365)
    source: str = "valeurs par défaut"

    @field_validator("paliers_jours")
    @classmethod
    def _positifs(cls, paliers: tuple[int, ...]) -> tuple[int, ...]:
        if not paliers or any(p < 0 for p in paliers):
            raise ValueError("au moins un palier, en jours positifs ou nuls.")
        return tuple(sorted(set(paliers), reverse=True))


class ObjetQuiExpire(StrEnum):
    DOCUMENT = "DOCUMENT"
    ADHESION = "ADHESION"
    MANDAT = "MANDAT"


class Expiration(BaseModel):
    model_config = ConfigDict(frozen=True)

    objet: ObjetQuiExpire
    dossier: str
    denomination: str
    libelle: str
    expire_le: date
    #: Négatif une fois expiré.
    jours_restants: int
    #: Stable : la même chose, à la même date, porte la même clé (garde des doublons).
    cle: str


def expirations_de(entreprise, a_la_date: date) -> list[Expiration]:
    """Ce qui expire dans ce dossier : documents en vigueur, adhésion et mandat datés."""
    from app.contextes.portefeuille.contrats import documents_en_vigueur

    def expiration(objet, libelle, fin: date, cle_objet: str) -> Expiration:
        return Expiration(
            objet=objet,
            dossier=entreprise.niu,
            denomination=entreprise.denomination,
            libelle=libelle,
            expire_le=fin,
            jours_restants=(fin - a_la_date).days,
            cle=f"{entreprise.niu}:{cle_objet}:{fin.isoformat()}",
        )

    sortie = [
        expiration(ObjetQuiExpire.DOCUMENT, d.libelle, d.expire_le, d.code)
        for d in documents_en_vigueur(entreprise.documents_a_echeance)
    ]
    # La DERNIÈRE période datée de chaque sorte : une adhésion renouvelée ne signale plus
    # l'ancienne. La borne `fin` est exclue (`[debut, fin[`) : le dernier jour couvert est la
    # veille, mais c'est `fin` que l'administration lira comme date de fin.
    for objet, periodes, nom in (
        (ObjetQuiExpire.ADHESION, entreprise.adhesions, "Adhésion au centre de gestion agréé"),
        (ObjetQuiExpire.MANDAT, entreprise.mandats, "Mandat déclaratif"),
    ):
        datees = [p for p in periodes if p.fin is not None]
        if not datees:
            continue  # sans date de fin, rien n'expire
        derniere = max(datees, key=lambda p: p.fin)
        # Renouvelée (une période commence à sa fin ou après) : rien à signaler.
        if any(p.debut >= derniere.fin for p in periodes if p is not derniere):
            continue
        sortie.append(expiration(objet, nom, derniere.fin, objet.value))
    return sorted(sortie, key=lambda e: e.expire_le)


def palier_a_signaler(
    jours_restants: int, paliers: Iterable[int], deja: Iterable[int]
) -> int | None:
    """Le palier atteint le plus proche, s'il n'a pas encore été signalé.

    Un document enregistré à cinq jours de son expiration reçoit le palier de 7, pas celui
    de 30 : prévenir « dans un mois » d'une chose qui expire vendredi serait faux.
    """
    atteints = [p for p in paliers if jours_restants <= p]
    if not atteints:
        return None
    plus_proche = min(atteints)
    return None if plus_proche in set(deja) else plus_proche
