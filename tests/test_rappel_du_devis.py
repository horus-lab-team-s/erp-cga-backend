"""Un devis au prix à confirmer prévient vraiment un responsable.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Quand un prix du devis n'est pas encore arrêté par le gérant, ou qu'une ligne se
chiffre sur étude, le devis ne se règle pas en ligne. L'application et la vitrine
promettaient « un responsable vous rappelle », et rien ne prévenait personne.

Ces cas tiennent :

  1. la demande entre dans la file du responsable, avec la référence du devis ;
  2. les coordonnées viennent du devis, jamais du corps de la requête ;
  3. aucun accord WhatsApp n'est déduit d'un numéro laissé pour un devis ;
  4. deux appuis ne font pas deux dossiers ;
  5. un devis dont tous les prix sont arrêtés se règle en ligne : la route refuse ;
  6. un devis inconnu répond 404.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import reinitialiser_dossiers
from app.contextes.souscription.adaptateurs.entrant.routes_http import reinitialiser_comptoir
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.gerant_d_essai import fixer_les_prix_du_comptoir

DIRECTION = "b.mballa@cga-brcg.cm"
TELEPHONE = "677000444"


@pytest.fixture
def client() -> Iterator[TestClient]:
    reinitialiser_atelier()
    reinitialiser_comptoir()
    # ⚠️ Les dossiers aussi : sans cela, les demandes des cas précédents, au même
    # numéro, se rattachent au dossier de celui-ci et faussent le compte.
    reinitialiser_dossiers()
    yield TestClient(creer_application())


def _devis(client: TestClient) -> str:
    r = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ESSAI",
                "prenom": "Rappel",
                "courriel": "rappel.essai@exemple.cm",
                "telephone": TELEPHONE,
                "denomination": "Rappel SARL",
            },
            "lignes": [{"service": "DOMICILIATION"}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["prix_arretes"] is False, "le cas suppose un prix non arrêté"
    return r.json()["reference"]


def _dossiers_du_numero(client: TestClient) -> list[dict]:
    r = client.post(
        "/transverse/session", json={"courriel": DIRECTION, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert r.status_code == 200, r.text
    dossiers = client.get("/acquisition/dossiers").json()
    return [d for d in dossiers if d["telephone"].endswith(TELEPHONE)]


def test_la_demande_entre_dans_la_file_avec_la_reference_du_devis(client):
    reference = _devis(client)
    r = client.post(f"/acquisition/devis/{reference}/rappel")
    assert r.status_code == 201, r.text
    assert TELEPHONE in r.json()["message"]

    (dossier,) = _dossiers_du_numero(client)
    fiche = client.get(f"/acquisition/dossiers/{dossier['reference']}").json()
    demande = fiche["demande"]
    assert reference in demande["message"]
    assert demande["nom"] == "Rappel ESSAI (Rappel SARL)"
    assert demande["courriel"] == "rappel.essai@exemple.cm"
    assert demande["service_souhaite"] == "DOMICILIATION"
    # ⚠️ Un numéro laissé pour un devis n'est pas un accord pour WhatsApp.
    assert demande["consentement"]["accorde"] is False


def test_le_corps_de_la_requete_est_ignore(client):
    reference = _devis(client)
    r = client.post(
        f"/acquisition/devis/{reference}/rappel",
        json={"nom": "Quelqu'un d'autre", "telephone": "699999999"},
    )
    assert r.status_code == 201, r.text
    (dossier,) = _dossiers_du_numero(client)
    assert "Quelqu'un" not in dossier["nom"]


def test_deux_appuis_ne_font_pas_deux_dossiers(client):
    reference = _devis(client)
    for _ in range(2):
        assert client.post(f"/acquisition/devis/{reference}/rappel").status_code == 201
    (dossier,) = _dossiers_du_numero(client)
    assert dossier["demandes"] == 2, "la seconde se rattache, et le responsable la voit"


def test_un_devis_aux_prix_arretes_se_regle_en_ligne(client):
    fixer_les_prix_du_comptoir()
    reference = _devis_fixe(client)
    r = client.post(f"/acquisition/devis/{reference}/rappel")
    assert r.status_code == 409, r.text
    assert "se règle en ligne" in r.json()["detail"]


def _devis_fixe(client: TestClient) -> str:
    r = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ESSAI",
                "prenom": "Fixe",
                "courriel": "fixe.essai@exemple.cm",
                "telephone": "677000445",
            },
            "lignes": [{"service": "DOMICILIATION"}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["prix_arretes"] is True
    return r.json()["reference"]


def test_un_devis_inconnu_repond_404(client):
    assert client.post("/acquisition/devis/DV-inconnu/rappel").status_code == 404
