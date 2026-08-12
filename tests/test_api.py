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

    def test_la_reponse_porte_la_facture_pour_les_donnees_extraites(self, client: TestClient):
        # § 8.2 : l'écran affiche les données extraites à côté des constats. Les
        # séparer en deux appels obligerait le front à recoller deux états.
        corps = client.get("/conformite/demonstration/F-2026-0412").json()
        facture = corps["facture"]
        assert facture["emetteur"]["denomination"] == "QUINCAILLERIE DU WOURI"
        assert facture["montants"]["total_ttc"] == "2350000"
        assert facture["reglement"]["mode"] == "ESPECES"
        assert len(facture["lignes"]) == 2

    def test_controle_groupe_de_tout_le_flux(self, client: TestClient):
        rapports = client.get("/conformite/demonstration/rapports").json()
        assert len(rapports) >= 25, "E03 exige 25 lignes visibles : le jeu doit les fournir"
        # La route ne doit pas être capturée par /demonstration/{reference}.
        assert all("facture" in r and "verdict" in r for r in rapports)
        # Distribution réaliste : l'écran ne doit pas être uniformément vert ni rouge.
        gravites = {r["verdict"]["comptabilisation_interdite"] for r in rapports}
        assert gravites == {True, False}

    def test_aucune_regle_en_echec_sur_tout_le_flux(self, client: TestClient):
        for r in client.get("/conformite/demonstration/rapports").json():
            assert not r["rapport"]["regles_en_echec"], r["rapport"]["reference_document"]

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


class TestVitrine:
    """Le contenu éditorial servi au site public.

    Ces routes sont en lecture seule et sans authentification : tout ce qu'elles
    rendent est déjà destiné à être affiché. Ce qu'on éprouve ici, c'est le
    contrat passé avec le site — pas la justesse du contenu, qui appartient au
    cabinet.
    """

    def test_le_sommaire_porte_les_articles_et_les_comptes(self, client: TestClient):
        # Les deux partent ensemble : le sommaire les affiche sur le même écran, et
        # deux allers-retours pour une page doubleraient la latence sur mobile.
        corps = client.get("/vitrine/articles").json()
        assert corps["articles"]
        assert set(corps["comptes_par_rubrique"])

    def test_le_sommaire_va_du_plus_recent_au_plus_ancien(self, client: TestClient):
        dates = [a["date"] for a in client.get("/vitrine/articles").json()["articles"]]
        assert dates == sorted(dates, reverse=True)

    def test_le_filtre_de_rubrique_restreint(self, client: TestClient):
        corps = client.get("/vitrine/articles", params={"rubrique": "annonces"}).json()
        assert corps["articles"]
        assert {a["rubrique"] for a in corps["articles"]} == {"annonces"}

    def test_une_rubrique_inconnue_montre_tout_plutot_qu_une_erreur(self, client: TestClient):
        """Un lien mal recopié doit montrer le blog, pas une page d'erreur."""
        tout = client.get("/vitrine/articles").json()["articles"]
        farfelu = client.get("/vitrine/articles", params={"rubrique": "n-importe-quoi"})
        assert farfelu.status_code == 200
        assert len(farfelu.json()["articles"]) == len(tout)

    def test_un_article_arrive_avec_ses_voisins_de_lecture(self, client: TestClient):
        slug = client.get("/vitrine/articles").json()["articles"][0]["slug"]
        corps = client.get(f"/vitrine/articles/{slug}").json()
        assert corps["article"]["slug"] == slug
        assert all(v["slug"] != slug for v in corps["voisins"])

    def test_un_article_inconnu_est_un_404(self, client: TestClient):
        assert client.get("/vitrine/articles/article-qui-n-existe-pas").status_code == 404

    def test_la_date_est_obligatoire_pour_l_annonce(self, client: TestClient):
        # Il n'existe volontairement aucune façon de demander « l'annonce courante » :
        # le serveur et le visiteur ne sont pas toujours dans le même fuseau.
        assert client.get("/vitrine/annonce").status_code == 422

    def test_hors_fenetre_l_annonce_est_nulle(self, client: TestClient):
        reponse = client.get("/vitrine/annonce", params={"a_la_date": "2000-01-01"})
        assert reponse.status_code == 200
        assert reponse.json() is None

    def test_les_institutions_sont_servies(self, client: TestClient):
        institutions = client.get("/vitrine/institutions").json()
        assert {i["cle"] for i in institutions} >= {"dgi", "cnps", "onecca", "ohada"}
