"""L'échéancier : des taux lus au référentiel, des dates d'accusé contrôlées, des exercices déduits.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 87)

Avant de brancher l'écran de l'échéancier, les routes ont été éprouvées. Quatre défauts :

1. **La pénalité sous-estimée.** `GET /obligations/penalite` prenait ses taux en
   paramètres, 10 % et 1,5 % par défaut, écrits dans la route. Le référentiel porte,
   validés, 25 % et 1,5 %. Pour 1 000 000 FCFA dus depuis deux mois : 130 000 FCFA
   annoncés au lieu de 280 000. Et un appelant pouvait passer des taux nuls.
2. **La TVA déposée dans le futur.** Le constat d'un dépôt de TVA acceptait un accusé
   daté du 01/12/2026, le 15/09/2026, et passait l'obligation à « déclarée ». Le constat
   hors TVA refusait déjà : les deux gardes n'étaient écrites que de ce côté.
3. **Les relances bornées à 2026.** Au 08/01/2027, veille de sept jours de l'échéance des
   obligations de décembre, aucune relance : l'exercice était « 2026 » par défaut… et
   seulement lui, dans une route qui ne devrait pas en demander.
4. **L'exercice « 2026 » par défaut** de la déclaration et du dépôt de TVA : une période
   de 2027 aurait été calculée sur les écritures de 2026.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    NIU_DEMO,
    code_attendu,
    reinitialiser_atelier,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee
from tests.conftest import enroler_par_le_courriel

INSTANT = datetime(2026, 9, 15, 10, 0)
BATIMENT = NIU_DEMO["BATIMENT"]
JANVIER = {"periode_debut": "2026-01-01", "periode_fin": "2026-01-31", "a_la_date": "2026-09-15"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    reinitialiser_atelier()
    with horloge_figee(INSTANT):
        client = TestClient(creer_application())
        reponse = client.post(
            "/transverse/session",
            json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        yield client


def _renforcer(client: TestClient) -> None:
    secret = enroler_par_le_courriel(client)
    reponse = client.post(
        "/transverse/session/renforcement", json={"code": code_attendu(secret, INSTANT)}
    )
    assert reponse.status_code == 200, reponse.text


def _statut_tva_de_janvier(client: TestClient) -> str:
    lignes = client.get(
        f"/obligations/dossiers/{BATIMENT}/echeancier",
        params={"exercice": "2026", "a_la_date": "2026-09-15"},
    ).json()
    [ligne] = [
        ligne
        for ligne in lignes
        if ligne["obligation"]["code_obligation"] == "TVA"
        and ligne["obligation"]["periode_fin"] == "2026-01-31"
    ]
    return ligne["obligation"]["statut"]


class TestLaPenaliteLitLeReferentiel:
    PARAMS = {"montant_du": "1000000", "echeance": "2026-07-15", "a_la_date": "2026-09-15"}

    def test_les_taux_valides_du_referentiel_sont_appliques(self, client):
        reponse = client.get("/obligations/penalite", params=self.PARAMS)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["penalite_fixe"] == "250000", "25 % du référentiel, et non 10 %"
        assert corps["majoration_mensuelle"] == "30000"
        assert corps["total"] == "280000"

    def test_la_reponse_dit_d_ou_viennent_les_taux(self, client):
        corps = client.get("/obligations/penalite", params=self.PARAMS).json()
        assert corps["taux_fixe"]["code"] == "PENALITE_RETARD_TAUX_FIXE"
        assert corps["taux_fixe"]["valeur"] == 25
        assert corps["taux_fixe"]["statut"] == "VALIDE"
        assert "L95" in corps["taux_fixe"]["fondement"]["texte"]
        assert corps["taux_mensuel"]["code"] == "PENALITE_RETARD_TAUX_MENSUEL"

    def test_un_taux_passe_par_l_appelant_n_a_aucun_effet(self, client):
        forces = {**self.PARAMS, "taux_fixe": "0", "taux_mensuel": "0"}
        assert client.get("/obligations/penalite", params=forces).json()["total"] == "280000"


@pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
class TestLaDateDeLAccuseDeTVA:
    def test_un_depot_date_du_futur_est_refuse(self, client):
        _renforcer(client)
        reponse = client.post(
            f"/obligations/dossiers/{BATIMENT}/depot-tva",
            params=JANVIER,
            json={"numero": "TVA-FUTUR", "depose_le": "2026-12-01T10:00:00"},
        )
        assert reponse.status_code == 409, reponse.text
        assert "postérieur à maintenant" in reponse.json()["detail"]
        assert _statut_tva_de_janvier(client) != "DECLAREE"

    def test_un_depot_date_d_avant_la_fin_de_periode_est_refuse(self, client):
        _renforcer(client)
        reponse = client.post(
            f"/obligations/dossiers/{BATIMENT}/depot-tva",
            params=JANVIER,
            json={"numero": "TVA-AVANT", "depose_le": "2026-01-20T10:00:00"},
        )
        assert reponse.status_code == 409, reponse.text
        assert "la période se termine le 31/01/2026" in reponse.json()["detail"]

    def test_un_depot_plausible_passe_toujours(self, client):
        _renforcer(client)
        reponse = client.post(
            f"/obligations/dossiers/{BATIMENT}/depot-tva",
            params=JANVIER,
            json={"numero": "TVA-JANVIER", "depose_le": "2026-02-12T10:00:00"},
        )
        assert reponse.status_code == 200, reponse.text
        assert _statut_tva_de_janvier(client) == "DECLAREE"


class TestLesExercicesNeSontPlusEcritsEnDur:
    def test_les_relances_de_janvier_portent_sur_decembre(self, client):
        reponse = client.get("/obligations/relances", params={"a_la_date": "2027-01-08"})
        assert reponse.status_code == 200, reponse.text
        echeances = {r["obligation"]["echeance"] for r in reponse.json()}
        assert "2027-01-15" in echeances, "aucune relance pour les obligations de décembre 2026"

    def test_la_declaration_prend_l_exercice_de_sa_periode(self, client):
        reponse = client.get(
            f"/obligations/dossiers/{BATIMENT}/declaration-tva",
            params={"periode_debut": "2030-01-01", "periode_fin": "2030-01-31"},
        )
        assert reponse.status_code == 409
        assert "aucun exercice" in reponse.json()["detail"]

    def test_un_exercice_explicite_reste_respecte(self, client):
        reponse = client.get(
            f"/obligations/dossiers/{BATIMENT}/declaration-tva",
            params={"periode_debut": "2026-07-01", "periode_fin": "2026-07-31", "exercice": "2026"},
        )
        assert reponse.status_code == 200, reponse.text
