"""La paie au Code général des impôts 2026 : ce que la lecture des textes a corrigé.

─────────────────────────────────────────────────────────────────────────────────
DEMANDE DU CABINET (14 octobre 2026) : « les taux de la paie depuis la
réglementation officielle ». Lu le 15 octobre 2026 dans l'édition officielle 2026
du CGI (DGI) et ses annexes, et dans le décret SMIG n° 2023/00338/PM. Les taux CNPS,
CFC, FNE, le barème IRPP et ses abattements étaient justes ; six points manquaient.

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* frais professionnels plafonnés à 4 800 000 par an (art. 34) ;
* pension CNPS salariale déduite de l'assiette IRPP (art. 34) ;
* aucune retenue IRPP sous 62 000 FCFA brut mensuel (art. 81) ;
* CFC et FNE sur une base arrondie au millier inférieur (loi n° 77/10) ;
* TDL au douzième d'un montant ANNUEL fixe par palier (art. C86-C87), bornes
  inclusives du texte ;
* redevance audiovisuelle, montant mensuel fixe par palier (ordonnance n° 89/004) ;
* un salaire de base sous le SMIG est signalé, sans être refusé ;
* un barème est à taux OU à montants, jamais mêlé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.referentiel.api import (
    DepotBaremesYaml,
    DepotParametresYaml,
    ServiceBaremes,
    ServiceParametres,
    TrancheBareme,
    VersionBareme,
)
from app.contextes.referentiel.api import Fondement as FondementRef
from app.contextes.social.api import Contrat, Periode, TypeContrat, calculer_bulletin
from app.infrastructure.config import configuration

JUILLET = Periode(annee=2026, mois=7)
DOSSIER = "M065544332211L"


@pytest.fixture(scope="module")
def parametres() -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    )


@pytest.fixture(scope="module")
def baremes() -> ServiceBaremes:
    return ServiceBaremes.depuis_depot(
        DepotBaremesYaml(configuration().dossier_referentiel / "baremes.yaml")
    )


def _bulletin(parametres, baremes, base: str, primes: str = "0"):
    contrat = Contrat(
        salarie="SAL-TEST",
        type_contrat=TypeContrat.CDI,
        debut=date(2020, 1, 1),
        salaire_base=Decimal(base),
        primes=Decimal(primes),
    )
    return calculer_bulletin(contrat, DOSSIER, JUILLET, parametres, baremes)


def _montant(bulletin, code: str) -> Decimal:
    return next(ligne.montant for ligne in bulletin.lignes if ligne.code == code)


class _Surcharge:
    """Le référentiel réel, une valeur changée : pour voir une règle que les autres masquent."""

    def __init__(self, reel: ServiceParametres, **valeurs) -> None:
        self._reel = reel
        self._valeurs = valeurs

    def resoudre(self, code, a_la_date):
        resolu = self._reel.resoudre(code, a_la_date)
        if code in self._valeurs:
            return resolu.model_copy(update={"valeur": self._valeurs[code]})
        return resolu


class TestLIrpp:
    def test_les_frais_professionnels_sont_plafonnes(self, parametres, baremes):
        """2 000 000 par mois : 30 % de 24 000 000 = 7 200 000, plafonné à 4 800 000.

        Pension 4,2 % de 750 000 (plafond) = 31 500, soit 378 000 l'an. Assiette :
        24 000 000 − 4 800 000 − 378 000 − 500 000 = 18 322 000. Barème : 200 000 +
        150 000 + 500 000 + 35 % × 13 322 000 (4 662 700) = 5 512 700 ; +10 % de CAC =
        6 063 970 ; mensualisé = 505 331.
        """
        assert _montant(_bulletin(parametres, baremes, "2000000"), "IRPP") == Decimal(505331)

    def test_aucune_retenue_sous_62_000_de_brut(self, parametres, baremes):
        """⚠️ Avec les abattements réels, un salaire sous 62 000 n'est de toute façon pas
        imposable : la dispense ne se voit qu'abattement annuel retiré. Elle est éprouvée
        ainsi, pour que la règle existe le jour où les abattements changent."""
        sans_abattement = _Surcharge(parametres, IRPP_ABATTEMENT_FORFAITAIRE_ANNUEL=0)

        sous = _bulletin(sans_abattement, baremes, "61999")
        au_seuil = _bulletin(sans_abattement, baremes, "62000")

        assert _montant(sous, "IRPP") == 0
        assert _montant(au_seuil, "IRPP") > 0


class TestLesTaxesSurSalaires:
    def test_cfc_et_fne_sur_la_base_arrondie_au_millier_inferieur(self, parametres, baremes):
        b = _bulletin(parametres, baremes, "575999")

        assert _montant(b, "CFC_S") == Decimal(5750)  # 1 % de 575 000
        assert _montant(b, "CFC_P") == Decimal(8625)  # 1,5 % de 575 000
        assert _montant(b, "FNE") == Decimal(5750)

    @pytest.mark.parametrize(
        ("base", "mensuel"),
        [
            ("61999", 0),
            ("62000", 250),  # 3 000 / 12 : borne basse incluse au texte
            ("75000", 250),  # borne haute incluse au texte
            ("75001", 500),
            ("80000", 500),
            ("500001", 2500),  # 30 000 / 12
        ],
    )
    def test_la_tdl_est_le_douzieme_du_montant_annuel_de_son_palier(
        self, parametres, baremes, base, mensuel
    ):
        assert _montant(_bulletin(parametres, baremes, base), "TDL") == Decimal(mensuel)

    @pytest.mark.parametrize(
        ("base", "primes", "rav"),
        [
            ("50000", "0", 0),
            ("90000", "10000", 750),  # brut 100 000 : borne haute incluse
            ("100001", "0", 1950),
            ("1500000", "0", 13000),
        ],
    )
    def test_la_redevance_audiovisuelle_par_palier_du_brut(
        self, parametres, baremes, base, primes, rav
    ):
        assert _montant(_bulletin(parametres, baremes, base, primes), "RAV") == Decimal(rav)

    def test_les_nouvelles_lignes_sont_retenues_sur_le_salaire(self, parametres, baremes):
        b = _bulletin(parametres, baremes, "400000")
        codes = {ligne.code: ligne for ligne in b.lignes}

        assert codes["RAV"].a_charge_du_salarie and codes["TDL"].a_charge_du_salarie
        # « À valider » tant que le fiscaliste n'a pas validé les barèmes au référentiel.
        assert codes["RAV"].non_valide and codes["TDL"].non_valide


class TestLeSmig:
    def test_un_salaire_sous_le_smig_est_signale_sans_etre_refuse(self, parametres, baremes):
        sous = _bulletin(parametres, baremes, "55000")
        au_smig = _bulletin(parametres, baremes, "60000")

        (alerte,) = sous.alertes
        assert "55 000 FCFA" in alerte and "60 000 FCFA" in alerte
        assert "2023/00338/PM" in alerte
        assert au_smig.alertes == ()


class TestLeBaremeAMontantFixe:
    def _version(self, tranches):
        return VersionBareme(
            tranches=tranches,
            applicable_du=date(2020, 1, 1),
            fondement=FondementRef(texte="test", source="test"),
        )

    def test_le_montant_du_palier_entier_et_non_une_somme_de_fractions(self):
        v = self._version(
            [
                TrancheBareme(plancher=Decimal(0), plafond=Decimal(100), montant=Decimal(0)),
                TrancheBareme(plancher=Decimal(100), plafond=None, montant=Decimal(750)),
            ]
        )
        assert v.appliquer(Decimal(99)) == 0
        assert v.appliquer(Decimal(100)) == 750
        assert v.appliquer(Decimal(10**9)) == 750

    def test_ni_taux_et_montant_dans_une_tranche_ni_bareme_mele(self):
        with pytest.raises(ValueError, match="un seul"):
            TrancheBareme(plancher=Decimal(0), taux=Decimal(10), montant=Decimal(5))
        with pytest.raises(ValueError, match="pas les deux"):
            self._version(
                [
                    TrancheBareme(plancher=Decimal(0), plafond=Decimal(100), taux=Decimal(10)),
                    TrancheBareme(plancher=Decimal(100), plafond=None, montant=Decimal(750)),
                ]
            )
