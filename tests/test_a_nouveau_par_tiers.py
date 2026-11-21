"""L'à-nouveau d'un compte de tiers se reporte tiers par tiers (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
LE DÉFAUT : le report tenait en une ligne par compte. Le total était juste, mais au
1er janvier l'exercice suivant ne savait plus ce qu'il devait à chaque fournisseur ni ce
que chaque client lui devait : ni balance auxiliaire, ni lettrage d'un règlement N+1.

⚠️ CE QUI EST GARDÉ
* un compte lettrable se reporte par tiers, chacun avec son sens ;
* le total de chaque compte est celui de l'ancien report, au franc près ;
* ce qui n'a pas de tiers reste reporté, sur une ligne sans tiers ;
* un tiers soldé ne produit pas de ligne ; un compte non lettrable ne change pas ;
* un brouillon n'entre pas dans le détail.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.contextes.cloture.domaine.a_nouveau import lignes_d_a_nouveau, soldes_par_tiers
from app.contextes.comptabilite.adaptateurs.sortant.plan_syscohada import COMPTES_SYSCOHADA
from app.contextes.comptabilite.contrats import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.contextes.comptabilite.domaine.projections import balance

PLAN = list(COMPTES_SYSCOHADA)
ALUCAM = "M012345678901A"
SOCATRAL = "M098765432109B"


def _ecriture(*lignes, numero: int, etat=EtatEcriture.VALIDEE) -> EcritureComptable:
    """`(compte, montant signé, tiers)` : positif au débit, négatif au crédit."""
    return EcritureComptable(
        journal="OD",
        exercice="2026",
        numero=numero,
        date_operation=date(2026, 6, 30),
        libelle="Opération d'essai",
        piece_justificative=f"PJ-{numero:04d}",
        lignes=[
            LigneEcriture(
                compte=compte,
                libelle=compte,
                sens=Sens.DEBIT if montant > 0 else Sens.CREDIT,
                montant=abs(Decimal(montant)),
                tiers=tiers,
            )
            for compte, montant, tiers in lignes
        ],
        etat=etat,
        saisie_par="l.fotso",
        saisie_le=datetime(2026, 6, 30, 9, 0),
        validee_par="a.bouba" if etat is EtatEcriture.VALIDEE else None,
        validee_le=datetime(2026, 7, 1, 9, 0) if etat is EtatEcriture.VALIDEE else None,
    )


EXERCICE = [
    # Deux fournisseurs à crédit, l'un réglé en partie ; une dette sans tiers.
    _ecriture(("602", 4_000_000, None), ("401", -4_000_000, ALUCAM), numero=1),
    _ecriture(("602", 1_500_000, None), ("401", -1_500_000, SOCATRAL), numero=2),
    _ecriture(("401", 1_000_000, ALUCAM), ("521", -1_000_000, None), numero=3),
    _ecriture(("605", 200_000, None), ("401", -200_000, None), numero=4),
    # Un fournisseur entièrement réglé : rien à reporter pour lui.
    _ecriture(("602", 300_000, None), ("401", -300_000, "M055555555555C"), numero=5),
    _ecriture(("401", 300_000, "M055555555555C"), ("521", -300_000, None), numero=6),
    # La banque, pour que la trésorerie soit positive.
    _ecriture(("521", 9_000_000, None), ("701", -9_000_000, None), numero=7),
    # Un brouillon sur un tiers : il ne doit rien faire naître.
    _ecriture(
        ("602", 777_000, None), ("401", -777_000, ALUCAM), numero=8, etat=EtatEcriture.BROUILLON
    ),
]


def _report():
    return lignes_d_a_nouveau(
        balance(EXERCICE), plan=PLAN, detail_par_tiers=soldes_par_tiers(EXERCICE)
    )


def _signe(ligne: LigneEcriture) -> Decimal:
    return ligne.montant if ligne.sens is Sens.DEBIT else -ligne.montant


class TestLeReportParTiers:
    def test_chaque_fournisseur_garde_sa_dette(self):
        par_tiers = {(x.compte, x.tiers): _signe(x) for x in _report() if x.compte == "401"}

        assert par_tiers == {
            ("401", ALUCAM): Decimal(-3_000_000),
            ("401", SOCATRAL): Decimal(-1_500_000),
            ("401", None): Decimal(-200_000),
        }

    def test_le_total_du_compte_est_celui_de_l_ancien_report(self):
        ancien = {x.compte: _signe(x) for x in lignes_d_a_nouveau(balance(EXERCICE), plan=PLAN)}
        nouveau: dict[str, Decimal] = {}
        for x in _report():
            nouveau[x.compte] = nouveau.get(x.compte, Decimal(0)) + _signe(x)

        assert nouveau == ancien

    def test_un_compte_non_lettrable_ne_change_pas(self):
        banques = [x for x in _report() if x.compte == "521"]

        assert len(banques) == 1 and banques[0].tiers is None

    def test_un_brouillon_n_entre_pas_dans_le_detail(self):
        assert soldes_par_tiers(EXERCICE)["401"][ALUCAM] == Decimal(-3_000_000)
