"""Deux transitions du domaine exposées : rétablir un compte, lire une pièce (pas 91).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Deux gestes attendus par les écrans n'avaient pas de route, alors que le domaine savait les
faire : lever la suspension d'un compte, et passer une pièce reçue à l'état LUE après
l'avoir identifiée.

En les exposant, deux défauts du domaine sont apparus :

- **rétablir un compte actif était accepté**, et écrivait « compte.retabli » au journal
  d'audit : une levée de suspension qui ne levait rien ;
- **« relire » une pièce déjà lue réécrivait ses données** (montant, numéro), sur
  lesquelles un contrôle ou une écriture a pu s'appuyer. La route ne lit qu'une pièce reçue.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.api import vider_les_magasins_de_la_collecte
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    atelier,
    reinitialiser_atelier,
    service_de_notification,
)
from app.main import creer_application

PIECE_RECUE_DE_BATIMENT = "PJ-2026-0028"
PIECE_RECUE_HORS_PORTEFEUILLE = "PJ-2026-0005"
IDENTIFICATION = {
    "type": "FACTURE_ACHAT",
    "reference_document": "F-2026-0999",
    "date_document": "2026-09-01",
    "montant_ttc": "100000",
}


@pytest.fixture(autouse=True)
def magasins_neufs():
    reinitialiser_atelier()
    vider_les_magasins_de_la_collecte()
    yield
    vider_les_magasins_de_la_collecte()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


class TestRetablirUnCompte:
    MOTIF = {"motif": "Suspension prononcée par erreur le 12/09."}

    def test_un_compte_suspendu_se_retablit_et_son_titulaire_est_prevenu(self):
        administrateur = _client("s.onana@cga-brcg.cm")
        reponse = administrateur.post("/transverse/comptes/C-008/retablissement", json=self.MOTIF)
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["etat"] == "ACTIF"
        [entree] = [e for e in atelier().journal.lister() if e.action == "compte.retabli"]
        assert entree.acteur == "C-002"
        assert entree.motif == self.MOTIF["motif"]
        assert service_de_notification().derniers("compte.retabli")

    def test_ses_habilitations_fermees_ne_se_rouvrent_pas(self):
        """Rétablir un compte ne rend aucun dossier : il faut réaccorder, daté."""
        avant = [(h.identifiant, h.fin) for h in atelier().habilitations.pour_compte("C-008")]
        _client("s.onana@cga-brcg.cm").post(
            "/transverse/comptes/C-008/retablissement", json=self.MOTIF
        )
        assert [
            (h.identifiant, h.fin) for h in atelier().habilitations.pour_compte("C-008")
        ] == avant

    def test_un_compte_actif_ne_se_retablit_pas_et_le_journal_reste_juste(self):
        reponse = _client("s.onana@cga-brcg.cm").post(
            "/transverse/comptes/C-004/retablissement", json=self.MOTIF
        )
        assert reponse.status_code == 409
        assert not [e for e in atelier().journal.lister() if e.action == "compte.retabli"]

    @pytest.mark.parametrize(
        ("courriel", "corps", "statut"),
        [
            ("l.fotso@cga-brcg.cm", {"motif": "Suspension prononcée par erreur."}, 403),
            ("s.onana@cga-brcg.cm", {"motif": "erreur"}, 422),
            (
                "s.onana@cga-brcg.cm",
                {"motif": "Suspension prononcée par erreur.", "etat": "ACTIF"},
                422,
            ),
        ],
        ids=["comptable", "motif-court", "etat-impose"],
    )
    def test_les_refus(self, courriel, corps, statut):
        reponse = _client(courriel).post("/transverse/comptes/C-008/retablissement", json=corps)
        assert reponse.status_code == statut
        assert atelier().comptes.lire("C-008").etat == "SUSPENDU"


class TestLireUnePiece:
    def test_une_piece_recue_identifiee_passe_a_lue(self):
        reponse = _client("l.fotso@cga-brcg.cm").post(
            f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}/lecture", json=IDENTIFICATION
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["etat"] == "LUE"
        assert reponse.json()["montant_ttc"] == "100000"

    def test_une_piece_non_identifiee_reste_recue(self):
        comptable = _client("l.fotso@cga-brcg.cm")
        reponse = comptable.post(f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}/lecture", json={})
        assert reponse.status_code == 409
        assert "il manque" in reponse.json()["detail"]
        fiche = comptable.get(f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}").json()
        assert fiche["piece"]["etat"] == "RECUE"

    def test_une_piece_lue_ne_se_reecrit_pas(self):
        comptable = _client("l.fotso@cga-brcg.cm")
        comptable.post(f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}/lecture", json=IDENTIFICATION)
        reponse = comptable.post(
            f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}/lecture", json={"montant_ttc": "1"}
        )
        assert reponse.status_code == 409
        fiche = comptable.get(f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}").json()
        assert fiche["piece"]["montant_ttc"] == "100000"

    def test_hors_portefeuille_comme_inconnue(self):
        comptable = _client("l.fotso@cga-brcg.cm")
        autrui = comptable.post(
            f"/collecte/pieces/{PIECE_RECUE_HORS_PORTEFEUILLE}/lecture", json=IDENTIFICATION
        )
        inconnue = comptable.post("/collecte/pieces/PJ-2026-9999/lecture", json=IDENTIFICATION)
        assert autrui.status_code == inconnue.status_code == 404
        assert autrui.json()["detail"].replace(
            PIECE_RECUE_HORS_PORTEFEUILLE, "X"
        ) == inconnue.json()["detail"].replace("PJ-2026-9999", "X")

    def test_la_direction_lit_la_boite_mais_n_identifie_pas(self):
        reponse = _client("b.mballa@cga-brcg.cm").post(
            f"/collecte/pieces/{PIECE_RECUE_DE_BATIMENT}/lecture", json=IDENTIFICATION
        )
        assert reponse.status_code == 403
