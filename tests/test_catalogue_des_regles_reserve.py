"""Le catalogue des règles est le produit du cabinet : un adhérent ne le lit pas.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 90)

La route `GET /conformite/regles` le disait elle-même : « le rulebook est le produit,
exactement ce que le cabinet vend et ce qu'un concurrent recopierait en une après-midi ».
Elle n'exigeait pourtant que `LIRE_DOSSIER`, que l'adhérent détient sur son propre
dossier. Essai : l'adhérent de SARL BATIMENT PLUS lisait le catalogue entier, **prédicats
exécutables compris**. Depuis la souscription en ligne (pas 83), devenir adhérent est à
la portée de tous.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application


def _client(courriel: str) -> TestClient:
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


@pytest.mark.parametrize(
    "courriel",
    ["jp.nkoa@batimentplus.cm", "g.atangana@inspection.cm"],
    ids=["adherent", "inspecteur"],
)
def test_hors_du_cabinet_on_ne_lit_pas_le_catalogue(courriel):
    """L'inspecteur des impôts, externe lui aussi, lit les dossiers qu'on lui ouvre, pas
    la mécanique de contrôle du cabinet."""
    reponse = _client(courriel).get("/conformite/regles")
    assert reponse.status_code == 403
    assert "predicat" not in reponse.text


@pytest.mark.parametrize(
    "courriel", ["l.fotso@cga-brcg.cm", "c.ndongo@cga-brcg.cm", "r.ebolo@cga-brcg.cm"]
)
def test_le_cabinet_le_lit_avec_ses_predicats(courriel):
    reponse = _client(courriel).get("/conformite/regles")
    assert reponse.status_code == 200, reponse.text
    assert reponse.json() and all("predicat" in regle for regle in reponse.json())
