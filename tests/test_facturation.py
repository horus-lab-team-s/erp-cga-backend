"""L'identité fiscale du cabinet, saisie plus tard, et la facture définitive (18 octobre 2026).

⚠️ CE QUI EST GARDÉ
* l'identité se saisit par morceaux ; tant qu'il manque une mention, la facture ne
  s'émet pas, et le refus dit laquelle ; rien ne s'invente ;
* un NIU mal formé, un taux pour un cabinet non assujetti sont refusés ;
* HT, TVA, TTC selon ce qui a été convenu, au franc près, TTC = HT + TVA ;
* une facture émise se relit telle quelle, même après une correction de l'identité ;
* la numérotation suit l'ordre d'émission, sans trou ;
* la direction seule saisit l'identité ; une échéance non réglée ne se facture pas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.souscription.domaine.facturation import (
    IdentiteFiscaleDuCabinet,
    MontantsConvenus,
    RegimeTvaDuCabinet,
    ventiler,
)
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_recu_honoraires import _texte

BATIMENT = NIU_DEMO["BATIMENT"]
DIRECTRICE = "b.mballa@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
ASSUJETTI = {
    "niu": "M012345678901X",
    "rccm": "RC/DLA/2019/B/0421",
    "regime_tva": "ASSUJETTI",
    "taux_tva": "19.25",
    "montants_convenus": "TTC",
}


class TestLIdentite:
    def test_vide_elle_dit_tout_ce_qui_manque(self):
        manques = IdentiteFiscaleDuCabinet().manques
        assert manques == [
            "le NIU du cabinet",
            "le numéro RCCM du cabinet",
            "le régime de TVA du cabinet",
        ]

    def test_assujetti_il_faut_le_taux_et_ce_qui_a_ete_convenu(self):
        identite = IdentiteFiscaleDuCabinet(
            niu="M012345678901X", rccm="RC/DLA/2019/B/0421", regime_tva=RegimeTvaDuCabinet.ASSUJETTI
        )
        assert identite.manques == [
            "le taux de TVA",
            "si les honoraires convenus s'entendent HT ou TTC",
        ]

    def test_ce_qui_est_incoherent_est_refuse(self):
        with pytest.raises(ValidationError, match="quatorze caractères"):
            IdentiteFiscaleDuCabinet(niu="12345")
        with pytest.raises(ValidationError, match="assujetti"):
            IdentiteFiscaleDuCabinet(
                regime_tva=RegimeTvaDuCabinet.NON_ASSUJETTI, taux_tva=Decimal("19.25")
            )


class TestLaVentilation:
    def _assujetti(self, convenus: MontantsConvenus) -> IdentiteFiscaleDuCabinet:
        return IdentiteFiscaleDuCabinet(
            regime_tva=RegimeTvaDuCabinet.ASSUJETTI,
            taux_tva=Decimal("19.25"),
            montants_convenus=convenus,
        )

    def test_ht_la_tva_s_ajoute(self):
        assert ventiler(Decimal(100000), self._assujetti(MontantsConvenus.HT)) == (
            Decimal(100000),
            Decimal(19250),
            Decimal(119250),
        )

    def test_ttc_la_tva_s_extrait_et_le_total_tient(self):
        ht, tva, ttc = ventiler(Decimal(25000), self._assujetti(MontantsConvenus.TTC))
        assert ttc == Decimal(25000) and ht + tva == ttc and ht == Decimal(20964)

    def test_sans_tva_le_montant_est_ht_et_ttc(self):
        non = IdentiteFiscaleDuCabinet(regime_tva=RegimeTvaDuCabinet.NON_ASSUJETTI)
        assert ventiler(Decimal(25000), non) == (Decimal(25000), Decimal(0), Decimal(25000))


@exige_postgresql
class TestParLesRoutes:
    IDENTITE = "/souscription/facturation/identite-fiscale"

    def _echeance_reglee(self, client) -> str:
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        return next(e for e in vue["echeances"] if e["etat"] == "REGLEE")["debut"]

    def test_saisie_plus_tard_puis_facture_figee(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        periode = self._echeance_reglee(client)
        facture = f"/souscription/honoraires/{BATIMENT}/echeances/{periode}/facture"
        attente = client.get(facture)
        assert attente.status_code == 409 and "le NIU du cabinet" in attente.text
        assert (
            client.get(f"/souscription/honoraires/{BATIMENT}").json()["facture_disponible"] is False
        )

        client.cookies.clear()
        ouvrir_une_session(client, DIRECTRICE)
        partielle = client.put(self.IDENTITE, json={"niu": "m012345678901x"})
        assert partielle.status_code == 200, partielle.text
        assert partielle.json()["identite"]["niu"] == "M012345678901X"
        assert partielle.json()["complete"] is False
        complete = client.put(self.IDENTITE, json=ASSUJETTI)
        assert complete.json()["complete"] is True and complete.json()["manques"] == []

        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        assert (
            client.get(f"/souscription/honoraires/{BATIMENT}").json()["facture_disponible"] is True
        )
        emise = client.get(facture)
        assert emise.status_code == 200, emise.text
        texte = _texte(emise.content)
        assert "M012345678901X" in texte and "RC/DLA/2019/B/0421" in texte
        assert "19,25 %" in texte and "TOTAL TTC" in texte
        numero = emise.headers["content-disposition"].split('"')[1].removesuffix(".pdf")
        assert numero.startswith("F-") and numero.endswith("-0001")

        # La direction corrige le RCCM : la facture déjà émise ne change pas.
        client.cookies.clear()
        ouvrir_une_session(client, DIRECTRICE)
        client.put(self.IDENTITE, json={**ASSUJETTI, "rccm": "RC/DLA/2024/B/9999"})
        relue = client.get(facture)
        assert relue.content == emise.content

    def test_une_echeance_non_reglee_ne_se_facture_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, DIRECTRICE)
        client.put(self.IDENTITE, json=ASSUJETTI)
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        due = next(e for e in vue["echeances"] if e["etat"] in ("A_REGLER", "EN_RETARD"))
        refus = client.get(f"/souscription/honoraires/{BATIMENT}/echeances/{due['debut']}/facture")
        assert refus.status_code == 409 and "réglée" in refus.text

    def test_la_direction_seule_saisit_l_identite(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        assert client.put(self.IDENTITE, json=ASSUJETTI).status_code == 403
        client.cookies.clear()
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        assert client.get(self.IDENTITE).status_code == 403
