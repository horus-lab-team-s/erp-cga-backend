"""Jeu de données de démonstration — § 13.5 du dossier de design.

Toutes ces données sont fictives. Elles sont employées **de manière cohérente d'un écran
à l'autre** : mêmes entreprises, mêmes montants, même période de référence — juillet
2026, la date du jour étant le 8 août 2026.

Ne jamais substituer de faux latin ni de valeurs aléatoires : les écrans doivent montrer
des chiffres qui s'additionnent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from .modeles import (
    ContexteControle,
    Document,
    FactureAControler,
    LigneFacture,
    ModeReglement,
    Montants,
    Partie,
    RegimeEmetteur,
    Reglement,
    TypeDocument,
)

__all__ = ["FACTURES_DEMO", "facture_demo"]


def _adherent(denomination: str, niu: str, rccm: str) -> Partie:
    return Partie(
        denomination=denomination, niu=niu, niu_actif=True, rccm=rccm, regime=RegimeEmetteur.REEL
    )


#: F-2026-0412 — verdict attendu : MAJEUR (règlement en espèces au-delà du seuil).
_F0412 = FactureAControler(
    document=Document(
        type=TypeDocument.FACTURE_ACHAT,
        reference="F-2026-0412",
        date_emission=date(2026, 7, 12),
    ),
    emetteur=Partie(
        denomination="QUINCAILLERIE DU WOURI",
        niu="M053311224455R",
        niu_actif=True,
        rccm="RC/DLA/2015/B/0881",
        regime=RegimeEmetteur.REEL,
    ),
    destinataire=_adherent("SARL BATIMENT PLUS", "M081234567890P", "RC/DLA/2021/B/0977"),
    montants=Montants(total_ht=D(1_970_650), total_tva=D(379_350), total_ttc=D(2_350_000)),
    reglement=Reglement(mode=ModeReglement.ESPECES, date_reglement=date(2026, 7, 12)),
    lignes=[
        LigneFacture(designation="Ciment CPJ 42,5 — 250 sacs", montant_ht=D(1_250_000)),
        LigneFacture(designation="Fers à béton HA12 — 60 barres", montant_ht=D(720_650)),
    ],
)

#: F-2026-0413 — verdict attendu : CONFORME.
_F0413 = FactureAControler(
    document=Document(reference="F-2026-0413", date_emission=date(2026, 7, 15)),
    emetteur=Partie(
        denomination="TRANSPORT SAWA EXPRESS",
        niu="M042233445566T",
        niu_actif=True,
        rccm="RC/DLA/2016/B/0442",
        regime=RegimeEmetteur.REEL,
    ),
    destinataire=_adherent("SARL BATIMENT PLUS", "M081234567890P", "RC/DLA/2021/B/0977"),
    montants=Montants(total_ht=D(1_200_000), total_tva=D(231_000), total_ttc=D(1_431_000)),
    reglement=Reglement(mode=ModeReglement.VIREMENT, date_reglement=date(2026, 7, 18)),
    lignes=[
        LigneFacture(designation="Transport de matériaux Douala — Kribi", montant_ht=D(1_200_000))
    ],
)

#: F-2026-0414 — verdict attendu : BLOQUANT (NIU absent) + AVERTISSEMENT (doublon).
_F0414 = FactureAControler(
    document=Document(reference="F-2026-0414", date_emission=date(2026, 7, 18)),
    emetteur=Partie(
        denomination="NÉGOCE MOUNGO SARL",
        niu=None,
        niu_actif=None,
        regime=RegimeEmetteur.REEL,
    ),
    destinataire=_adherent("BOULANGERIE LA COLOMBE SARL", "M071122334455J", "RC/YAO/2020/B/0311"),
    montants=Montants(total_ht=D(450_000), total_tva=D(86_625), total_ttc=D(536_625)),
    reglement=Reglement(mode=ModeReglement.ORANGE_MONEY, date_reglement=date(2026, 7, 18)),
    lignes=[LigneFacture(designation="Farine de blé T55 — 90 sacs", montant_ht=D(450_000))],
    # Une facture du même fournisseur et du même montant a été reçue le 09/07/2026.
    contexte=ContexteControle(doublons_potentiels=1),
)

#: F-2026-0415 — verdict attendu : AVERTISSEMENT (désignation imprécise).
_F0415 = FactureAControler(
    document=Document(reference="F-2026-0415", date_emission=date(2026, 7, 22)),
    emetteur=Partie(
        denomination="IMPRIMERIE DU LITTORAL",
        niu="M061199887766D",
        niu_actif=True,
        rccm="RC/DLA/2014/B/0233",
        regime=RegimeEmetteur.REEL,
    ),
    destinataire=_adherent("AGRO-NKOLO SA", "M065544332211L", "RC/BAF/2018/B/0145"),
    montants=Montants(total_ht=D(3_400_000), total_tva=D(654_500), total_ttc=D(4_054_500)),
    reglement=Reglement(mode=ModeReglement.VIREMENT, date_reglement=date(2026, 7, 25)),
    lignes=[LigneFacture(designation="Travaux divers", montant_ht=D(3_400_000))],
)

#: F-2026-0416 — verdict attendu : CONFORME.
_F0416 = FactureAControler(
    document=Document(reference="F-2026-0416", date_emission=date(2026, 7, 28)),
    emetteur=Partie(
        denomination="SOCIÉTÉ DE GARDIENNAGE ALPHA SÉCURITÉ",
        niu="M077788990022S",
        niu_actif=True,
        rccm="RC/YAO/2013/B/0107",
        regime=RegimeEmetteur.REEL,
    ),
    destinataire=_adherent("CLINIQUE LE BON SAMARITAIN", "M093344556677N", "RC/YAO/2017/B/0892"),
    montants=Montants(total_ht=D(850_000), total_tva=D(163_625), total_ttc=D(1_013_625)),
    reglement=Reglement(mode=ModeReglement.CHEQUE, date_reglement=date(2026, 7, 30)),
    lignes=[
        LigneFacture(designation="Gardiennage — juillet 2026, 3 agents", montant_ht=D(850_000))
    ],
)

FACTURES_DEMO: dict[str, FactureAControler] = {
    facture.document.reference: facture
    for facture in (_F0412, _F0413, _F0414, _F0415, _F0416)
}


def facture_demo(reference: str) -> FactureAControler:
    if reference not in FACTURES_DEMO:
        connues = ", ".join(sorted(FACTURES_DEMO))
        raise KeyError(f"facture « {reference} » inconnue. Références disponibles : {connues}")
    return FACTURES_DEMO[reference]
