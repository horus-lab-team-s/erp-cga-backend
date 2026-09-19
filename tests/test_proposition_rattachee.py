"""La TVA récupérable dépend du régime du portefeuille, pas de celui de la requête.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 73)

La route de proposition écrivait « le régime n'est pas demandé : il est lu au
portefeuille ». Aucune ligne ne le lisait : la TVA récupérable se décidait sur le
régime du destinataire **déclaré dans la facture envoyée**. Sur ETS TCHOUMBA & FILS,
au régime IGS, déclarer « REEL » faisait apparaître 231 000 FCFA de TVA déductible.
Et une facture adressée à une autre entreprise se proposait sans refus.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.contextes.comptabilite.api import FactureDUnAutreDossier, rattacher_au_dossier
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.api import RegimeEmetteur
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import app

CONFORME = FACTURES_DEMO["F-2026-0413"]
#: Le destinataire de la facture conforme, au réel.
BATIMENT_PLUS = CONFORME.destinataire.niu
#: Une entreprise au régime IGS : elle ne récupère aucune TVA.
TCHOUMBA = "P019876543210K"
#: Le compte de TVA déductible du plan d'imputation par défaut.
TVA_DEDUCTIBLE = "4451"


def _avec_destinataire(*, niu, regime):
    donnees = CONFORME.model_dump(mode="json")
    donnees["destinataire"] = {**donnees["destinataire"], "niu": niu, "regime": regime}
    return donnees


class TestLeRattachement:
    def test_le_regime_du_portefeuille_remplace_celui_de_la_facture_et_le_dit(self):
        facture = CONFORME.model_copy(
            update={"destinataire": CONFORME.destinataire.model_copy(update={"niu": TCHOUMBA})}
        )
        rattachee, rectifications = rattacher_au_dossier(
            facture, niu=TCHOUMBA, regime=RegimeEmetteur.IGS
        )
        assert rattachee.destinataire.regime is RegimeEmetteur.IGS
        (phrase,) = rectifications
        assert "déclaré REEL" in phrase and "portefeuille IGS" in phrase

    def test_un_regime_identique_ne_rectifie_rien(self):
        rattachee, rectifications = rattacher_au_dossier(
            CONFORME, niu=BATIMENT_PLUS, regime=RegimeEmetteur.REEL
        )
        assert rattachee == CONFORME
        assert rectifications == []

    def test_un_destinataire_sans_niu_recoit_celui_du_dossier(self):
        facture = CONFORME.model_copy(
            update={"destinataire": CONFORME.destinataire.model_copy(update={"niu": None})}
        )
        rattachee, rectifications = rattacher_au_dossier(
            facture, niu=BATIMENT_PLUS, regime=RegimeEmetteur.REEL
        )
        assert rattachee.destinataire.niu == BATIMENT_PLUS
        assert any("sans NIU" in r for r in rectifications)

    def test_une_facture_adressee_ailleurs_est_refusee(self):
        with pytest.raises(FactureDUnAutreDossier, match="adressée à M081234567890P"):
            rattacher_au_dossier(CONFORME, niu=TCHOUMBA, regime=RegimeEmetteur.IGS)


class TestParLaRoute:
    """La sonde du pas 73, rejouée."""

    @pytest.fixture
    def client(self):
        with TestClient(app) as client:
            assert client.post(
                "/transverse/session",
                json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
            ).status_code == 200
            yield client

    def _proposer(self, client, dossier, facture):
        return client.post(
            f"/comptabilite/dossiers/{dossier}/propositions",
            json={"facture": facture, "journal": "AC"},
        )

    def test_declarer_le_reel_ne_fait_plus_recuperer_la_tva_d_une_entreprise_igs(self, client):
        reponse = self._proposer(
            client, TCHOUMBA, _avec_destinataire(niu=TCHOUMBA, regime="REEL")
        )
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        comptes = {
            ligne["compte"]: Decimal(ligne["montant"]) for ligne in corps["saisie"]["lignes"]
        }
        assert TVA_DEDUCTIBLE not in comptes, corps["saisie"]["lignes"]
        # ⚠️ La contre-épreuve : la charge porte la TVA, et l'écriture reste équilibrée.
        assert comptes["401"] == CONFORME.montants.total_ttc
        assert any("portefeuille IGS" in r for r in corps["rectifications"])

    def test_declarer_l_igs_ne_prive_pas_de_tva_une_entreprise_au_reel(self, client):
        reponse = self._proposer(
            client, BATIMENT_PLUS, _avec_destinataire(niu=BATIMENT_PLUS, regime="IGS")
        )
        comptes = {ligne["compte"] for ligne in reponse.json()["saisie"]["lignes"]}
        assert TVA_DEDUCTIBLE in comptes

    def test_le_controle_de_conformite_lit_le_regime_rattache(self, client):
        """⚠️ La règle FAC-ACH-007 (TVA non déductible sur règlement en espèces) ne vise
        que les destinataires au réel. Contrôlée sur la facture **déclarée** au réel,
        elle annonçait à une entreprise IGS une TVA non déductible qu'elle ne déduit
        jamais. Une mutation qui contrôlait la facture d'origine survivait sans ce cas.
        """
        especes = FACTURES_DEMO["F-2026-0412"].model_dump(mode="json")
        especes["destinataire"] = {**especes["destinataire"], "niu": TCHOUMBA, "regime": "REEL"}
        corps = self._proposer(client, TCHOUMBA, especes).json()
        assert corps["rapport"]["constats"] == [], corps["rapport"]["constats"]
        # ⚠️ La contre-épreuve : chez son vrai destinataire, au réel, la règle parle.
        chez_lui = self._proposer(
            client, BATIMENT_PLUS, FACTURES_DEMO["F-2026-0412"].model_dump(mode="json")
        ).json()
        assert "FAC-ACH-007" in str(chez_lui["rapport"]["constats"])

    def test_une_facture_d_une_autre_entreprise_est_refusee(self, client):
        reponse = self._proposer(client, TCHOUMBA, CONFORME.model_dump(mode="json"))
        assert reponse.status_code == 409, reponse.text
        assert "adressée à M081234567890P" in reponse.json()["detail"]

    def test_un_dossier_inconnu_repond_404(self, client):
        reponse = self._proposer(
            client, "M000000000000X", _avec_destinataire(niu=None, regime="REEL")
        )
        # Le réviseur est transverse : l'accès passe, c'est le portefeuille qui ne connaît pas.
        assert reponse.status_code == 404, reponse.text
