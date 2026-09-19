"""Jeu de données de démonstration — § 13.5 du dossier de design.

Toutes ces données sont fictives. Elles sont employées **de manière cohérente d'un écran
à l'autre** : mêmes entreprises, mêmes montants, même période de référence — juillet
2026, la date du jour étant le 8 août 2026.

Ne jamais substituer de faux latin ni de valeurs aléatoires : les écrans doivent montrer
des chiffres qui s'additionnent.

Les cinq factures du § 13.5 — F-2026-0412 à 0416 — sont **canoniques** : leurs verdicts
sont ceux que le cabinet a vus sur les maquettes et sont verrouillés par les tests. Les
suivantes complètent le flux entrant de juillet, afin que l'écran E03 montre les
25 lignes qu'exige sa fiche, avec une distribution de gravité réaliste.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from app.contextes.conformite.domaine.entites import (
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

__all__ = ["FACTURES_DEMO", "facture_demo", "REFERENCES_CANONIQUES"]

#: Les cinq factures du § 13.5. Leurs verdicts sont verrouillés par les tests.
REFERENCES_CANONIQUES = (
    "F-2026-0412",
    "F-2026-0413",
    "F-2026-0414",
    "F-2026-0415",
    "F-2026-0416",
)

#: Taux employé pour reconstituer la TVA des factures de démonstration.
#: Ce n'est PAS une lecture du référentiel : ce sont des données d'illustration, pas un
#: calcul fiscal. Le moteur, lui, lit toujours le référentiel à la date de l'opération.
_TAUX_DEMO = Decimal("0.1925")


class _Adherent(NamedTuple):
    denomination: str
    niu: str
    rccm: str
    #: Le régime du DESTINATAIRE, et il commande la déductibilité de la TVA.
    #: Deux des six adhérents relèvent du synthétique : la règle FAC-ACH-007 n'a
    #: alors rien à dire, puisqu'ils ne récupèrent la TVA sur aucun achat.
    #: Source de vérité : le portefeuille du contexte B, dont ce jeu s'aligne.
    regime: RegimeEmetteur = RegimeEmetteur.REEL


BATIMENT = _Adherent("SARL BATIMENT PLUS", "M081234567890P", "RC/DLA/2021/B/0977")
TCHOUMBA = _Adherent("ETS TCHOUMBA & FILS", "P019876543210K", "RC/DLA/2019/A/1842",
                     RegimeEmetteur.IGS)
COLOMBE = _Adherent("BOULANGERIE LA COLOMBE SARL", "M071122334455J", "RC/YAO/2020/B/0311")
AGRO = _Adherent("AGRO-NKOLO SA", "M065544332211L", "RC/BAF/2018/B/0145")
NGUEMA = _Adherent("CABINET NGUEMA CONSEIL", "P027788990011M", "RC/DLA/2022/A/2510",
                   RegimeEmetteur.IGS)
CLINIQUE = _Adherent("CLINIQUE LE BON SAMARITAIN", "M093344556677N", "RC/YAO/2017/B/0892")


def _fabriquer(
    reference: str,
    jour: int,
    adherent: _Adherent,
    fournisseur: str,
    niu_fournisseur: str | None,
    ht: int,
    mode: ModeReglement,
    designations: list[str],
    *,
    niu_actif: bool | None = True,
    rccm_fournisseur: str | None = "RC/DLA/2015/B/0881",
    ecart_lignes: int = 0,
    doublons: int = 0,
    etranger: bool = False,
) -> FactureAControler:
    """Construit une facture cohérente : la TVA et le TTC découlent du HT.

    `ecart_lignes` introduit volontairement un écart entre la somme des lignes et le
    total hors taxes, pour déclencher FAC-CAL-002.
    """
    montant_ht = Decimal(ht)
    tva = (montant_ht * _TAUX_DEMO).quantize(Decimal(1), rounding=ROUND_HALF_UP)

    total_lignes = montant_ht + Decimal(ecart_lignes)
    part = (total_lignes / len(designations)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    lignes = [LigneFacture(designation=d, montant_ht=part) for d in designations[:-1]]
    lignes.append(
        LigneFacture(
            designation=designations[-1],
            montant_ht=total_lignes - part * (len(designations) - 1),
        )
    )

    return FactureAControler(
        document=Document(
            type=TypeDocument.FACTURE_ACHAT,
            reference=reference,
            date_emission=date(2026, 7, jour),
        ),
        emetteur=Partie(
            denomination=fournisseur,
            niu=niu_fournisseur,
            niu_actif=niu_actif if niu_fournisseur else None,
            rccm=rccm_fournisseur,
            regime=RegimeEmetteur.REEL,
            etranger=etranger,
        ),
        destinataire=Partie(
            denomination=adherent.denomination,
            niu=adherent.niu,
            niu_actif=True,
            rccm=adherent.rccm,
            regime=adherent.regime,
        ),
        montants=Montants(total_ht=montant_ht, total_tva=tva, total_ttc=montant_ht + tva),
        reglement=Reglement(mode=mode, date_reglement=date(2026, 7, min(jour + 3, 31))),
        lignes=lignes,
        contexte=ContexteControle(doublons_potentiels=doublons),
    )


E = ModeReglement.ESPECES
V = ModeReglement.VIREMENT
C = ModeReglement.CHEQUE
OM = ModeReglement.ORANGE_MONEY
MM = ModeReglement.MTN_MOMO

_FACTURES = [
    # ── § 13.5 · les cinq canoniques ─────────────────────────────────────────
    # Majeur : espèces au-delà du seuil.
    _fabriquer("F-2026-0412", 12, BATIMENT, "QUINCAILLERIE DU WOURI", "M053311224455R",
               1_970_650, E, ["Ciment CPJ 42,5 — 250 sacs", "Fers à béton HA12 — 60 barres"]),
    # Conforme.
    _fabriquer("F-2026-0413", 15, BATIMENT, "TRANSPORT SAWA EXPRESS", "M042233445566T",
               1_200_000, V, ["Transport de matériaux Douala — Kribi"],
               rccm_fournisseur="RC/DLA/2016/B/0442"),
    # Bloquant : NIU absent. Plus un doublon possible avec F-2026-0398.
    _fabriquer("F-2026-0414", 18, COLOMBE, "NÉGOCE MOUNGO SARL", None,
               450_000, OM, ["Farine de blé T55 — 90 sacs"],
               rccm_fournisseur=None, doublons=1),
    # Avertissement : désignation imprécise.
    _fabriquer("F-2026-0415", 22, AGRO, "IMPRIMERIE DU LITTORAL", "M061199887766D",
               3_400_000, V, ["Travaux divers"], rccm_fournisseur="RC/DLA/2014/B/0233"),
    # Conforme.
    _fabriquer("F-2026-0416", 28, CLINIQUE, "SOCIÉTÉ DE GARDIENNAGE ALPHA SÉCURITÉ",
               "M077788990022S", 850_000, C, ["Gardiennage — juillet 2026, 3 agents"],
               rccm_fournisseur="RC/YAO/2013/B/0107"),

    # ── Le reste du flux entrant de juillet ──────────────────────────────────
    _fabriquer("F-2026-0417", 29, TCHOUMBA, "BUREAUTIQUE BONAMOUSSADI", "M088877665544B",
               612_000, E, ["Ramettes A4 — 40 cartons", "Cartouches d'encre — 12 unités"]),
    _fabriquer("F-2026-0418", 2, AGRO, "SEMENCES DU NOUN", "M034455667788W",
               2_840_000, V, ["Semences de maïs améliorées — 4 t"]),
    _fabriquer("F-2026-0419", 3, BATIMENT, "LOCATION ENGINS BONABÉRI", "M045566778899G",
               1_400_000, V, ["Location de pelleteuse — 7 jours"]),
    _fabriquer("F-2026-0420", 4, CLINIQUE, "PHARMA DISTRIB SARL", "M056677889900H",
               3_180_000, V, ["Consommables médicaux", "Réactifs de laboratoire"]),
    _fabriquer("F-2026-0421", 6, COLOMBE, "MINOTERIE DU LITTORAL", "M067788990011I",
               1_960_000, V, ["Farine de blé T45 — 400 sacs"]),
    _fabriquer("F-2026-0422", 7, NGUEMA, "PAPETERIE BONANJO", "M078899001122J",
               186_000, E, ["Fournitures de bureau"]),
    _fabriquer("F-2026-0423", 8, AGRO, "ENGRAIS CAMEROUN SA", "M089900112233K",
               6_400_000, V, ["Engrais NPK 20-10-10 — 20 t"]),
    _fabriquer("F-2026-0424", 9, BATIMENT, "SABLIÈRE DU MOUNGO", None,
               418_000, E, ["Sable de rivière — 12 camions"], rccm_fournisseur=None),
    _fabriquer("F-2026-0425", 10, TCHOUMBA, "GROSSISTE MARCHÉ CENTRAL", "M090011223344L",
               1_284_900, E, ["Marchandises"]),
    _fabriquer("F-2026-0426", 11, CLINIQUE, "BLANCHISSERIE AKWA", "M001122334455M",
               288_200, MM, ["Blanchissage du linge — juillet 2026"]),
    _fabriquer("F-2026-0427", 13, AGRO, "TRANSPORT MBOUDA", "M012233445566N",
               2_500_000, V, ["Transport de récolte Bafoussam — Douala"], niu_actif=False),
    _fabriquer("F-2026-0428", 14, COLOMBE, "ÉLECTRICITÉ ENEO", "M023344556677O",
               318_400, V, ["Fourniture d'électricité — juillet 2026"],
               rccm_fournisseur="RC/DLA/2001/B/0004"),
    _fabriquer("F-2026-0429", 16, NGUEMA, "TÉLÉCOM CAMTEL", "M034455667788P",
               142_000, V, ["Abonnement fibre professionnelle — juillet"]),
    _fabriquer("F-2026-0430", 17, BATIMENT, "MENUISERIE BAFOUSSAM", "M045566778899Q",
               1_642_000, C, ["Charpente bois", "Menuiseries extérieures"],
               ecart_lignes=12_400),
    _fabriquer("F-2026-0431", 19, AGRO, "ETS BAMENDA FOURNITURES", None,
               1_622_800, V, ["Petit outillage agricole"], rccm_fournisseur=None),
    _fabriquer("F-2026-0432", 20, CLINIQUE, "MAINTENANCE BIOMÉDICALE SARL", "M056677889900R",
               940_000, V, ["Maintenance d'échographe", "Étalonnage d'automate"]),
    _fabriquer("F-2026-0433", 21, TCHOUMBA, "CARBURANTS TRADEX", "M067788990011S",
               186_000, MM, ["Gasoil — 240 litres"]),
    _fabriquer("F-2026-0434", 23, COLOMBE, "EMBALLAGES DOUALA", "M078899001122T",
               624_000, E, ["Sachets kraft imprimés — 20 000 unités"]),
    _fabriquer("F-2026-0435", 24, BATIMENT, "SÉCURITÉ CHANTIER PLUS", "M089900112233U",
               760_000, V, ["Prestations"]),
    _fabriquer("F-2026-0436", 25, AGRO, "ASSURANCES CHANTERELLE", "M090011223344V",
               2_180_000, V, ["Assurance multirisque industrielle — exercice 2026"]),
    _fabriquer("F-2026-0437", 26, NGUEMA, "FORMATION CONTINUE PLUS", "M001122334455W",
               450_000, V, ["Formation en fiscalité — 2 collaborateurs"]),
    _fabriquer("F-2026-0438", 27, CLINIQUE, "OXYGÈNE MÉDICAL SA", "M012233445566X",
               1_340_000, V, ["Bouteilles d'oxygène médical — 40 unités"]),
    _fabriquer("F-2026-0439", 30, BATIMENT, "GLOBAL STEEL TRADING LTD", None,
               8_400_000, V, ["Profilés acier importés — 32 t"],
               rccm_fournisseur=None, etranger=True),
    _fabriquer("F-2026-0440", 31, TCHOUMBA, "NÉGOCE MOUNGO SARL", "M023344556677Y",
               980_000, E, ["Riz parfumé — 200 sacs"]),
]

FACTURES_DEMO: dict[str, FactureAControler] = {
    facture.document.reference: facture for facture in _FACTURES
}


def facture_demo(reference: str) -> FactureAControler:
    if reference not in FACTURES_DEMO:
        connues = ", ".join(sorted(FACTURES_DEMO))
        raise KeyError(f"facture « {reference} » inconnue. Références disponibles : {connues}")
    return FACTURES_DEMO[reference]
