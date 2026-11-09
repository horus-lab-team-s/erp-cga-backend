"""Les versements d'un dossier commercial : frais de traitement, puis tranches (lot 3).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA RESPONSABLE A DEMANDÉ (réunion du 4 novembre 2026, demande 21)

« Quand le client paie en plusieurs fois, il faut que cela s'affiche : ce qu'il a payé
et ce qu'il lui reste à payer. Exemple : 200 000 FCFA réglés en deux fois 100 000.
Dès que tout est payé, une case l'indique. »

Et, le 9 novembre 2026 : dès la soumission de la demande, le client verse des **frais
de traitement de dossier**, pour la plupart des services. C'est une garantie de ne pas
travailler pour rien ; ils sont **déduits** du total de la procédure.

LE SUPER ADMINISTRATEUR RÈGLE DEUX CHOSES (`RegleDesVersements`)

* le montant des frais de traitement, **par service** (absent : pas de frais) ;
* le **seuil de démarrage** : la part du total versée à partir de laquelle le dossier
  est engagé (70 % par exemple). Sans règle : 100 %, c'est-à-dire le comportement
  d'avant, où l'on réglait tout avant de commencer.

⚠️ AUCUN MONTANT N'EST INVENTÉ ICI. Sans règle fixée, il n'y a ni frais ni seuil
réduit : le parcours se comporte exactement comme avant ce lot.

CE QUI SE CALCULE, ET NE SE STOCKE PAS

Seuls les versements sont stockés. Ce qui est payé, ce qui reste, « soldé » et « seuil
atteint » se recalculent à chaque lecture (`situation_des_versements`), comme
l'échéancier d'honoraires : un versement constaté à 10 h se voit à 10 h.

⚠️ UN VERSEMENT NE DÉPASSE PAS LE RESTE À PAYER. Le dépassement serait un trop-perçu à
rembourser, et personne ne l'aurait décidé : il est refusé (`VersementRefuse`).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contextes.souscription.domaine.honoraires import CanalDeReglement

__all__ = [
    "NatureVersement",
    "REGLE_SANS_FRAIS",
    "RegleDesVersements",
    "SituationDesVersements",
    "VersementDejaRecu",
    "VersementDuParcours",
    "VersementRefuse",
    "situation_des_versements",
    "verifier_le_versement",
]


class VersementRefuse(ValueError):
    """Le versement proposé ne peut pas être reçu (montant nul, au-delà du reste…)."""


class VersementDejaRecu(ValueError):
    """Cette référence (rappel d'opérateur, reçu de caisse) est déjà comptée."""


class NatureVersement(StrEnum):
    #: Versé à la soumission de la demande ; déduit ensuite du total de la proforma.
    FRAIS_DE_TRAITEMENT = "FRAIS_DE_TRAITEMENT"
    #: Une tranche du règlement de la proforma.
    TRANCHE = "TRANCHE"
    #: La pénalité d'un rendez-vous manqué (9 novembre 2026). ⚠️ JAMAIS déduite du total :
    #: elle sanctionne l'absence, elle ne paie pas la prestation.
    PENALITE_RENDEZ_VOUS = "PENALITE_RENDEZ_VOUS"
    #: De l'argent reçu de l'opérateur que rien ne peut absorber (9 novembre 2026) : au-delà
    #: du reste, sur une proforma soldée, sur un dossier classé. ⚠️ JAMAIS perdu, jamais
    #: déduit : il attend à la caisse d'être restitué au client.
    TROP_PERCU = "TROP_PERCU"
    #: La caisse constate qu'un trop-perçu a été rendu au client.
    RESTITUTION = "RESTITUTION"


class VersementDuParcours(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    #: La référence du dossier commercial.
    dossier: str = Field(min_length=1)
    nature: NatureVersement
    montant: Decimal = Field(gt=0)
    canal: CanalDeReglement
    #: La référence de l'opérateur, ou le numéro du reçu de caisse.
    reference_externe: str = Field(min_length=1)
    #: La proforma réglée, pour une tranche.
    proforma: str | None = None
    constate_le: datetime
    #: Un compte du cabinet, ou « systeme » pour une notification de l'opérateur.
    constate_par: str = Field(min_length=1)


class RegleDesVersements(BaseModel):
    """Ce que le super administrateur fixe : frais par service, seuil de démarrage."""

    model_config = ConfigDict(frozen=True)

    #: Le montant des frais de traitement, par code de service. Absent : aucun frais.
    frais_par_service: dict[str, Decimal] = Field(default_factory=dict)
    #: La part du total, en pour cent, à partir de laquelle le dossier est engagé.
    seuil_demarrage_pourcent: int = Field(default=100, ge=1, le=100)
    #: 9 novembre 2026 : la pénalité d'un rendez-vous manqué. Absente : aucune.
    penalite_rendez_vous: Decimal | None = Field(default=None, gt=0)
    motif: str = Field(min_length=5, max_length=500)
    fixee_par: str = Field(min_length=1)
    fixee_le: datetime

    @field_validator("frais_par_service")
    @classmethod
    def _frais_positifs(cls, frais: dict[str, Decimal]) -> dict[str, Decimal]:
        for service, montant in frais.items():
            if montant <= 0:
                raise ValueError(
                    f"frais de {service} : un montant positif, ou retirer le service de la liste."
                )
        return frais

    def frais_de(self, service: str) -> Decimal | None:
        return self.frais_par_service.get(service)


#: La règle d'un cabinet qui n'en a fixé aucune : pas de frais, tout régler avant.
REGLE_SANS_FRAIS = RegleDesVersements(
    motif="aucune règle fixée : pas de frais de traitement, règlement complet",
    fixee_par="systeme",
    fixee_le=datetime(2026, 11, 9),
)


class SituationDesVersements(BaseModel):
    model_config = ConfigDict(frozen=True)

    dossier: str
    #: Le total de la proforma vivante ; `None` tant qu'aucune n'est émise.
    total: Decimal | None
    frais_verses: Decimal
    tranches_versees: Decimal
    #: Les pénalités de rendez-vous manqués versées. Hors du « payé ».
    penalites_versees: Decimal = Decimal(0)
    #: Reçu de l'opérateur sans pouvoir être absorbé, et pas encore restitué.
    trop_percu: Decimal = Decimal(0)
    #: Frais de traitement compris : ils sont déduits du total.
    paye: Decimal
    #: Ce qu'il reste à verser sur la proforma ; `None` sans proforma.
    reste: Decimal | None
    solde: bool
    #: Le montant à atteindre pour engager le dossier ; `None` sans proforma.
    seuil: Decimal | None
    seuil_atteint: bool
    seuil_pourcent: int
    versements: list[VersementDuParcours]


def situation_des_versements(
    dossier: str,
    total: Decimal | None,
    versements: Sequence[VersementDuParcours],
    regle: RegleDesVersements,
) -> SituationDesVersements:
    siens = sorted((v for v in versements if v.dossier == dossier), key=lambda v: v.constate_le)
    frais = sum(
        (v.montant for v in siens if v.nature is NatureVersement.FRAIS_DE_TRAITEMENT), Decimal(0)
    )
    tranches = sum((v.montant for v in siens if v.nature is NatureVersement.TRANCHE), Decimal(0))
    # ⚠️ Les pénalités ne paient pas la prestation : elles restent hors du « payé ».
    penalites = sum(
        (v.montant for v in siens if v.nature is NatureVersement.PENALITE_RENDEZ_VOUS),
        Decimal(0),
    )
    paye = frais + tranches
    trop_percu = sum(
        (v.montant for v in siens if v.nature is NatureVersement.TROP_PERCU), Decimal(0)
    ) - sum((v.montant for v in siens if v.nature is NatureVersement.RESTITUTION), Decimal(0))
    if total is None:
        return SituationDesVersements(
            dossier=dossier,
            total=None,
            frais_verses=frais,
            tranches_versees=tranches,
            penalites_versees=penalites,
            trop_percu=trop_percu,
            paye=paye,
            reste=None,
            solde=False,
            seuil=None,
            seuil_atteint=False,
            seuil_pourcent=regle.seuil_demarrage_pourcent,
            versements=siens,
        )
    reste = max(total - paye, Decimal(0))
    # Arrondi au franc supérieur : 70 % de 100 001 ne s'atteint pas avec 70 000,70.
    seuil = (total * regle.seuil_demarrage_pourcent / Decimal(100)).quantize(
        Decimal(1), rounding=ROUND_CEILING
    )
    return SituationDesVersements(
        dossier=dossier,
        total=total,
        frais_verses=frais,
        tranches_versees=tranches,
        penalites_versees=penalites,
        trop_percu=trop_percu,
        paye=paye,
        reste=reste,
        solde=reste == 0,
        seuil=seuil,
        seuil_atteint=paye >= seuil,
        seuil_pourcent=regle.seuil_demarrage_pourcent,
        versements=siens,
    )


def verifier_le_versement(
    situation: SituationDesVersements, nature: NatureVersement, montant: Decimal
) -> None:
    """Lève `VersementRefuse` si ce versement ne peut pas être reçu."""
    if montant <= 0:
        raise VersementRefuse("un versement porte un montant positif.")
    if nature in (
        NatureVersement.FRAIS_DE_TRAITEMENT,
        NatureVersement.PENALITE_RENDEZ_VOUS,
        NatureVersement.TROP_PERCU,
    ):
        # ⚠️ 9 novembre 2026 : des frais versés deux fois (au rendez-vous, puis au formulaire)
        # sont TOUS déduits : « la somme des deux doit être déduite ». Un second versement
        # n'est donc pas refusé ; les routes évitent seulement de le solliciter.
        return
    if nature is NatureVersement.RESTITUTION:
        if montant > situation.trop_percu:
            raise VersementRefuse("on ne restitue pas plus que le trop-perçu en attente.")
        return
    if situation.reste is None:
        raise VersementRefuse(
            "aucune proforma n'est émise : il n'y a pas encore de total à régler."
        )
    if situation.solde:
        raise VersementRefuse("cette proforma est déjà soldée.")
    if montant > situation.reste:
        raise VersementRefuse(
            f"le versement ({montant} FCFA) dépasse le reste à payer ({situation.reste} FCFA)."
        )
