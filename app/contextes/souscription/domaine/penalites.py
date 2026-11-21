"""Les pénalités de retard, décidées par un responsable (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA RESPONSABLE A DEMANDÉ (réunion du 4 novembre 2026, demandes 12 et 13)

« Il faut des délais clairs et, AU BESOIN, des pénalités. » « Si le client revient bien après
avec la même proforma, on doit pouvoir lui dire qu'elle a expiré, que les montants ont changé,
et lui appliquer une pénalité ou refacturer. »

⚠️ LE SYSTÈME SIGNALE, UN RESPONSABLE DÉCIDE. « Au besoin » : un dépassement n'est pas une
faute de plein droit (un client malade, un retard du cabinet lui-même). La vue « Retards »
montre le dépassement ; la pénalité s'inflige à la main, avec une explication, et le client
en est prévenu. Une pénalité infligée par erreur se LÈVE (jamais ne s'efface), avec un motif.

⚠️ AUCUN MONTANT N'EST ÉCRIT ICI. Le montant par défaut de chaque motif se fixe dans la règle
des versements ; sans montant par défaut, le responsable le saisit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LIBELLES_DES_MOTIFS",
    "MotifDePenalite",
    "PenaliteInfligee",
    "PenaliteRefusee",
    "lever",
    "total_des_penalites_actives",
]


class PenaliteRefusee(ValueError):
    pass


class MotifDePenalite(StrEnum):
    #: Des statuts transmis et pas validés dans le délai.
    RETARD_VALIDATION_STATUTS = "RETARD_VALIDATION_STATUTS"
    #: Des originaux pas retirés dans le délai.
    RETARD_RETRAIT_ORIGINAUX = "RETARD_RETRAIT_ORIGINAUX"
    #: Une proforma revenue après son expiration.
    PROFORMA_EXPIREE = "PROFORMA_EXPIREE"
    #: Un autre manquement, que l'explication dit.
    AUTRE = "AUTRE"


LIBELLES_DES_MOTIFS: dict[MotifDePenalite, str] = {
    MotifDePenalite.RETARD_VALIDATION_STATUTS: "Retard de validation des statuts",
    MotifDePenalite.RETARD_RETRAIT_ORIGINAUX: "Retard de retrait des originaux",
    MotifDePenalite.PROFORMA_EXPIREE: "Proforma expirée (réémission)",
    MotifDePenalite.AUTRE: "Autre retard",
}


class PenaliteInfligee(BaseModel):
    model_config = ConfigDict(frozen=True)

    numero: int = Field(ge=1)
    motif: MotifDePenalite
    montant: Decimal = Field(gt=0)
    #: Ce que le client lira : le délai, la date, le fait.
    explication: str = Field(min_length=10, max_length=500)
    infligee_par: str = Field(min_length=1)
    infligee_le: datetime
    levee_le: datetime | None = None
    levee_par: str | None = None
    motif_de_levee: str | None = None

    @property
    def active(self) -> bool:
        return self.levee_le is None


def total_des_penalites_actives(penalites) -> Decimal:
    return sum((p.montant for p in penalites if p.active), Decimal(0))


def lever(
    penalites: tuple[PenaliteInfligee, ...],
    numero: int,
    *,
    motif: str,
    par: str,
    le: datetime,
    deja_versees: Decimal,
) -> tuple[PenaliteInfligee, ...]:
    """Lève la pénalité `numero`. ⚠️ On ne lève pas ce que le client a déjà versé : l'argent
    reçu se restitue par la caisse, il ne disparaît pas d'une liste."""
    cible = next((p for p in penalites if p.numero == numero), None)
    if cible is None:
        raise PenaliteRefusee(f"aucune pénalité n° {numero} sur ce dossier.")
    if not cible.active:
        raise PenaliteRefusee(f"la pénalité n° {numero} est déjà levée.")
    reste_actif = total_des_penalites_actives(penalites) - cible.montant
    if deja_versees > reste_actif:
        raise PenaliteRefusee(
            "cette pénalité est déjà réglée : le client a versé son montant. Restituez-le "
            "par la caisse plutôt que de la lever."
        )
    return tuple(
        p.model_copy(update={"levee_le": le, "levee_par": par, "motif_de_levee": motif})
        if p.numero == numero
        else p
        for p in penalites
    )
