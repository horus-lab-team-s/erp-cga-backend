"""Un dépôt rejoué rend la pièce inchangée (pas 96).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

La route de dépôt promettait qu'un client dont la connexion tombe « peut recommencer sans
crainte ». Essai du pas 96 : l'adhérent dépose, le comptable lit la pièce, l'adhérent rejoue
son envoi. Réponse 201, et la pièce était **redevenue RECUE, sans type, sans numéro, sans
montant**, avec une nouvelle heure de réception. Une file de dépôts hors ligne, qui rejoue
par construction, aurait effacé la lecture du cabinet à chaque retour du réseau.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.api import vider_les_magasins_de_la_collecte
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import maintenant
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
ADHERENT = "jp.nkoa@batimentplus.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
IDENTIFICATION = {
    "type": "FACTURE_ACHAT",
    "reference_document": "F-REJEU-1",
    "date_document": "2026-09-01",
    "montant_ttc": "118000",
}


@pytest.fixture(autouse=True)
def magasins_neufs(tmp_path, monkeypatch):
    monkeypatch.setenv("CGA_DOSSIER_FICHIERS", str(tmp_path))
    from app.infrastructure import config

    config.configuration.cache_clear()
    reinitialiser_atelier()
    vider_les_magasins_de_la_collecte()
    yield
    vider_les_magasins_de_la_collecte()
    config.configuration.cache_clear()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _deposer(client: TestClient, contenu: bytes, **corps):
    fichier = client.post(
        "/collecte/fichiers",
        params={"entreprise": BATIMENT},
        files={"fichier": ("facture.pdf", contenu, "application/pdf")},
    )
    assert fichier.status_code in (200, 201), fichier.text
    return client.post(
        "/collecte/pieces",
        json={
            "entreprise": BATIMENT,
            "canal": "PORTAIL",
            "empreinte": fichier.json()["empreinte"],
            **corps,
        },
    )


PDF = b"%PDF-1.4\n%rejeu du pas 96\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


class TestLeRejeuNeReecritRien:
    def test_une_piece_lue_par_le_cabinet_reste_lue_apres_le_rejeu(self):
        adherent = _client(ADHERENT)
        premier = _deposer(adherent, PDF)
        assert premier.status_code == 201, premier.text
        assert premier.json()["rejeu"] is False
        piece = premier.json()["piece"]

        lue = _client(COMPTABLE).post(
            f"/collecte/pieces/{piece['identifiant']}/lecture", json=IDENTIFICATION
        )
        assert lue.status_code == 200, lue.text

        rejeu = _deposer(adherent, PDF)
        assert rejeu.status_code == 200, rejeu.text
        assert rejeu.json()["rejeu"] is True
        rendue = rejeu.json()["piece"]
        assert rendue["etat"] == "LUE"
        assert rendue["reference_document"] == "F-REJEU-1"
        assert rendue["montant_ttc"] in ("118000", "118000.00", "118000.0")
        assert rendue["recue_le"] == piece["recue_le"], (
            "l'heure de réception, qui fait foi, a été réécrite"
        )

    def test_le_rejeu_ne_change_pas_non_plus_ce_que_le_depot_declarait(self):
        adherent = _client(ADHERENT)
        _deposer(adherent, PDF, emetteur="QUINCAILLERIE DU WOURI")
        rejeu = _deposer(adherent, PDF, emetteur="AUTRE CHOSE")
        assert rejeu.json()["piece"]["emetteur"] == "QUINCAILLERIE DU WOURI"

    def test_un_autre_fichier_reste_une_nouvelle_piece(self):
        adherent = _client(ADHERENT)
        assert _deposer(adherent, PDF).status_code == 201
        autre = _deposer(adherent, PDF.replace(b"96", b"97"))
        assert autre.status_code == 201 and autre.json()["rejeu"] is False

    def test_hors_perimetre_le_rejeu_ne_revele_pas_la_piece(self):
        _deposer(_client(ADHERENT), PDF)
        intrus = _client(AUTRE_ADHERENT)
        fichier = intrus.post(
            "/collecte/fichiers",
            params={"entreprise": BATIMENT},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        )
        assert fichier.status_code == 404

    def test_le_jour_de_la_capture_est_retenu_comme_date_de_depot(self):
        """La file hors ligne envoie le jour où la photo a été prise."""
        capture = (maintenant().date() - timedelta(days=2)).isoformat()
        depot = _deposer(_client(ADHERENT), PDF, depose_le=capture)
        assert depot.status_code == 201, depot.text
        assert depot.json()["piece"]["depose_le"] == capture
        assert date.fromisoformat(depot.json()["piece"]["recue_le"][:10]) == maintenant().date()


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_le_rejeu_rend_la_piece_lue_en_base(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT)
        piece = _deposer(client, PDF).json()["piece"]
        ouvrir_une_session(client, COMPTABLE)
        assert (
            client.post(
                f"/collecte/pieces/{piece['identifiant']}/lecture", json=IDENTIFICATION
            ).status_code
            == 200
        )
        ouvrir_une_session(client, ADHERENT)
        rejeu = _deposer(client, PDF)
        assert rejeu.status_code == 200, rejeu.text
        assert rejeu.json()["piece"]["etat"] == "LUE"
