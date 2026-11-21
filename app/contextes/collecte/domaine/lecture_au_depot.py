"""La lecture préparée au dépôt (18 octobre 2026, point 13).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ

« Notre agent d'analyse de conformité des factures se déclenche directement quand il
y a un upload côté client. Il n'est pas bloquant, il est au choix de l'admin comme
fonctionnalité, et si l'adhérent le veut bien. »

TROIS CONDITIONS, TOUTES NÉCESSAIRES

* le cabinet a activé la fonctionnalité (`ReglageDeLecture`) ;
* l'adhérent a donné son accord pour son dossier (`ConsentementDeLecture`) : son
  document part chez Anthropic, et c'est à lui d'en décider ;
* la clé de l'agent est posée. Sans elle, rien ne sort, rien ne casse : la saisie
  reste ouverte, comme toujours.

NON BLOQUANT, PAR CONSTRUCTION

Le dépôt ne l'attend pas : un travail de fond lit les pièces reçues après coup et
range la proposition à côté de la pièce. Une panne de l'agent laisse une lecture avec
son incident, jamais une pièce refusée.

⚠️ UNE PROPOSITION, PAS UN CONTRÔLE. Le contrôle de conformité qui fait foi se fait sur
une pièce **identifiée**, après confirmation humaine (`controle_a_reception.py`, qui
refuse de juger des valeurs supposées). Ce que l'agent prépare ici, ce sont les champs
préremplis et des **points à vérifier**, signalés au collaborateur, jamais un verdict.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ConsentementDeLecture",
    "LecturePreparee",
    "ReglageDeLecture",
    "ValeurPreparee",
    "points_a_verifier",
]


class ReglageDeLecture(BaseModel):
    """La fonctionnalité, pour le cabinet : activée ou non, par qui, pourquoi."""

    model_config = ConfigDict(frozen=True)

    actif: bool
    motif: str = Field(min_length=5, max_length=500)
    regle_par: str = Field(min_length=1)
    regle_le: datetime


class ConsentementDeLecture(BaseModel):
    """L'accord de l'adhérent, pour son dossier. Il se retire comme il se donne."""

    model_config = ConfigDict(frozen=True)

    niu: str = Field(min_length=1)
    accorde: bool
    par: str = Field(min_length=1)
    le: datetime


class ValeurPreparee(BaseModel):
    model_config = ConfigDict(frozen=True)

    champ: str
    valeur: Any
    confiance: Decimal
    a_relire: bool


class LecturePreparee(BaseModel):
    model_config = ConfigDict(frozen=True)

    piece: str = Field(min_length=1)
    entreprise: str = Field(min_length=1)
    lue_le: datetime
    moteur: str
    incident: str | None = None
    valeurs: list[ValeurPreparee] = Field(default_factory=list)
    points_a_verifier: list[str] = Field(default_factory=list)


def _montant(valeurs: dict[str, Any], champ: str) -> Decimal | None:
    brut = valeurs.get(champ)
    if brut in (None, ""):
        return None
    try:
        return Decimal(str(brut).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def points_a_verifier(valeurs: Iterable[ValeurPreparee]) -> list[str]:
    """Ce que le collaborateur doit regarder en premier. Déterministe : l'agent lit, ces
    règles constatent ; elles ne jugent pas la déductibilité, qui exige la pièce confirmée."""
    lues = {v.champ: v.valeur for v in valeurs}
    points: list[str] = []
    if not lues.get("niu_emetteur"):
        points.append("NIU du fournisseur absent : sans lui, la TVA n'est pas déductible.")
    if not lues.get("date_emission"):
        points.append("Date de la facture non lue.")
    if not lues.get("reference_document"):
        points.append("Numéro de facture non lu.")
    ht, tva, ttc = (_montant(lues, c) for c in ("montant_ht", "montant_tva", "montant_ttc"))
    if ht is not None and tva is not None and ttc is not None and ht + tva != ttc:
        points.append(f"HT + TVA = {ht + tva} ne fait pas le TTC lu ({ttc}).")
    if ht is not None and tva is not None and ht > 0 and tva > 0:
        taux = (tva / ht * 100).quantize(Decimal("0.01"))
        if abs(taux - Decimal("19.25")) > Decimal("0.5"):
            points.append(f"TVA à {taux} % du HT : le taux général est 19,25 %.")
    relire = sorted(v.champ for v in valeurs if v.a_relire)
    if relire:
        points.append(f"À relire sur le document : {', '.join(relire)}.")
    return points
