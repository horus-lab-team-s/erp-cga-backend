"""La DSF achetée en ligne, du prix arrêté par la direction à sa mission (19 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
Point 24 : « le module DSF en libre-service pour l'utilisateur en ligne ». L'adhérent
avait sa page « Ma DSF » ; qui n'était pas adhérent ne pouvait pas l'acheter.

⚠️ CE QUI EST GARDÉ
* sans prix arrêté par la direction, la DSF n'est PAS en vente : aucun tarif inventé ;
* le prix fixé, elle se commande et se règle comme les autres services ;
* payée, la commande se présente dans la file « à ouvrir » des missions, et ne s'ouvre
  qu'en DSF ciblée, une seule fois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.horloge import horloge_figee
from tests.gerant_d_essai import fixer_les_prix_du_comptoir

PROSPECT = {
    "nom": "ABENA",
    "prenom": "Paul",
    "courriel": "paul.abena@exemple.cm",
    "telephone": "699123456",
    "denomination": "ETS ABENA ET FILS",
    "niu": "P012345678901Q",
    "chiffre_affaires_declare": "18000000",
}


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true")
    configuration.cache_clear()
    reinitialiser_atelier()
    from app.contextes.souscription.adaptateurs.entrant.routes_http import (
        reinitialiser_comptoir,
    )

    reinitialiser_comptoir()
    fixer_les_prix_du_comptoir()
    with horloge_figee(datetime(2026, 10, 19, 10, 0)):
        yield TestClient(creer_application())
    configuration.cache_clear()


def _connecter(client: TestClient, courriel: str) -> None:
    r = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert r.status_code == 200, r.text


def _service_dsf(client: TestClient) -> dict:
    services = client.get("/souscription/services", params={"a_la_date": "2026-10-19"}).json()
    return next(s for s in services if s["code"] == "DSF")


def test_sans_prix_arrete_la_dsf_n_est_pas_en_vente(client):
    """Le devis s'établit, mais il ne se règle pas : aucun tarif n'est inventé."""
    assert _service_dsf(client)["souscriptible_en_ligne"] is False
    devis = client.post(
        "/souscription/devis", json={"prospect": PROSPECT, "lignes": [{"service": "DSF"}]}
    ).json()
    assert devis["complet"] is False
    (ligne,) = devis["lignes"]
    assert ligne["montant"] is None and "cabinet" in ligne["precision"]
    assert "seuil" not in ligne["precision"]
    engagement = client.post(f"/souscription/devis/{devis['reference']}/engagement", json={})
    assert engagement.status_code >= 400, engagement.text


def test_du_prix_fixe_a_la_mission(client):
    _connecter(client, "b.mballa@cga-brcg.cm")
    fixation = client.post(
        "/souscription/tarifs",
        json={
            "service": "DSF",
            "formule": "DSF_LIBERATOIRE",
            "montant": "150000",
            "a_partir_du": "2026-10-19",
            "motif": "Prix de la DSF seule arrêté par la direction.",
        },
    )
    assert fixation.status_code == 201, fixation.text
    client.cookies.clear()
    assert _service_dsf(client)["souscriptible_en_ligne"] is True

    devis = client.post(
        "/souscription/devis", json={"prospect": PROSPECT, "lignes": [{"service": "DSF"}]}
    )
    assert devis.status_code == 201, devis.text
    engagement = client.post(f"/souscription/devis/{devis.json()['reference']}/engagement", json={})
    assert engagement.status_code == 201, engagement.text
    commande = engagement.json()["souscription"]["reference"]

    _connecter(client, "p.moukouri@cga-brcg.cm")
    (attente,) = [d for d in client.get("/missions/a-ouvrir").json() if d["reference"] == commande]
    assert attente["client"] == "ETS ABENA ET FILS"
    assert "P012345678901Q" in attente["message"]

    mauvaise = client.post(
        "/missions",
        json={"type": "ANALYSE_CONTRAT", "objet": "DSF", "reference_commerciale": commande},
    )
    assert mauvaise.status_code == 409, mauvaise.text
    ouverte = client.post(
        "/missions",
        json={
            "type": "DSF_CIBLEE",
            "objet": "DSF de l'exercice 2025",
            "reference_commerciale": commande,
            "dossier_niu": "P012345678901Q",
            "exercice": "2025",
        },
    )
    assert ouverte.status_code == 201, ouverte.text
    assert ouverte.json()["client_courriel"] == "paul.abena@exemple.cm"
    assert commande not in [d["reference"] for d in client.get("/missions/a-ouvrir").json()]
    seconde = client.post(
        "/missions",
        json={
            "type": "DSF_CIBLEE",
            "objet": "Encore",
            "reference_commerciale": commande,
            "dossier_niu": "P012345678901Q",
            "exercice": "2025",
        },
    )
    assert seconde.status_code == 409
