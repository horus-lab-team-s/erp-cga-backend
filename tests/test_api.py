"""L'API exposée."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import creer_application


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(creer_application())


def test_sante(client: TestClient):
    assert client.get("/sante").json()["etat"] == "operationnel"


class TestReferentiel:
    def test_lecture_datee(self, client: TestClient):
        reponse = client.get(
            "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": "2026-07-15"}
        )
        assert reponse.status_code == 200
        assert reponse.json()["valeur"] == 19.25

    def test_la_date_est_obligatoire(self, client: TestClient):
        # Aucune lecture de paramètre sans date : le contrat de l'API le refuse.
        assert client.get("/referentiel/parametres/TVA_TAUX_GENERAL").status_code == 422

    def test_code_inconnu(self, client: TestClient):
        reponse = client.get(
            "/referentiel/parametres/CODE_INEXISTANT", params={"a_la_date": "2026-07-15"}
        )
        assert reponse.status_code == 404

    def test_date_hors_de_toute_version(self, client: TestClient):
        reponse = client.get(
            "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": "1990-01-01"}
        )
        assert reponse.status_code == 422

    def test_referentiel_pas_encore_opposable(self, client: TestClient):
        etat = client.get("/referentiel/validation", params={"a_la_date": "2026-07-15"}).json()
        assert etat["opposable"] is False
        assert etat["non_valides"]


class TestConformite:
    def test_catalogue_des_regles(self, client: TestClient):
        codes = {r["code"] for r in client.get("/conformite/regles").json()}
        assert codes == {"FAC-ID-003", "FAC-ACH-007", "FAC-CAL-002", "FAC-DOC-011", "FAC-VRA-005"}

    def test_controle_d_une_facture_bloquante(self, client: TestClient):
        corps = client.get("/conformite/demonstration/F-2026-0414").json()
        assert corps["verdict"]["comptabilisation_interdite"] is True
        assert corps["verdict"]["glyphe"] == "⬣"
        assert "536 625" in corps["verdict"]["detail"].replace(" ", " ")

    def test_controle_d_une_facture_majeure(self, client: TestClient):
        corps = client.get("/conformite/demonstration/F-2026-0412").json()
        assert corps["verdict"]["comptabilisation_interdite"] is False
        assert "379 350" in corps["verdict"]["titre"].replace(" ", " ")

    def test_controle_d_une_facture_conforme(self, client: TestClient):
        corps = client.get("/conformite/demonstration/F-2026-0413").json()
        assert corps["rapport"]["constats"] == []
        assert corps["verdict"]["titre"].startswith("Conforme")

    def test_le_rapport_conserve_les_parametres_employes(self, client: TestClient):
        # Traçabilité : la valeur exacte du seuil employée doit figurer au rapport, sans
        # quoi un contrôle de 2026 ne serait plus reproductible en 2028.
        rapport = client.get("/conformite/demonstration/F-2026-0412").json()["rapport"]
        codes = {p["code"] for p in rapport["parametres_employes"]}
        assert "SEUIL_ESPECES_DEDUCTIBILITE_TVA" in codes
        seuil = next(
            p
            for p in rapport["parametres_employes"]
            if p["code"] == "SEUIL_ESPECES_DEDUCTIBILITE_TVA"
        )
        assert seuil["valeur"] == 500000
        assert seuil["applicable_du"] == "2019-01-01"

    def test_avertissement_de_non_validation(self, client: TestClient):
        verdict = client.get("/conformite/demonstration/F-2026-0412").json()["verdict"]
        assert verdict["avertissement_validation"] is not None
        assert "opposable" in verdict["avertissement_validation"]

    def test_facture_de_demonstration_inconnue(self, client: TestClient):
        assert client.get("/conformite/demonstration/F-0000-0000").status_code == 404

    def test_controle_d_une_facture_transmise(self, client: TestClient):
        facture = {
            "document": {"reference": "F-2026-9999", "date_emission": "2026-07-20"},
            "emetteur": {"denomination": "TEST", "niu": None, "regime": "REEL"},
            "montants": {"total_ht": "100000", "total_tva": "19250", "total_ttc": "119250"},
            "reglement": {"mode": "VIREMENT"},
            "lignes": [{"designation": "Ciment CPJ 42,5", "montant_ht": "100000"}],
        }
        corps = client.post("/conformite/controler", json=facture).json()
        assert corps["verdict"]["comptabilisation_interdite"] is True
        assert [c["code_regle"] for c in corps["rapport"]["constats"]] == ["FAC-ID-003"]
