"""Une entreprise qui souscrit en ligne rejoint le portefeuille (10 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Constaté à la recette du 10 novembre : une entreprise existante souscrit en ligne, paie,
l'administration vérifie l'identité et ouvre l'accès — puis plus rien. La file
d'inscription ne lisait que les dossiers commerciaux, et l'adhérent restait devant
« le cabinet finalise l'ouverture de votre dossier », sans fin.

  1. la souscription dont l'accès est ouvert rejoint la file, sous sa référence `SO-…` ;
  2. la direction l'inscrit par la même route, préremplie (dénomination, régime) ;
  3. le NIU inscrit est celui de la souscription, sur lequel l'accès a été ouvert ;
  4. un comptable ne l'inscrit pas ; une fois inscrite, elle sort de la file.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.portefeuille.adaptateurs.sortant.magasins_memoire import (
    vider_les_entreprises_en_memoire,
)
from app.contextes.souscription.adaptateurs.entrant.routes_http import reinitialiser_comptoir
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee
from tests.gerant_d_essai import fixer_les_prix_du_comptoir

INSTANT = datetime(2026, 9, 14, 10, 0)
NIU = "M092500077123K"


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true")
    configuration.cache_clear()
    reinitialiser_atelier()
    reinitialiser_comptoir()
    vider_les_entreprises_en_memoire()
    fixer_les_prix_du_comptoir()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())
    vider_les_entreprises_en_memoire()
    configuration.cache_clear()


def _cabinet(courriel: str) -> TestClient:
    cabinet = TestClient(creer_application())
    reponse = cabinet.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return cabinet


def _souscrire_et_ouvrir(client: TestClient) -> str:
    devis = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ETOA",
                "prenom": "Paul",
                "courriel": "p.etoa@exemple.cm",
                "telephone": "677889911",
                "denomination": "QUINCAILLERIE DU WOURI SARL",
                "niu": NIU,
                "chiffre_affaires_declare": "42000000",
            },
            "lignes": [{"service": "ADHESION", "formule": "LIBERATOIRE"}],
        },
    )
    assert devis.status_code == 201, devis.text
    engagement = client.post(f"/souscription/devis/{devis.json()['reference']}/engagement", json={})
    assert engagement.status_code == 201, engagement.text
    reference = engagement.json()["souscription"]["reference"]
    ouverture = _cabinet("s.onana@cga-brcg.cm").post(
        f"/souscription/souscriptions/{reference}/activation",
        json={"verification": "RCCM et CNI du gérant présentés au cabinet ce jour."},
    )
    assert ouverture.status_code == 200, ouverture.text
    return reference


def _inscription(niu: str = NIU) -> dict:
    return {
        "niu": niu,
        "denomination": "QUINCAILLERIE DU WOURI SARL",
        "forme_juridique": "SARL",
        "date_creation": "2019-03-01",
        "regime": "IGS",
        "centre": "CDI",
        "cloture_exercice_en_cours": "2026-12-31",
        "justification": "Carte de contribuable, RCCM et avis d'imposition vus au cabinet.",
    }


class TestLaSouscriptionRejointLePortefeuille:
    def test_elle_entre_dans_la_file_preremplie(self, client: TestClient):
        reference = _souscrire_et_ouvrir(client)
        file = _cabinet("b.mballa@cga-brcg.cm").get("/acquisition/inscriptions/en-attente")
        assert file.status_code == 200, file.text
        (ligne,) = [x for x in file.json() if x["reference"] == reference]
        assert ligne["inscriptible"] is True
        assert ligne["proposition"]["denomination"] == "QUINCAILLERIE DU WOURI SARL"
        assert ligne["proposition"]["regime"] == "IGS"
        assert ligne["proposition"]["niu"] == NIU

    def test_la_direction_l_inscrit_et_elle_sort_de_la_file(self, client: TestClient):
        reference = _souscrire_et_ouvrir(client)
        direction = _cabinet("b.mballa@cga-brcg.cm")
        chemin = f"/acquisition/dossiers/{reference}/inscription"
        reponse = direction.post(chemin, json=_inscription())
        assert reponse.status_code == 201, reponse.text
        assert reponse.json()["inscrit_sous"] == NIU
        fiche = direction.get(f"/portefeuille/entreprises/{NIU}")
        assert fiche.status_code == 200, fiche.text
        file = direction.get("/acquisition/inscriptions/en-attente").json()
        assert reference not in [x["reference"] for x in file]

    def test_le_niu_inscrit_est_celui_de_la_souscription(self, client: TestClient):
        reference = _souscrire_et_ouvrir(client)
        reponse = _cabinet("b.mballa@cga-brcg.cm").post(
            f"/acquisition/dossiers/{reference}/inscription", json=_inscription("M000000000000X")
        )
        assert reponse.status_code == 422, reponse.text
        assert "celui de la souscription" in reponse.json()["detail"]

    def test_un_comptable_ne_l_inscrit_pas(self, client: TestClient):
        reference = _souscrire_et_ouvrir(client)
        reponse = _cabinet("l.fotso@cga-brcg.cm").post(
            f"/acquisition/dossiers/{reference}/inscription", json=_inscription()
        )
        assert reponse.status_code == 403
