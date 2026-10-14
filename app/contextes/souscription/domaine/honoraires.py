"""Les honoraires d'un adhérent : ce qui est convenu, ce qui est dû, ce qui est réglé.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Paiement Tara ou manuel des frais, avec changement de statut », depuis l'espace
adhérent ; et le choix arrêté le même jour : l'adhérent voit son **échéancier
complet** (réglées, à régler, en retard, à venir), règle par mobile money, ou au
guichet, où la responsable de caisse le constate.

Jusque-là, le montant convenu avec un adhérent n'était écrit nulle part. Les
souscriptions en ligne portaient une mensualité ; un adhérent entré au
portefeuille autrement n'en avait aucune, et son espace ne pouvait rien lui dire
de ce qu'il devait.

LE CONTRAT SE DÉCIDE, L'ÉCHÉANCIER SE CALCULE, LES RÈGLEMENTS SE STOCKENT

Même discipline que l'abonnement (`abonnements.py`) : l'échéancier découle du
montant, de la périodicité et de la date de début. Le persister figerait un
calendrier qui deviendrait faux au premier changement de contrat. Ce qui est
conservé, ce sont les **règlements**, un par période, et l'état d'une échéance
se lit en confrontant les deux.

⚠️ UNE PÉRIODE NE SE RÈGLE QU'UNE FOIS. Le mobile money et le guichet peuvent
arriver pour la même période : le second règlement est refusé, et la base le
garantit aussi (clé unique `(locataire, niu, periode)`). Un double règlement
non détecté est un remboursement que personne ne pense à faire.

⚠️ CE MODULE NE TOUCHE NI À LA TVA NI À LA CNPS. Ce sont des sommes dues à
l'administration, pas au cabinet ; elles ont leur propre voie (décision du 14
octobre 2026, deux modes : quittance de l'adhérent, ou cabinet mandaté qui tient
les fonds à part puis reverse).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.domaine.abonnements import mois_apres

__all__ = [
    "DELAI_DE_REGLEMENT",
    "CanalDeReglement",
    "ContratDHonoraires",
    "EcheanceDHonoraires",
    "EtatEcheanceDHonoraires",
    "PeriodeDejaReglee",
    "PeriodiciteDHonoraires",
    "ReglementDHonoraires",
    "echeancier_d_honoraires",
    "reference_du_contrat",
]

#: Combien de jours une échéance reste « à régler » avant de passer « en retard ».
#:
#: ⚠️ Le délai de grâce de l'abonnement (quinze jours), et pour la même raison :
#: un règlement mobile échoue souvent pour une cause passagère, et l'adhérent doit
#: avoir le temps de recommencer avant d'être dit en retard.
DELAI_DE_REGLEMENT = timedelta(days=15)


class PeriodiciteDHonoraires(StrEnum):
    MENSUELLE = "MENSUELLE"
    TRIMESTRIELLE = "TRIMESTRIELLE"
    ANNUELLE = "ANNUELLE"

    @property
    def mois(self) -> int:
        return {"MENSUELLE": 1, "TRIMESTRIELLE": 3, "ANNUELLE": 12}[self.value]


def reference_du_contrat(niu: str) -> str:
    """La référence lisible du contrat d'un adhérent : un contrat par entreprise."""
    return f"HON-{niu}"


class ContratDHonoraires(BaseModel):
    """Ce que le cabinet et l'adhérent ont convenu. **Une décision, datée et signée.**"""

    model_config = ConfigDict(frozen=True)

    niu: str = Field(min_length=1)
    denomination: str = Field(min_length=1)
    montant: Decimal = Field(gt=0, description="Par période, en francs CFA")
    periodicite: PeriodiciteDHonoraires
    #: Le début de la première période : l'échéancier en découle.
    debut: date
    fixe_par: str = Field(min_length=1)
    fixe_le: datetime

    @property
    def reference(self) -> str:
        return reference_du_contrat(self.niu)


class CanalDeReglement(StrEnum):
    #: Notifié par l'opérateur : personne n'a rien saisi.
    MOBILE_MONEY = "MOBILE_MONEY"
    #: Constaté par la responsable de caisse, sur un reçu.
    GUICHET = "GUICHET"


class ReglementDHonoraires(BaseModel):
    """Le règlement d'UNE période. Ni partiel, ni groupé : une période, un règlement."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    niu: str = Field(min_length=1)
    #: Le début de la période réglée : c'est la clé de l'échéance.
    periode: date
    montant: Decimal = Field(gt=0)
    canal: CanalDeReglement
    #: La référence de l'opérateur, ou le numéro du reçu de caisse.
    reference_externe: str = Field(min_length=1)
    constate_le: datetime
    #: Un compte du cabinet, ou « systeme » pour une notification de l'opérateur.
    constate_par: str = Field(min_length=1)


class PeriodeDejaReglee(ValueError):
    """La période porte déjà un règlement."""


class EtatEcheanceDHonoraires(StrEnum):
    REGLEE = "REGLEE"
    #: Exigible depuis moins que le délai de règlement : rien d'anormal encore.
    A_REGLER = "A_REGLER"
    EN_RETARD = "EN_RETARD"
    A_VENIR = "A_VENIR"


class EcheanceDHonoraires(BaseModel):
    """Une période, ce qu'elle coûte, et où elle en est. Calculée, jamais stockée."""

    model_config = ConfigDict(frozen=True)

    niu: str
    debut: date
    fin: date
    exigible_le: date
    montant: Decimal
    etat: EtatEcheanceDHonoraires
    reglement: ReglementDHonoraires | None = None


def echeancier_d_honoraires(
    contrat: ContratDHonoraires,
    reglements: Sequence[ReglementDHonoraires],
    a_la_date: date,
    *,
    a_venir: int = 2,
) -> list[EcheanceDHonoraires]:
    """Toutes les périodes ouvertes jusqu'ici, et les `a_venir` suivantes.

    ⚠️ L'ÉCHÉANCE EST EXIGIBLE AU DÉBUT DE SA PÉRIODE. Les honoraires d'un
    cabinet se paient d'avance : on règle le mois qui commence, pas celui qui
    vient de finir.

    ⚠️ UN RÈGLEMENT D'UNE PÉRIODE INCONNUE DE L'ÉCHÉANCIER N'EST PAS PERDU DE
    VUE : il n'est pas rendu ici, mais il reste en base. Il ne peut arriver que
    si le contrat a changé de date de début, et c'est au cabinet d'en décider.
    """
    par_periode = {r.periode: r for r in reglements if r.niu == contrat.niu}
    pas = contrat.periodicite.mois
    echeances: list[EcheanceDHonoraires] = []
    futures = 0
    rang = 0
    while True:
        debut = mois_apres(contrat.debut, rang * pas)
        fin = mois_apres(contrat.debut, (rang + 1) * pas) - timedelta(days=1)
        reglement = par_periode.get(debut)
        if reglement is not None:
            etat = EtatEcheanceDHonoraires.REGLEE
        elif debut > a_la_date:
            etat = EtatEcheanceDHonoraires.A_VENIR
        elif a_la_date - debut <= DELAI_DE_REGLEMENT:
            etat = EtatEcheanceDHonoraires.A_REGLER
        else:
            etat = EtatEcheanceDHonoraires.EN_RETARD
        if debut > a_la_date:
            if futures >= a_venir:
                break
            futures += 1
        echeances.append(
            EcheanceDHonoraires(
                niu=contrat.niu,
                debut=debut,
                fin=fin,
                exigible_le=debut,
                montant=contrat.montant,
                etat=etat,
                reglement=reglement,
            )
        )
        rang += 1
    return echeances
