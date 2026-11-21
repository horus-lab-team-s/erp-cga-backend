"""Les relances d'honoraires : ce qui doit être dit à l'adhérent, et quand (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ (réunion du 4 novembre 2026)

« Leur faire comprendre, à l'avance, que leur compte sera bloqué. » L'échéancier
(`honoraires.py`) et la situation (`situation_de_reglement.py`) savaient déjà tout ;
personne ne le disait à l'adhérent tant qu'il n'ouvrait pas son espace.

QUATRE PALIERS, TOUS TIRÉS DES RÈGLES EXISTANTES

* `echeance_a_regler` : une échéance devient exigible (au début de sa période) ;
* `en_retard` : elle passe en retard (`DELAI_DE_REGLEMENT` après son exigibilité) ;
* `preavis_suspension` : l'accès du client passe à AVERTI (`preavis_acces_jours` avant
  la suspension, selon la règle du cabinet) ;
* `acces_suspendu` : l'accès du client est SUSPENDU (`acces_client_mois` après
  l'exigibilité de la plus ancienne échéance impayée).

Aucun délai n'est inventé ici : chacun vient de la règle qui décide déjà de l'état.

⚠️ UNE FOIS PAR PALIER ET PAR ÉCHÉANCE. La clé d'une relance est
`{niu}:{période}:{palier}`. Pour les deux paliers d'accès, la période est celle de la
plus ancienne échéance impayée, celle qui fait courir la suspension : un adhérent qui
règle cette échéance-là mais reste suspendu par la suivante en est informé une fois de
plus, et c'est voulu (son reste dû a changé, son accès non).

⚠️ PAS D'INONDATION : LE PALIER LE PLUS GRAVE, LES AUTRES COUVERTS. À un passage, pour
un dossier, une seule relance part : la plus grave parmi celles qui n'ont jamais été
traitées (à gravité égale, la plus récente). Les autres sont tenues pour couvertes par
elle et inscrites comme telles, sans envoi. Un dossier en retard de cinq mois au premier
passage reçoit ainsi UN message (celui de sa suspension, qui porte le total dû), et non
une douzaine. Les passages suivants ne relancent que les paliers atteints depuis.

Ce choix a été préféré à une fenêtre de jours : une fenêtre aurait tu à un adhérent
suspendu depuis deux mois qu'il l'était, alors que c'est le message qui compte le plus.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.honoraires import (
    DELAI_DE_REGLEMENT,
    ContratDHonoraires,
    EtatEcheanceDHonoraires,
    ReglementDHonoraires,
    echeancier_d_honoraires,
)
from app.contextes.souscription.domaine.situation_de_reglement import (
    EtatDAccesClient,
    GraceDeReglement,
    RegleDeDepassement,
    situation_de_reglement,
)

__all__ = [
    "PalierDeRelance",
    "RelanceDHonoraires",
    "relance_a_envoyer",
    "relances_atteintes",
]


class PalierDeRelance(StrEnum):
    ECHEANCE_A_REGLER = "echeance_a_regler"
    EN_RETARD = "en_retard"
    PREAVIS_SUSPENSION = "preavis_suspension"
    ACCES_SUSPENDU = "acces_suspendu"

    @property
    def gabarit(self) -> str:
        """Le code du courriel : `honoraires.<palier>`."""
        return f"honoraires.{self.value}"

    @property
    def gravite(self) -> int:
        return list(PalierDeRelance).index(self)


class RelanceDHonoraires(BaseModel):
    """Un palier atteint par un dossier. Calculé, jamais stocké : seule sa clé l'est."""

    model_config = ConfigDict(frozen=True)

    niu: str
    palier: PalierDeRelance
    #: Le début de la période en cause (pour l'accès : la plus ancienne impayée).
    periode: date
    fin_de_periode: date
    exigible_le: date
    #: Le jour où le palier a été atteint.
    atteint_le: date
    #: Le montant de l'échéance en cause.
    montant: Decimal
    #: Tout ce qui est exigible et non réglé, ce jour-là.
    du: Decimal
    #: Dernier jour pour régler avant d'être dit en retard.
    limite: date
    #: Pour les paliers d'accès : le jour de la suspension.
    suspension_le: date | None = None

    @property
    def cle(self) -> str:
        return f"{self.niu}:{self.periode.isoformat()}:{self.palier.value}"


def relances_atteintes(
    contrat: ContratDHonoraires | None,
    reglements: Sequence[ReglementDHonoraires],
    regle: RegleDeDepassement,
    graces: Sequence[GraceDeReglement],
    a_la_date: date,
) -> list[RelanceDHonoraires]:
    """Tous les paliers atteints ce jour par les échéances impayées, et l'accès en cours.

    Une échéance réglée n'en porte aucun ; un accès ouvert (ou rouvert par une grâce)
    non plus. Le tri entre ce qui est déjà dit et ce qui reste à dire est l'affaire de
    `relance_a_envoyer`.
    """
    if contrat is None:
        return []
    echeances = echeancier_d_honoraires(contrat, reglements, a_la_date, a_venir=0)
    impayees = [
        e
        for e in echeances
        if e.etat in (EtatEcheanceDHonoraires.A_REGLER, EtatEcheanceDHonoraires.EN_RETARD)
    ]
    if not impayees:
        return []
    situation = situation_de_reglement(
        contrat.niu, contrat, reglements, regle, graces, a_la_date
    )
    du = situation.du

    def relance(e, palier: PalierDeRelance, atteint_le: date, **autres) -> RelanceDHonoraires:
        return RelanceDHonoraires(
            niu=contrat.niu,
            palier=palier,
            periode=e.debut,
            fin_de_periode=e.fin,
            exigible_le=e.exigible_le,
            atteint_le=atteint_le,
            montant=e.montant,
            du=du,
            limite=e.exigible_le + DELAI_DE_REGLEMENT,
            **autres,
        )

    atteintes: list[RelanceDHonoraires] = []
    for e in impayees:
        atteintes.append(relance(e, PalierDeRelance.ECHEANCE_A_REGLER, e.exigible_le))
        if e.etat is EtatEcheanceDHonoraires.EN_RETARD:
            atteintes.append(
                relance(
                    e,
                    PalierDeRelance.EN_RETARD,
                    e.exigible_le + DELAI_DE_REGLEMENT + timedelta(days=1),
                )
            )
    suspension = situation.acces_suspendu_le
    if suspension is not None and situation.acces_client is not EtatDAccesClient.OUVERT:
        # La même échéance que celle d'où la situation fait courir la suspension.
        plus_ancienne = min(
            (e for e in impayees if e.etat is EtatEcheanceDHonoraires.EN_RETARD),
            key=lambda e: e.exigible_le,
        )
        if situation.acces_client is EtatDAccesClient.SUSPENDU:
            palier, atteint_le = PalierDeRelance.ACCES_SUSPENDU, suspension
        else:
            palier = PalierDeRelance.PREAVIS_SUSPENSION
            atteint_le = suspension - timedelta(days=regle.preavis_acces_jours)
        atteintes.append(relance(plus_ancienne, palier, atteint_le, suspension_le=suspension))
    return atteintes


def relance_a_envoyer(
    atteintes: Sequence[RelanceDHonoraires], deja: Collection[str]
) -> tuple[RelanceDHonoraires | None, list[RelanceDHonoraires]]:
    """La relance qui part à ce passage, et celles qu'elle couvre sans envoi.

    Parmi les paliers jamais traités (`deja` : les clés déjà inscrites), le plus grave ;
    à gravité égale, le plus récemment atteint. Voir l'en-tête : un passage n'envoie
    jamais plus d'un message par dossier.
    """
    nouvelles = [r for r in atteintes if r.cle not in deja]
    if not nouvelles:
        return None, []
    choisie = max(nouvelles, key=lambda r: (r.palier.gravite, r.atteint_le, r.periode))
    return choisie, [r for r in nouvelles if r.cle != choisie.cle]
