"""Les fonds qu'un adhérent confie au cabinet pour payer l'administration à sa place.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DÉCIDÉ, LE 14 OCTOBRE 2026

Pour la TVA et la CNPS, deux voies, au choix de l'adhérent : il paie lui-même
l'administration et envoie sa quittance ; ou, s'il a signé un mandat de paiement
(`MandatDePaiement`, au portefeuille), il remet les fonds au cabinet, par mobile
money ou au guichet, et le cabinet reverse puis porte la quittance.

UN APPEL DE FONDS, TROIS ÉTATS, DANS CET ORDRE ET DANS AUCUN AUTRE

    A_REGLER       le cabinet a arrêté le montant de la période ; l'adhérent le doit
    FONDS_RECUS    l'argent est chez le cabinet ; l'administration n'est PAS payée
    REVERSE        le cabinet a payé l'administration, et porte sa quittance

⚠️ CE NE SONT PAS DES HONORAIRES. Cet argent n'est pas au cabinet : il le détient
pour un tiers, l'administration. Il vit dans son propre agrégat, sa propre table,
et sa propre ligne de caisse, pour qu'aucun total ne les additionne jamais.

⚠️ « FONDS REÇUS » N'EST PAS « PAYÉE ». L'adhérent lit « le cabinet a reçu vos
fonds et règle l'administration », jamais « payé », tant que la quittance de
l'administration n'est pas portée. Un retard de reversement est une pénalité pour
l'adhérent, et il doit pouvoir le voir.

⚠️ LE MONTANT EST ARRÊTÉ PAR UN HUMAIN. Hors TVA, rien ne calcule un montant
définitif (Q29, point 6). Le comptable ou le réviseur l'arrête avant d'appeler
les fonds ; personne d'autre ne le tape, ni l'adhérent, ni la caisse.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.domaine.honoraires import CanalDeReglement

__all__ = [
    "AppelDeFonds",
    "EtatAppelDeFonds",
    "TransitionDeFondsRefusee",
    "identifiant_d_appel",
]


class EtatAppelDeFonds(StrEnum):
    A_REGLER = "A_REGLER"
    FONDS_RECUS = "FONDS_RECUS"
    REVERSE = "REVERSE"


class TransitionDeFondsRefusee(ValueError):
    """Le geste demandé n'existe pas depuis l'état de l'appel."""


def identifiant_d_appel(niu: str, code_obligation: str, periode_debut: date) -> str:
    """Un appel par obligation et par période : l'identifiant le garantit."""
    return f"AF-{niu}-{code_obligation}-{periode_debut:%Y%m%d}"


class AppelDeFonds(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    niu: str = Field(min_length=1)
    denomination: str = Field(min_length=1)
    code_obligation: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    periode_debut: date
    periode_fin: date
    montant: Decimal = Field(gt=0)
    arrete_par: str = Field(min_length=1)
    arrete_le: datetime
    etat: EtatAppelDeFonds = EtatAppelDeFonds.A_REGLER
    #: Les fonds remis au cabinet : par où, sous quelle référence, constatés par qui.
    canal: CanalDeReglement | None = None
    reference_des_fonds: str | None = None
    fonds_recus_le: datetime | None = None
    fonds_constates_par: str | None = None
    #: Le paiement de l'administration : la pièce qui porte sa quittance.
    quittance: str | None = None
    reverse_le: datetime | None = None
    reverse_par: str | None = None

    def recevoir_les_fonds(
        self, *, canal: CanalDeReglement, reference: str, a_l_instant: datetime, par: str
    ) -> AppelDeFonds:
        """⚠️ Une seule fois : des fonds remis deux fois pour la même période sont un
        remboursement à faire, pas un second encaissement silencieux."""
        if self.etat is not EtatAppelDeFonds.A_REGLER:
            raise TransitionDeFondsRefusee(
                f"{self.libelle} ({self.periode_debut:%m/%Y}) : les fonds sont déjà reçus "
                f"({self.canal.value if self.canal else '?'}, {self.reference_des_fonds})."
            )
        return self.model_copy(
            update={
                "etat": EtatAppelDeFonds.FONDS_RECUS,
                "canal": canal,
                "reference_des_fonds": reference,
                "fonds_recus_le": a_l_instant,
                "fonds_constates_par": par,
            }
        )

    def reverser(self, *, quittance: str, a_l_instant: datetime, par: str) -> AppelDeFonds:
        """⚠️ On ne reverse que ce qu'on a reçu : payer l'administration avant d'avoir les
        fonds, c'est avancer l'argent du cabinet, et ce n'est pas ce que dit le mandat."""
        if self.etat is not EtatAppelDeFonds.FONDS_RECUS:
            raise TransitionDeFondsRefusee(
                f"{self.libelle} ({self.periode_debut:%m/%Y}) est à l'état {self.etat.value} : "
                "on ne reverse que des fonds reçus, et une seule fois."
            )
        return self.model_copy(
            update={
                "etat": EtatAppelDeFonds.REVERSE,
                "quittance": quittance,
                "reverse_le": a_l_instant,
                "reverse_par": par,
            }
        )
