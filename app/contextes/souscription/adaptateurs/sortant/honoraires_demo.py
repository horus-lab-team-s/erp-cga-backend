"""Les contrats d'honoraires de la démonstration, datés du jour de l'amorçage.

⚠️ RELATIFS À LA DATE D'AMORÇAGE, comme les accusés (`demonstration_a_jour.py`) :
des dates en dur montreraient, six mois plus tard, un adhérent en retard de six
périodes. Ici, chaque base amorcée montre les quatre états d'un échéancier.

Bâtiment Plus, mensuel : réglé jusqu'à l'avant-dernier mois (au guichet, puis
par mobile money), le mois dernier EN RETARD, le mois en cours À RÉGLER.
La Colombe, trimestriel : tout réglé sauf le trimestre en cours.
Tchoumba, annuel : l'année en cours réglée.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.contextes.souscription.domaine.abonnements import mois_apres
from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    ContratDHonoraires,
    PeriodiciteDHonoraires,
    ReglementDHonoraires,
    echeancier_d_honoraires,
)
from app.contextes.transverse.api import NIU_DEMO

__all__ = ["honoraires_demo"]

_FIXE_PAR = "C-001"


def honoraires_demo(
    a_la_date: date,
) -> tuple[list[ContratDHonoraires], list[ReglementDHonoraires]]:
    # ⚠️ 18 octobre 2026 : les périodes en cours commencent cinq jours avant l'amorçage.
    # Calées sur le 1er, elles passaient « en retard » après le 15 du mois, et la
    # démonstration, comme trois cas de la suite, ne montrait plus « à régler ». Le jour
    # est borné à 28 : un mois de février n'a pas de 30.
    pivot = a_la_date - timedelta(days=5)
    debut_du_mois = pivot.replace(day=min(pivot.day, 28))
    trimestre = debut_du_mois
    contrats = [
        ContratDHonoraires(
            niu=NIU_DEMO["BATIMENT"],
            denomination="SARL BATIMENT PLUS",
            montant=Decimal("45000"),
            periodicite=PeriodiciteDHonoraires.MENSUELLE,
            debut=mois_apres(debut_du_mois, -6),
            fixe_par=_FIXE_PAR,
            fixe_le=datetime.combine(mois_apres(debut_du_mois, -6), time(9)),
        ),
        ContratDHonoraires(
            niu=NIU_DEMO["COLOMBE"],
            denomination="BOULANGERIE LA COLOMBE SARL",
            montant=Decimal("120000"),
            periodicite=PeriodiciteDHonoraires.TRIMESTRIELLE,
            debut=mois_apres(trimestre, -9),
            fixe_par=_FIXE_PAR,
            fixe_le=datetime.combine(mois_apres(trimestre, -9), time(9)),
        ),
        ContratDHonoraires(
            niu=NIU_DEMO["TCHOUMBA"],
            denomination="ETS TCHOUMBA & FILS",
            montant=Decimal("300000"),
            periodicite=PeriodiciteDHonoraires.ANNUELLE,
            debut=date(a_la_date.year, 1, 1),
            fixe_par=_FIXE_PAR,
            fixe_le=datetime(a_la_date.year, 1, 2, 9),
        ),
    ]
    reglements: list[ReglementDHonoraires] = []
    for contrat in contrats:
        echeances = echeancier_d_honoraires(contrat, [], a_la_date, a_venir=0)
        # Bâtiment Plus laisse ses deux derniers mois ; les autres, la seule période en cours.
        laissees = 2 if contrat.periodicite is PeriodiciteDHonoraires.MENSUELLE else 1
        if contrat.periodicite is PeriodiciteDHonoraires.ANNUELLE:
            laissees = 0
        for rang, e in enumerate(echeances[: len(echeances) - laissees]):
            mobile = rang % 2 == 1
            reglements.append(
                ReglementDHonoraires(
                    identifiant=f"RH-DEMO-{contrat.niu}-{e.debut:%Y%m}",
                    niu=contrat.niu,
                    periode=e.debut,
                    montant=e.montant,
                    canal=CanalDeReglement.MOBILE_MONEY if mobile else CanalDeReglement.GUICHET,
                    reference_externe=(
                        f"TARA-{e.debut:%Y%m}-{contrat.niu[-4:]}"
                        if mobile
                        else f"Reçu de caisse {e.debut:%Y}-{rang + 101:04d}"
                    ),
                    constate_le=datetime.combine(e.debut, time(10, 30)),
                    constate_par="systeme" if mobile else "C-009",
                )
            )
    return contrats, reglements
