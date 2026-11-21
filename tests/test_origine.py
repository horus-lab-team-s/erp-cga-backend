"""Une modification portant la session vient d'un site du cabinet (audit du 19 octobre 2026)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.contextes.transverse.adaptateurs.entrant.dependances import NOM_TEMOIN
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.infrastructure import origine
from app.main import creer_application

COURRIEL = "l.fotso@cga-brcg.cm"


def _client() -> TestClient:
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": COURRIEL, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def test_le_temoin_porte_le_meme_nom_des_deux_cotes():
    assert origine._TEMOIN == NOM_TEMOIN


def test_un_post_venu_d_un_site_etranger_est_refuse():
    client = _client()
    reponse = client.post(
        "/transverse/notifications/lecture",
        json={"jusqu_au_rang": 1},
        headers={"Origin": "https://piege.exemple.com"},
    )
    assert reponse.status_code == 403
    assert "site du cabinet" in reponse.text


def test_un_post_venu_d_un_site_du_cabinet_passe():
    client = _client()
    autorisee = next(iter(origine.origines_du_cabinet()))
    reponse = client.post(
        "/transverse/notifications/lecture",
        json={"jusqu_au_rang": 1},
        headers={"Origin": autorisee},
    )
    assert reponse.status_code == 200, reponse.text


def test_sans_origine_rien_ne_change():
    """Les appels de serveur à serveur, le mobile et Tara ne posent pas d'`Origin`."""
    client = _client()
    reponse = client.post("/transverse/notifications/lecture", json={"jusqu_au_rang": 1})
    assert reponse.status_code == 200, reponse.text


def test_sans_temoin_une_origine_etrangere_n_a_rien_a_usurper():
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session",
        json={"courriel": COURRIEL, "mot_de_passe": MOT_DE_PASSE_DEMO},
        headers={"Origin": "https://piege.exemple.com"},
    )
    assert reponse.status_code == 200, reponse.text


def test_une_lecture_n_est_pas_controlee():
    client = _client()
    reponse = client.get("/transverse/moi", headers={"Origin": "https://piege.exemple.com"})
    assert reponse.status_code == 200
