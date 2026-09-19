"""Une ressource d'autrui répond comme une ressource inexistante, et les documents existent.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 88)

**Le NIU d'autrui dans un refus.** Les routes qui retrouvent le dossier à partir d'une
ressource (une pièce, son document, une demande, une facture de démonstration) le
passaient à `exiger_dossier`, dont le refus dit « aucun dossier {niu} accessible ». Essai
réel : l'adhérent de LA COLOMBE demandait le document d'une pièce de SARL BATIMENT PLUS et
recevait le NIU de ce client, dans une réponse différente de celle d'une pièce
inexistante. Les identifiants de démonstration sont séquentiels : on énumérait. La
docstring de la route de démonstration de la conformité affirmait déjà l'inverse.

**Des documents annoncés et absents.** Les pièces de démonstration portaient une empreinte
fabriquée sur leur référence, sans fichier au magasin : « Télécharger le document »
rendait « introuvable » sur chacune.

**Une date future refusée comme trop ancienne.** Le refus parlait de « pièce plus
ancienne » à qui déclarait un dépôt dans l'avenir.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.api import PIECES_DEMO
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, NIU_DEMO, reinitialiser_atelier
from app.main import creer_application

PIECE_DE_BATIMENT = next(
    p.identifiant for p in PIECES_DEMO.values() if p.entreprise == NIU_DEMO["BATIMENT"]
)


def _client(courriel: str) -> TestClient:
    reinitialiser_atelier()
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _identiques(client: TestClient, methode: str, existant: str, inexistant: str, **kwargs):
    a = getattr(client, methode)(existant, **kwargs)
    b = getattr(client, methode)(inexistant, **kwargs)
    return a, b


class TestHorsPerimetreCommeInexistant:
    @pytest.mark.parametrize(
        "gabarit", ["/collecte/pieces/{}", "/collecte/pieces/{}/fichier"], ids=["fiche", "document"]
    )
    def test_une_piece_d_autrui(self, gabarit):
        adherent = _client("mc.essomba@lacolombe.cm")
        autrui = adherent.get(gabarit.format(PIECE_DE_BATIMENT))
        absente = adherent.get(gabarit.format("PJ-2026-9999"))
        assert autrui.status_code == absente.status_code == 404
        assert NIU_DEMO["BATIMENT"] not in autrui.text
        assert autrui.json()["detail"].replace(PIECE_DE_BATIMENT, "X") == absente.json()[
            "detail"
        ].replace("PJ-2026-9999", "X")

    def test_une_facture_de_demonstration_d_autrui(self):
        adherent = _client("mc.essomba@lacolombe.cm")
        autrui = adherent.get("/conformite/demonstration/F-2026-0412")
        absente = adherent.get("/conformite/demonstration/F-2026-9999")
        assert autrui.status_code == absente.status_code == 404
        assert NIU_DEMO["BATIMENT"] not in autrui.text
        assert autrui.json()["detail"].replace("0412", "X") == absente.json()["detail"].replace(
            "9999", "X"
        )

    def test_une_demande_d_autrui(self):
        """DP-2026-012 porte sur un dossier que le comptable C-004 ne suit pas."""
        comptable = _client("l.fotso@cga-brcg.cm")
        corps = {"piece": "PJ-2026-0001"}
        autrui = comptable.post("/collecte/demandes/DP-2026-012/satisfaction", json=corps)
        absente = comptable.post("/collecte/demandes/DP-2026-999/satisfaction", json=corps)
        assert autrui.status_code == absente.status_code == 404
        assert "P019876543210K" not in autrui.text
        assert autrui.json()["detail"].replace("012", "X") == absente.json()["detail"].replace(
            "999", "X"
        )

    def test_un_niu_ecrit_par_l_appelant_garde_son_message(self):
        """La règle ne change pas quand l'appelant a lui-même écrit le NIU : il ne
        l'apprend pas, et le message qui invite à demander l'affectation reste utile."""
        adherent = _client("mc.essomba@lacolombe.cm")
        reponse = adherent.get(f"/portefeuille/entreprises/{NIU_DEMO['BATIMENT']}")
        assert reponse.status_code == 404
        assert "en demander l'affectation" in reponse.json()["detail"]


class TestLesDocumentsDeDemonstrationExistent:
    def test_chaque_piece_rend_son_document_intact(self, tmp_path, monkeypatch):
        """⚠️ Sur un magasin **vide** : le magasin est sur disque et adressé par le contenu,
        et les documents rangés par une exécution précédente faisaient passer ce cas même
        quand plus rien ne les rangeait. Une mutation l'a montré."""
        from app.contextes.collecte.adaptateurs.sortant.magasins_memoire import (
            vider_les_magasins_de_la_collecte,
        )
        from app.infrastructure.config import configuration

        monkeypatch.setenv("CGA_DOSSIER_FICHIERS", str(tmp_path))
        configuration.cache_clear()
        vider_les_magasins_de_la_collecte()
        assert not any(tmp_path.iterdir())
        direction = _client("b.mballa@cga-brcg.cm")
        for piece in PIECES_DEMO.values():
            reponse = direction.get(f"/collecte/pieces/{piece.identifiant}/fichier")
            assert reponse.status_code == 200, (piece.identifiant, reponse.text)
            assert reponse.content.startswith(b"%PDF-")
            assert hashlib.sha256(reponse.content).hexdigest() == piece.empreinte
            assert len(reponse.content) == piece.taille_octets
        configuration.cache_clear()
        vider_les_magasins_de_la_collecte()

    def test_le_doublon_delibere_reste_un_autre_fichier(self):
        """La démonstration du doublon « même facture, autre fichier » tient toujours."""
        de_0412 = [p for p in PIECES_DEMO.values() if p.reference_document == "F-2026-0412"]
        assert len(de_0412) == 2
        assert de_0412[0].empreinte != de_0412[1].empreinte


def test_une_date_de_depot_future_est_dite_future():
    comptable = _client("l.fotso@cga-brcg.cm")
    pdf = b"%PDF-1.4\n%%EOF\n"
    range_ = comptable.post(
        "/collecte/fichiers",
        params={"entreprise": NIU_DEMO["BATIMENT"]},
        files={"fichier": ("f.pdf", pdf, "application/pdf")},
    ).json()
    reponse = comptable.post(
        "/collecte/pieces",
        json={
            "entreprise": NIU_DEMO["BATIMENT"],
            "canal": "DEPOT_CABINET",
            "empreinte": range_["empreinte"],
            "depose_le": "2099-01-01",
        },
    )
    assert reponse.status_code == 422
    assert "dans l'avenir" in reponse.json()["detail"]
