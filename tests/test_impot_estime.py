"""L'IS estimé de l'exercice, minimum de perception compris (15 octobre 2026).

⚠️ CE QUI EST GARDÉ (CGI 2026)
* 30 % du résultat fiscal, majoré de 10 % de centimes additionnels (art. 17) ;
* jamais moins de 2 % du chiffre d'affaires HT arrondi au millier inférieur, majorés de
  même (art. 22 (2) et 23) : le plus élevé des deux est dû ;
* un déficit ne dispense pas du minimum ;
* à l'IGS, et pour une forme hors IS, rien n'est estimé, et le motif le dit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.cloture.application.impot_estime import estimer_l_impot
from tests.conftest import exige_postgresql, ouvrir_une_session

D = Decimal
CLOTURE = date(2026, 12, 31)


def _estimer(parametres, *, resultat, ca, forme="SARL", regime="REEL"):
    return estimer_l_impot(
        forme_juridique=forme,
        regime=regime,
        resultat_fiscal=D(resultat),
        chiffre_affaires=D(ca),
        parametres=parametres,
        cloture=CLOTURE,
    )


class TestLeCalcul:
    def test_au_taux_quand_le_benefice_l_emporte(self, parametres):
        impot = _estimer(parametres, resultat=10_000_000, ca=60_000_000)
        assert impot.is_au_taux == D(3_300_000)  # 30 % + 10 % de CAC
        assert impot.minimum_de_perception == D(1_320_000)  # 2,2 % de 60 M
        assert impot.impot_du == D(3_300_000) and impot.retenu == "TAUX"

    def test_le_minimum_quand_le_benefice_est_faible(self, parametres):
        impot = _estimer(parametres, resultat=1_000_000, ca=80_000_000)
        assert impot.impot_du == impot.minimum_de_perception == D(1_760_000)
        assert impot.retenu == "MINIMUM"

    def test_un_deficit_paie_le_minimum(self, parametres):
        impot = _estimer(parametres, resultat=-4_000_000, ca=50_000_000)
        assert impot.is_au_taux == D(0) and impot.impot_du == D(1_100_000)

    def test_ni_benefice_ni_chiffre_rien_n_est_du(self, parametres):
        impot = _estimer(parametres, resultat=-4_570_650, ca=0)
        assert impot.impot_du == D(0) and impot.retenu == "AUCUN"

    def test_la_base_s_arrondit_au_millier_inferieur(self, parametres):
        impot = _estimer(parametres, resultat=0, ca="50_000_999".replace("_", ""))
        assert impot.base_du_minimum == D(50_000_000)

    def test_rien_n_est_estime_a_l_igs_ni_hors_is(self, parametres):
        igs = _estimer(parametres, resultat=5_000_000, ca=30_000_000, regime="IGS")
        ets = _estimer(parametres, resultat=5_000_000, ca=60_000_000, forme="ETS")
        assert not igs.estime and "C 38" in igs.motif
        assert not ets.estime and "IRPP" in ets.motif

    def test_un_taux_non_valide_se_signale(self, parametres):
        # Le minimum de perception est au référentiel « à valider » (15 octobre 2026).
        assert _estimer(parametres, resultat=1, ca=60_000_000).non_valide is True


@exige_postgresql
class TestParLaLiasse:
    def test_la_liasse_d_un_dossier_au_reel_porte_son_is(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        batiment = client.get("/cloture/dossiers/M081234567890P/liasse/2026").json()
        nguema = client.get("/cloture/dossiers/P027788990011M/liasse/2025").json()

        assert batiment["impot"]["estime"] is True
        assert D(batiment["impot"]["impot_du"]) >= D(batiment["impot"]["minimum_de_perception"])
        assert nguema["impot"]["estime"] is False
