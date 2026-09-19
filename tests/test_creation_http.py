"""La création d'entreprise par ses routes : ce que la couche HTTP n'avait jamais éprouvé.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 82)

Le domaine de la création était testé ; ses routes, jamais. Deux défauts vivaient donc
dans la couche que personne n'appelait :

- les écritures acceptaient une date de la requête : un dossier au dépôt CFCE du
  30/07/2026 franchissait le suivi d'immatriculation le 01/01/2019 ;
- en mémoire, la conversion ne rangeait l'entreprise nulle part : la conversion
  réussissait, et l'entreprise restait introuvable au portefeuille.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.creation_entreprise.adaptateurs.entrant import routes_http as creations
from app.contextes.portefeuille.adaptateurs.sortant.magasins_memoire import (
    vider_les_entreprises_en_memoire,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

LE_JOUR = datetime(2026, 9, 14, 10, 0)
FORMALITES = "p.moukouri@cga-brcg.cm"


@pytest.fixture
def client():
    reinitialiser_atelier()
    creations._depot_memoire.cache_clear()
    vider_les_entreprises_en_memoire()
    with horloge_figee(LE_JOUR), TestClient(creer_application()) as c:
        assert c.post(
            "/transverse/session", json={"courriel": FORMALITES, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code == 200
        yield c
    creations._depot_memoire.cache_clear()
    vider_les_entreprises_en_memoire()


class TestLaDateDesEcritures:
    def test_une_etape_se_date_du_jour_quelle_que_soit_la_requete(self, client):
        """⚠️ La sonde du pas 82 : `a_la_date=2019-01-01` n'est plus lu."""
        reponse = client.post(
            "/creations/CR-2026-0013/etape?a_la_date=2019-01-01",
            json={"vers": "SUIVI_IMMATRICULATION"},
        )
        assert reponse.status_code == 200, reponse.text
        dernier = reponse.json()["jalons"][-1]
        assert (dernier["etape"], dernier["survenu_le"]) == ("SUIVI_IMMATRICULATION", "2026-09-14")
        assert dernier["par"] == "C-007", "le jalon nomme son auteur"

    def test_l_ouverture_se_date_du_jour(self, client):
        reponse = client.post(
            "/creations?a_la_date=2020-01-01",
            json={
                "reference": "CR-2026-0099",
                "fondateur": {
                    "nom": "EKANI", "prenom": "Paul", "courriel": "p.ekani@exemple.cm",
                    "telephone": "+237690000000", "piece_identite": "CNI 1234567890",
                },
                "denomination_souhaitee": "EKANI TRANSPORTS",
                "forme_juridique": "SARL",
                "activite": "Transport de marchandises",
                "siege": "Douala, Bonabéri",
                "capital": "1000000",
            },
        )
        assert reponse.status_code == 201, reponse.text
        assert reponse.json()["ouvert_le"] == "2026-09-14"

    def test_un_identifiant_obtenu_dans_le_futur_est_refuse(self, client):
        reponse = client.post(
            "/creations/CR-2026-0014/identifiants",
            json={"niu": "M998877665544A", "niu_obtenu_le": "2026-12-01"},
        )
        assert reponse.status_code == 422
        assert "pas encore passée" in reponse.json()["detail"]


class TestLaConversion:
    def test_l_entreprise_convertie_est_au_portefeuille(self, client):
        """⚠️ La seconde sonde : en mémoire, l'entreprise restait introuvable (404)."""
        reponse = client.post(
            "/creations/CR-2026-0015/conversion",
            json={"regime": "REEL", "centre": "CDI", "adherent": True},
        )
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["enregistree_au_portefeuille"] is True
        fiche = client.get(f"/portefeuille/entreprises/{corps['niu']}")
        assert fiche.status_code == 200, fiche.text
        assert fiche.json()["denomination"] == corps["denomination"]
        # Convertir deux fois ne crée pas une seconde entreprise.
        assert client.post(
            "/creations/CR-2026-0015/conversion",
            json={"regime": "REEL", "centre": "CDI", "adherent": True},
        ).status_code == 409


class TestLeDroit:
    def test_le_comptable_ne_suit_pas_les_formalites(self):
        reinitialiser_atelier()
        with TestClient(creer_application()) as c:
            c.post(
                "/transverse/session",
                json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
            )
            assert c.post("/creations/CR-2026-0013/abandon", json={"motif": "x"}).status_code == 403
