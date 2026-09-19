"""Le fichier du personnel : un matricule ne se réattribue pas, un contrat ne se chevauche pas.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 89)

Avant de brancher l'embauche à l'écran, les routes du Social ont été éprouvées, par un
comptable qui suit LA COLOMBE, SARL BATIMENT PLUS et AGRO :

- inscrire sur LA COLOMBE un salarié au matricule `SAL-0004` renommait NKOULOU, salarié
  de BATIMENT, en « INTRUS » et le faisait passer chez LA COLOMBE avec son contrat : il
  disparaissait de la déclaration sociale de son employeur ;
- ouvrir un contrat par l'adresse de LA COLOMBE sur `SAL-0001` déplaçait le salarié d'AGRO ;
- un second contrat chevauchant le premier était accepté, et la paie du mois devenait
  impossible ;
- une fin de contrat antérieure à son début rendait 500.

Et le seul chemin pour changer un contrat, « on le ferme et on en ouvre un nouveau »,
n'avait pas de route pour fermer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contextes.social.api import vider_les_magasins_du_social
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, NIU_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

COLOMBE = NIU_DEMO["COLOMBE"]
BATIMENT = NIU_DEMO["BATIMENT"]
AGRO = NIU_DEMO["AGRO"]
JOUR = {"a_la_date": "2026-09-15"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    reinitialiser_atelier()
    vider_les_magasins_du_social()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session",
        json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    yield client
    vider_les_magasins_du_social()


def _personnel(client: TestClient, dossier: str) -> dict[str, dict]:
    reponse = client.get(f"/social/dossiers/{dossier}/salaries", params=JOUR)
    assert reponse.status_code == 200, reponse.text
    return {s["matricule"]: s for s in reponse.json()}


def _contrat(client, dossier, matricule, **champs):
    return client.post(f"/social/dossiers/{dossier}/salaries/{matricule}/contrats", json=champs)


class TestUnMatriculeNeSeReattribuePas:
    def test_le_matricule_d_un_salarie_d_autrui_est_refuse_sans_le_nommer(self, client):
        avant = _personnel(client, BATIMENT)
        reponse = client.post(
            f"/social/dossiers/{COLOMBE}/salaries",
            json={"matricule": "SAL-0004", "nom": "INTRUS", "prenom": "X"},
        )
        assert reponse.status_code == 409, reponse.text
        assert "NKOULOU" not in reponse.text and BATIMENT not in reponse.text
        assert _personnel(client, BATIMENT) == avant, "le salarié de BATIMENT a changé"
        assert "SAL-0004" not in _personnel(client, COLOMBE)

    def test_dans_le_meme_dossier_le_refus_dit_qui_porte_le_matricule(self, client):
        reponse = client.post(
            f"/social/dossiers/{BATIMENT}/salaries",
            json={"matricule": "SAL-0004", "nom": "DOUBLON", "prenom": "Y"},
        )
        assert reponse.status_code == 409
        assert "NKOULOU" in reponse.json()["detail"]
        assert _personnel(client, BATIMENT)["SAL-0004"]["nom"] == "NKOULOU"


class TestUnContratSOuvreDansSonDossier:
    def test_le_salarie_d_autrui_est_introuvable_comme_un_inconnu(self, client):
        avant = _personnel(client, AGRO)
        corps = {"type_contrat": "CDI", "debut": "2026-09-01", "salaire_base": "50000"}
        autrui = _contrat(client, COLOMBE, "SAL-0001", **corps)
        inconnu = _contrat(client, COLOMBE, "SAL-9999", **corps)
        assert autrui.status_code == inconnu.status_code == 404
        assert autrui.json()["detail"].replace("SAL-0001", "X") == inconnu.json()["detail"].replace(
            "SAL-9999", "X"
        )
        assert _personnel(client, AGRO) == avant

    def test_un_contrat_qui_chevauche_est_refuse_et_la_paie_reste_possible(self, client):
        reponse = _contrat(
            client,
            BATIMENT,
            "SAL-0005",
            type_contrat="CDI",
            debut="2026-01-01",
            salaire_base="999999",
        )
        assert reponse.status_code == 409, reponse.text
        assert "clore le contrat en vigueur" in reponse.json()["detail"]
        assert (
            client.get(f"/social/dossiers/{BATIMENT}/bulletins/2026/8/SAL-0005").status_code == 200
        )

    def test_une_fin_avant_le_debut_est_une_erreur_de_saisie(self, client):
        reponse = _contrat(
            client,
            BATIMENT,
            "SAL-0005",
            type_contrat="CDD",
            debut="2027-09-01",
            fin="2027-01-01",
            salaire_base="1",
        )
        assert reponse.status_code == 422, reponse.text


class TestClorePuisOuvrir:
    def test_changer_de_contrat_se_fait_en_deux_gestes_et_garde_l_histoire(self, client):
        cloture = client.post(
            f"/social/dossiers/{BATIMENT}/salaries/SAL-0005/contrats/cloture",
            json={"le": "2026-10-01"},
        )
        assert cloture.status_code == 200, cloture.text
        assert cloture.json()["fin"] == "2026-10-01"
        nouveau = _contrat(
            client,
            BATIMENT,
            "SAL-0005",
            type_contrat="CDI",
            debut="2026-10-01",
            salaire_base="200000",
        )
        assert nouveau.status_code == 201, nouveau.text
        septembre = client.get(f"/social/dossiers/{BATIMENT}/bulletins/2026/9/SAL-0005").json()
        octobre = client.get(f"/social/dossiers/{BATIMENT}/bulletins/2026/10/SAL-0005").json()
        assert septembre["salaire_base"] == "150000"
        assert octobre["salaire_base"] == "200000"

    @pytest.mark.parametrize(
        ("matricule", "le"),
        [("SAL-0005", "2026-07-20"), ("SAL-0005", "2027-06-01"), ("SAL-0001", "2026-10-01")],
        ids=["au-debut", "apres-la-fin", "salarie-d-autrui"],
    )
    def test_rien_a_clore(self, client, matricule, le):
        reponse = client.post(
            f"/social/dossiers/{BATIMENT}/salaries/{matricule}/contrats/cloture", json={"le": le}
        )
        assert reponse.status_code == 404, reponse.text


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_inscrire_ouvrir_clore_rouvrir_d_une_requete_a_l_autre(self, plateforme):
        """⚠️ Le dépôt SQL tient le rattachement d'un salarié par requête : c'est lui qui
        faisait rattacher le salarié au dossier de l'adresse. Chaque geste est une requête."""
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        base = f"/social/dossiers/{COLOMBE}/salaries"
        assert (
            client.post(
                base, json={"matricule": "SAL-P89-1", "nom": "N", "prenom": "P"}
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"{base}/SAL-P89-1/contrats",
                json={"type_contrat": "CDD", "debut": "2026-09-01", "salaire_base": "90000"},
            ).status_code
            == 201
        )
        assert (
            client.post(f"{base}/SAL-P89-1/contrats/cloture", json={"le": "2026-10-01"}).status_code
            == 200
        )
        assert (
            client.post(
                f"{base}/SAL-P89-1/contrats",
                json={"type_contrat": "CDI", "debut": "2026-10-01", "salaire_base": "95000"},
            ).status_code
            == 201
        )
        assert (
            client.post(base, json={"matricule": "SAL-0004", "nom": "I", "prenom": "X"}).status_code
            == 409
        )


class TestLeBulletinRendSesTotaux:
    def test_le_net_rendu_exclut_les_avantages_en_nature(self, client):
        """⚠️ Pas 89 : le net, les charges et le coût employeur étaient calculés et jamais
        rendus. Un écran les aurait recalculés, et l'erreur classique est d'y laisser les
        avantages en nature, déjà fournis en nature."""
        b = client.get(f"/social/dossiers/{BATIMENT}/bulletins/2026/8/SAL-0004").json()
        retenues = sum(
            int(ligne["montant"]) for ligne in b["lignes"] if ligne["a_charge_du_salarie"]
        )
        patronales = sum(
            int(ligne["montant"]) for ligne in b["lignes"] if not ligne["a_charge_du_salarie"]
        )
        assert int(b["avantages_evalues"]) > 0, "le cas n'éprouve rien sans avantage en nature"
        assert int(b["net_a_payer"]) == int(b["salaire_base"]) + int(b["primes"]) - retenues
        assert int(b["brut_taxable"]) == int(b["salaire_base"]) + int(b["primes"]) + int(
            b["avantages_evalues"]
        )
        assert int(b["cout_employeur"]) == int(b["brut_taxable"]) + patronales
        assert int(b["retenues_salariales"]) == retenues
