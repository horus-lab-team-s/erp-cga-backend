"""Le journal de caisse : tout ce qui a été encaissé, par axe (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ

« La caissière doit avoir le suivi des versements effectués via Tara, et un report
avec le motif de versement, pour un suivi financier global par axe : inscription,
paiement d'honoraires, paiement CNPS… Avec Tara, le rapprochement se fait
automatiquement ; seuls les versements manuels sont saisis par la caissière, et elle
doit être dans l'axe constaté. »

Le rapprochement Tara et l'axe imposé au guichet existaient déjà : chaque versement
se constate sur l'objet qu'il règle (une proforma, une échéance, un appel de fonds),
et l'objet porte son axe. Ce qui manquait, c'est la lecture d'ensemble. Ce module la
compose ; il ne stocke rien.

⚠️ LES FONDS DE TIERS SONT UN AXE À PART, ET LE TOTAL LE DIT. Ils ne sont pas au
cabinet : ils sont dus à l'administration. Le total des encaissements du cabinet les
exclut ; ils ont leur propre total (décision du 14 octobre 2026).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AXE_DU_SERVICE",
    "AxeDeVersement",
    "FamilleDeVersement",
    "JournalDeCaisse",
    "LigneDuJournal",
    "TotalParAxe",
    "composer_le_journal",
]


class FamilleDeVersement(StrEnum):
    """Ce qu'un droit de la matrice ouvre à la vue (`VOIR_VERSEMENTS_…`)."""

    COMMERCE = "COMMERCE"
    HONORAIRES = "HONORAIRES"
    FONDS_DE_TIERS = "FONDS_DE_TIERS"


class AxeDeVersement(StrEnum):
    INSCRIPTION = "INSCRIPTION"
    CREATION = "CREATION"
    PRESTATION = "PRESTATION"
    ABONNEMENT_EN_LIGNE = "ABONNEMENT_EN_LIGNE"
    HONORAIRES = "HONORAIRES"
    TVA = "TVA"
    CNPS = "CNPS"
    IRPP_SALAIRES = "IRPP_SALAIRES"

    @property
    def famille(self) -> FamilleDeVersement:
        if self is AxeDeVersement.HONORAIRES:
            return FamilleDeVersement.HONORAIRES
        if self in (AxeDeVersement.TVA, AxeDeVersement.CNPS, AxeDeVersement.IRPP_SALAIRES):
            return FamilleDeVersement.FONDS_DE_TIERS
        return FamilleDeVersement.COMMERCE


#: Le service d'une proforma, rangé sur son axe. Un service inconnu est une prestation.
AXE_DU_SERVICE: dict[str, AxeDeVersement] = {
    "ADHESION": AxeDeVersement.INSCRIPTION,
    "CREATION": AxeDeVersement.CREATION,
}


class LigneDuJournal(BaseModel):
    model_config = ConfigDict(frozen=True)

    le: datetime
    axe: AxeDeVersement
    #: Le motif du versement, lisible : « Honoraires, mars 2026 », « Proforma PF-… ».
    objet: str
    client: str
    #: Le NIU, ou la référence du dossier commercial avant qu'il y en ait un.
    dossier: str
    montant: Decimal
    #: `MOBILE_MONEY` (Tara, rapproché tout seul) ou `GUICHET` (constaté par la caisse).
    canal: str
    reference: str | None = None


class TotalParAxe(BaseModel):
    axe: AxeDeVersement
    nombre: int
    montant: Decimal
    dont_mobile_money: Decimal
    dont_guichet: Decimal


class JournalDeCaisse(BaseModel):
    du: date
    au: date
    lignes: list[LigneDuJournal]
    totaux: list[TotalParAxe]
    #: Ce qui revient au cabinet : tout, hors fonds de tiers.
    total_du_cabinet: Decimal
    #: Ce qui est dû à l'administration, tenu à part.
    total_des_fonds_de_tiers: Decimal
    #: Les familles que le lecteur voit : une famille retirée par la matrice n'est ni
    #: listée ni totalisée, et l'écran dit qu'elle est masquée.
    familles_visibles: list[FamilleDeVersement]


def composer_le_journal(
    lignes: Iterable[LigneDuJournal],
    *,
    du: date,
    au: date,
    familles: Iterable[FamilleDeVersement],
    jour_de: Callable[[datetime], date],
) -> JournalDeCaisse:
    """Filtre sur la période (`jour_de` : l'instant, en jour de Douala) et les familles
    visibles, trie du plus récent au plus ancien, totalise par axe."""
    visibles = set(familles)
    retenues = sorted(
        (
            ligne
            for ligne in lignes
            if ligne.axe.famille in visibles and du <= jour_de(ligne.le) <= au
        ),
        key=lambda ligne: ligne.le,
        reverse=True,
    )
    totaux: list[TotalParAxe] = []
    for axe in AxeDeVersement:
        de_l_axe = [ligne for ligne in retenues if ligne.axe is axe]
        if not de_l_axe:
            continue
        totaux.append(
            TotalParAxe(
                axe=axe,
                nombre=len(de_l_axe),
                montant=sum((ligne.montant for ligne in de_l_axe), Decimal(0)),
                dont_mobile_money=sum(
                    (ligne.montant for ligne in de_l_axe if ligne.canal == "MOBILE_MONEY"),
                    Decimal(0),
                ),
                dont_guichet=sum(
                    (ligne.montant for ligne in de_l_axe if ligne.canal != "MOBILE_MONEY"),
                    Decimal(0),
                ),
            )
        )
    tiers = FamilleDeVersement.FONDS_DE_TIERS
    return JournalDeCaisse(
        du=du,
        au=au,
        lignes=retenues,
        totaux=totaux,
        total_du_cabinet=sum((t.montant for t in totaux if t.axe.famille is not tiers), Decimal(0)),
        total_des_fonds_de_tiers=sum(
            (t.montant for t in totaux if t.axe.famille is tiers), Decimal(0)
        ),
        familles_visibles=sorted(visibles),
    )
