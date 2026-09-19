"""Une date antérieure à l'histoire d'un dossier ne fait plus tomber le portefeuille.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 86)

La fiche 360° propose de résoudre les statuts d'un dossier à la date d'une pièce
ancienne. Essai réel : au 01/01/2019, SARL BATIMENT PLUS n'avait aucun régime connu (sa
première période commence le 15/03/2021). La route des statuts rendait **500**, et la
liste du portefeuille au même jour aussi : un seul dossier sans statut faisait tomber
tous les autres.

- Les statuts d'un dossier hors de son histoire rendent **409**, avec le message du
  domaine qui nomme les périodes connues. Pas 404 : le dossier existe.
- La liste à cette date **omet** les dossiers sans statut : le cabinet ne connaissait
  alors ni leur régime ni leur centre, il ne les suivait pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, NIU_DEMO, reinitialiser_atelier
from app.main import creer_application

BATIMENT = NIU_DEMO["BATIMENT"]


@pytest.fixture
def client() -> TestClient:
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session",
        json={"courriel": "b.mballa@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return client


class TestHorsDeLHistoire:
    def test_les_statuts_avant_la_premiere_periode_rendent_409_avec_les_periodes(self, client):
        reponse = client.get(
            f"/portefeuille/entreprises/{BATIMENT}/statuts", params={"a_la_date": "2019-01-01"}
        )
        assert reponse.status_code == 409, reponse.text
        assert "aucun régime connu au 2019-01-01" in reponse.json()["detail"]
        assert "2021-03-15" in reponse.json()["detail"]

    def test_la_liste_omet_ce_qui_n_existait_pas_et_garde_le_reste(self, client):
        du_jour = client.get("/portefeuille/entreprises", params={"a_la_date": "2026-09-15"})
        aujourd_hui = {d["niu"] for d in du_jour.json()}
        reponse = client.get("/portefeuille/entreprises", params={"a_la_date": "2019-01-01"})
        assert reponse.status_code == 200, reponse.text
        alors = {d["niu"] for d in reponse.json()}
        assert BATIMENT in aujourd_hui
        assert BATIMENT not in alors
        assert alors < aujourd_hui

    def test_dans_l_histoire_rien_ne_change(self, client):
        reponse = client.get(
            f"/portefeuille/entreprises/{BATIMENT}/statuts", params={"a_la_date": "2026-09-15"}
        )
        assert reponse.status_code == 200
        assert reponse.json()["regime"] == "REEL"
