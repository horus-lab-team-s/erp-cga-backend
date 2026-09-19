"""L'API exposée."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.adaptateurs.entrant.dependances import reinitialiser_atelier
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import creer_application


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(creer_application())


@pytest.fixture
def client_reviseur(application) -> TestClient:
    """Un client déjà connecté, en réviseur sur tout le portefeuille.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CES TESTS ONT BESOIN D'UNE SESSION

    Le contexte Conformité a longtemps répondu sans en réclamer aucune, et ces
    tests en profitaient sans le dire. Ils ont donc **passé au vert pendant
    tout le temps où le moteur était une API publique** : c'est le mode de
    panne le plus coûteux d'une suite de tests, celui qui certifie l'absence de
    garde au lieu de la signaler.

    Le réviseur est choisi parce qu'il cumule `LIRE_DOSSIER`, `LIRE_PIECE` et
    `CONTROLER_CONFORMITE`, et que sa portée couvre tout le portefeuille : la
    restriction par dossier se teste ailleurs, avec un compte à portée réduite.
    ─────────────────────────────────────────────────────────────────────────
    """
    return _connecte(application, "a.bouba@cga-brcg.cm")


@pytest.fixture
def client_adherent(application) -> TestClient:
    """Un adhérent, habilité au seul dossier SARL BATIMENT PLUS.

    C'est le compte qui a révélé le défaut : il ouvrait la boîte de réception
    et y lisait les six sociétés du portefeuille.
    """
    return _connecte(application, "jp.nkoa@batimentplus.cm")


@pytest.fixture
def client_administrateur(application) -> TestClient:
    """Le seul rôle délibérément privé de tout droit sur les dossiers."""
    return _connecte(application, "s.onana@cga-brcg.cm")


@pytest.fixture(scope="module")
def application():
    """Une seule application pour tout le module, et c'est nécessaire.

    ⚠️ `reinitialiser_atelier()` vide les caches — dont le magasin de sessions.
    Appelé par chaque fixture de client, il révoquait la session de la
    précédente : un test qui compare deux profils voyait alors le premier
    répondre 401, et concluait à un refus là où il n'y avait qu'un atelier
    remis à zéro sous ses pieds.
    """
    reinitialiser_atelier()
    return creer_application()


def _connecte(application, courriel: str) -> TestClient:
    client = TestClient(application)
    reponse = client.post(
        "/transverse/session",
        json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return client


#: Le minimum qu'accepte le moteur, pour éprouver une autorisation et non un schéma.
FACTURE_MINIMALE = {
    "document": {
        "type": "FACTURE_ACHAT",
        "reference": "F-TEST-0001",
        "date_emission": "2026-07-15",
    },
    "emetteur": {"denomination": "FOURNISSEUR", "niu": "M011111111111A"},
    "montants": {"total_ht": "1000000", "total_tva": "192500", "total_ttc": "1192500"},
}


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
    def test_catalogue_des_regles(self, client_reviseur: TestClient):
        codes = {r["code"] for r in client_reviseur.get("/conformite/regles").json()}
        assert codes == {"FAC-ID-003", "FAC-ACH-007", "FAC-CAL-002", "FAC-DOC-011", "FAC-VRA-005"}

    def test_controle_d_une_facture_bloquante(self, client_reviseur: TestClient):
        corps = client_reviseur.get("/conformite/demonstration/F-2026-0414").json()
        assert corps["verdict"]["comptabilisation_interdite"] is True
        assert corps["verdict"]["glyphe"] == "⬣"
        assert "536 625" in corps["verdict"]["detail"].replace(" ", " ")

    def test_controle_d_une_facture_majeure(self, client_reviseur: TestClient):
        corps = client_reviseur.get("/conformite/demonstration/F-2026-0412").json()
        assert corps["verdict"]["comptabilisation_interdite"] is False
        assert "379 350" in corps["verdict"]["titre"].replace(" ", " ")

    def test_controle_d_une_facture_conforme(self, client_reviseur: TestClient):
        corps = client_reviseur.get("/conformite/demonstration/F-2026-0413").json()
        assert corps["rapport"]["constats"] == []
        assert corps["verdict"]["titre"].startswith("Conforme")

    def test_le_rapport_conserve_les_parametres_employes(self, client_reviseur: TestClient):
        # Traçabilité : la valeur exacte du seuil employée doit figurer au rapport, sans
        # quoi un contrôle de 2026 ne serait plus reproductible en 2028.
        rapport = client_reviseur.get("/conformite/demonstration/F-2026-0412").json()["rapport"]
        codes = {p["code"] for p in rapport["parametres_employes"]}
        assert "SEUIL_ESPECES_DEDUCTIBILITE_TVA" in codes
        seuil = next(
            p
            for p in rapport["parametres_employes"]
            if p["code"] == "SEUIL_ESPECES_DEDUCTIBILITE_TVA"
        )
        assert seuil["valeur"] == 100000
        assert seuil["applicable_du"] == "2019-01-01"

    def test_avertissement_de_non_validation(self, client_reviseur: TestClient):
        verdict = client_reviseur.get("/conformite/demonstration/F-2026-0412").json()["verdict"]
        assert verdict["avertissement_validation"] is not None
        assert "opposable" in verdict["avertissement_validation"]

    def test_la_reponse_porte_la_facture_pour_les_donnees_extraites(
        self, client_reviseur: TestClient
    ):
        # § 8.2 : l'écran affiche les données extraites à côté des constats. Les
        # séparer en deux appels obligerait le front à recoller deux états.
        corps = client_reviseur.get("/conformite/demonstration/F-2026-0412").json()
        facture = corps["facture"]
        assert facture["emetteur"]["denomination"] == "QUINCAILLERIE DU WOURI"
        assert facture["montants"]["total_ttc"] == "2350000"
        assert facture["reglement"]["mode"] == "ESPECES"
        assert len(facture["lignes"]) == 2

    def test_controle_groupe_de_tout_le_flux(self, client_reviseur: TestClient):
        rapports = client_reviseur.get("/conformite/demonstration/rapports").json()
        assert len(rapports) >= 25, "E03 exige 25 lignes visibles : le jeu doit les fournir"
        # La route ne doit pas être capturée par /demonstration/{reference}.
        assert all("facture" in r and "verdict" in r for r in rapports)
        # Distribution réaliste : l'écran ne doit pas être uniformément vert ni rouge.
        gravites = {r["verdict"]["comptabilisation_interdite"] for r in rapports}
        assert gravites == {True, False}

    def test_aucune_regle_en_echec_sur_tout_le_flux(self, client_reviseur: TestClient):
        for r in client_reviseur.get("/conformite/demonstration/rapports").json():
            assert not r["rapport"]["regles_en_echec"], r["rapport"]["reference_document"]

    def test_facture_de_demonstration_inconnue(self, client_reviseur: TestClient):
        assert client_reviseur.get("/conformite/demonstration/F-0000-0000").status_code == 404

    def test_controle_d_une_facture_transmise(self, client_reviseur: TestClient):
        facture = {
            "document": {"reference": "F-2026-9999", "date_emission": "2026-07-20"},
            "emetteur": {"denomination": "TEST", "niu": None, "regime": "REEL"},
            "montants": {"total_ht": "100000", "total_tva": "19250", "total_ttc": "119250"},
            "reglement": {"mode": "VIREMENT"},
            "lignes": [{"designation": "Ciment CPJ 42,5", "montant_ht": "100000"}],
        }
        corps = client_reviseur.post("/conformite/controler", json=facture).json()
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


class TestConformiteFermee:
    """Le contexte Conformité ne répond plus sans session.

    ─────────────────────────────────────────────────────────────────────────
    LE TEST QUI MANQUAIT, ET CE QU'IL AURAIT COÛTÉ

    Ces cinq routes ont répondu `200` à un appelant anonyme pendant toute la
    vie du projet. Aucun test ne l'a vu, parce qu'aucun test ne demandait
    « et si personne n'est connecté ? » : ils validaient tous le chemin
    nominal, avec un client qui se trouvait n'avoir aucune session — et qui
    passait quand même.

    Constaté en recette : soixante appels anonymes du moteur en 0,3 seconde,
    tous servis, sans limiteur. Le moteur de conformité est le produit ; il
    était une API publique gratuite.

    D'où la forme de ces tests : ils n'affirment pas qu'une route **marche**,
    ils affirment qu'elle **refuse**. Les deux se testent, et seule la seconde
    famille attrape ce genre de trou.
    ─────────────────────────────────────────────────────────────────────────
    """

    ROUTES_FERMEES = [
        ("GET", "/conformite/regles"),
        ("GET", "/conformite/demonstration"),
        ("GET", "/conformite/demonstration/rapports"),
        ("GET", "/conformite/demonstration/F-2026-0412"),
    ]

    @pytest.mark.parametrize(("methode", "chemin"), ROUTES_FERMEES)
    def test_sans_session_tout_est_401(self, client: TestClient, methode: str, chemin: str):
        assert client.request(methode, chemin).status_code == 401

    def test_le_moteur_ne_tourne_pas_pour_un_anonyme(self, client: TestClient):
        assert client.post("/conformite/controler", json=FACTURE_MINIMALE).status_code == 401

    def test_un_adherent_ne_fait_pas_tourner_le_moteur(self, client_adherent: TestClient):
        """Lire une pièce et faire tourner le moteur sont deux droits distincts.

        L'adhérent détient `LIRE_PIECE` : il consulte les rapports de son
        dossier. Il ne détient pas `CONTROLER_CONFORMITE` — décider qu'une
        facture est conforme est un acte professionnel qui engage le cabinet.
        """
        reponse = client_adherent.post("/conformite/controler", json=FACTURE_MINIMALE)
        assert reponse.status_code == 403
        assert "CONTROLER_CONFORMITE" in reponse.json()["detail"]

    def test_la_boite_de_reception_est_restreinte_au_perimetre(
        self, client_adherent: TestClient, client_reviseur: TestClient
    ):
        """Le défaut central : six sociétés visibles par un adhérent d'une seule.

        La boîte de réception affichait tout le flux du cabinet à quiconque
        tenait une session. Le jeu de données est fictif ; la liste des raisons
        sociales du portefeuille ne l'était pas.
        """
        tout = client_reviseur.get("/conformite/demonstration/rapports").json()
        sien = client_adherent.get("/conformite/demonstration/rapports").json()

        destinataires = {
            (r["facture"].get("destinataire") or {}).get("niu") for r in sien
        }
        assert destinataires == {"M081234567890P"}, (
            "l'adhérent voit un dossier qui n'est pas le sien"
        )
        assert 0 < len(sien) < len(tout)

    def test_une_piece_hors_perimetre_est_introuvable_et_non_refusee(
        self, client_adherent: TestClient
    ):
        """404, pas 403 : un 403 confirmerait que la pièce existe.

        Un adhérent qui essaierait des références apprendrait, par la
        différence entre les deux codes, quelles factures le cabinet détient
        pour ses concurrents.
        """
        voisine = client_adherent.get("/conformite/demonstration/F-2026-0414")
        assert voisine.status_code == 404
        inexistante = client_adherent.get("/conformite/demonstration/F-0000-0000")
        assert inexistante.status_code == 404

    def test_le_404_ne_liste_plus_les_references_disponibles(
        self, client_reviseur: TestClient
    ):
        detail = client_reviseur.get("/conformite/demonstration/F-0000-0000").json()["detail"]
        assert "Disponibles" not in detail

    def test_un_administrateur_ne_voit_aucune_piece(self, client_administrateur: TestClient):
        """Le rôle conçu sans aucun droit sur les dossiers, et qui les ouvrait tous."""
        assert client_administrateur.get("/conformite/demonstration/rapports").status_code == 403
        assert client_administrateur.get("/conformite/regles").status_code == 403
