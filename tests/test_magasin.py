"""Le magasin de fichiers, et les deux routes qui l'exposent.

─────────────────────────────────────────────────────────────────────────────────
CE QUI EST EN JEU

Ce sont les seules routes du système à accepter puis à **rendre** un fichier
fourni de l'extérieur. Trois défauts y sont classiques, et chacun a des
conséquences que les tests ci-dessous nomment explicitement :

* **traversée de chemin** — une clé concaténée sans contrôle donne accès à
  n'importe quel fichier du serveur, et permet d'en écraser n'importe lequel ;
* **type déclaré cru sur parole** — un document HTML étiqueté `image/jpeg`,
  rendu plus tard avec cette étiquette, exécute du script dans le domaine du
  cabinet, sur la page où un comptable est connecté ;
* **lecture non bornée** — un envoi unique suffit à épuiser la mémoire du
  processus.

Un quatrième point, plus discret, est vérifié aussi : **l'écriture est
atomique**. Une coupure au milieu d'un dépôt ne doit pas laisser un fichier
partiel à une clé valide — il se relirait sans erreur et s'afficherait comme un
PDF tronqué, sans que rien n'ait échoué.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.adaptateurs.sortant.magasin_local import (
    TYPES_ACCEPTES,
    CleInvalide,
    FichierAbsent,
    MagasinFichiersLocal,
    detecter_le_type,
)
from app.contextes.collecte.domaine.pieces import empreinte
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    reinitialiser_atelier,
)
from app.contextes.transverse.adaptateurs.sortant.donnees_demo import (
    MOT_DE_PASSE_DEMO,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 8, 15, 10, 0)

PDF = b"%PDF-1.7\nUn justificatif quelconque.\n%%EOF"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def magasin(tmp_path: Path) -> MagasinFichiersLocal:
    return MagasinFichiersLocal(tmp_path, "CGA-BRCG")


class TestDetectionDuType:
    def test_les_quatre_formats_attendus_sont_reconnus(self):
        assert detecter_le_type(PDF) == "application/pdf"
        assert detecter_le_type(JPEG) == "image/jpeg"
        assert detecter_le_type(PNG) == "image/png"
        assert detecter_le_type(b"II*\x00rien") == "image/tiff"

    def test_un_document_html_n_est_pas_reconnu(self):
        """Le cas qui motive tout ce contrôle.

        Accepté puis rendu comme une image, il exécuterait son script dans le
        domaine du cabinet.
        """
        assert detecter_le_type(b"<html><script>alert(1)</script></html>") is None

    def test_une_archive_n_est_pas_reconnue(self):
        """Une boîte de réception de justificatifs n'a pas à recevoir d'archives."""
        assert detecter_le_type(b"PK\x03\x04rien") is None

    def test_la_liste_reste_courte(self):
        """Chaque format admis est une surface d'analyse de plus.

        Si cette liste s'allonge, que ce soit une décision, pas une dérive.
        """
        assert set(TYPES_ACCEPTES) == {
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/tiff",
        }


class TestMagasin:
    def test_un_fichier_depose_se_relit_a_l_identique(
        self, magasin: MagasinFichiersLocal
    ):
        cle = empreinte(PDF)
        magasin.deposer(cle, PDF, type_mime="application/pdf")
        assert magasin.lire(cle) == PDF
        assert magasin.type_mime(cle) == "application/pdf"
        assert magasin.taille(cle) == len(PDF)

    def test_deposer_deux_fois_le_meme_fichier_ne_le_duplique_pas(
        self, magasin: MagasinFichiersLocal, tmp_path: Path
    ):
        """Un adhérent qui renvoie trois fois la même facture est le cas normal."""
        cle = empreinte(PDF)
        magasin.deposer(cle, PDF, type_mime="application/pdf")
        magasin.deposer(cle, PDF, type_mime="application/pdf")
        fichiers = [c for c in tmp_path.rglob("*") if c.is_file() and c.suffix != ".type"]
        assert len(fichiers) == 1

    def test_un_fichier_absent_leve_clairement(self, magasin: MagasinFichiersLocal):
        with pytest.raises(FichierAbsent):
            magasin.lire("a" * 64)
        assert magasin.existe("a" * 64) is False

    def test_le_fichier_est_range_en_arbre(
        self, magasin: MagasinFichiersLocal, tmp_path: Path
    ):
        """Un dossier plat de cent mille fichiers devient impraticable."""
        cle = empreinte(PDF)
        magasin.deposer(cle, PDF, type_mime="application/pdf")
        attendu = tmp_path / "CGA-BRCG" / cle[:2] / cle[2:4] / cle
        assert attendu.is_file()

    def test_aucun_fichier_partiel_ne_subsiste(
        self, magasin: MagasinFichiersLocal, tmp_path: Path
    ):
        """L'écriture passe par un temporaire, puis un renommage atomique.

        Un `.partiel` qui traînerait signalerait que le renommage n'a pas eu
        lieu — et donc qu'une coupure laisserait un document tronqué à une clé
        valide.
        """
        magasin.deposer(empreinte(PDF), PDF, type_mime="application/pdf")
        assert list(tmp_path.rglob("*.partiel")) == []

    def test_le_type_inconnu_ne_fait_jamais_interpreter(
        self, magasin: MagasinFichiersLocal, tmp_path: Path
    ):
        """Sans fichier voisin, on rend `octet-stream` — donc un téléchargement."""
        cle = empreinte(PDF)
        magasin.deposer(cle, PDF, type_mime="application/pdf")
        (tmp_path / "CGA-BRCG" / cle[:2] / cle[2:4] / f"{cle}.type").unlink()
        assert magasin.type_mime(cle) == "application/octet-stream"


class TestTraverseeDeChemin:
    """La défense la plus importante du module.

    Une clé venue d'une requête, concaténée sans contrôle, donne la lecture de
    n'importe quel fichier du serveur et l'écrasement de n'importe quel autre.
    La protection est une **liste blanche** : tout ce qui n'est pas 64
    caractères hexadécimaux minuscules n'existe pas.
    """

    @pytest.mark.parametrize(
        "cle",
        [
            "../../../etc/passwd",
            "..%2F..%2Fetc%2Fpasswd",
            "/etc/passwd",
            "a" * 63,
            "a" * 65,
            "A" * 64,  # majuscules : une seule forme canonique est admise
            "abcd/../" + "a" * 56,
            "",
            "a" * 63 + "!",
        ],
    )
    def test_toute_cle_qui_n_est_pas_une_empreinte_est_refusee(
        self, magasin: MagasinFichiersLocal, cle: str
    ):
        with pytest.raises(CleInvalide):
            magasin.lire(cle)
        with pytest.raises(CleInvalide):
            magasin.deposer(cle, PDF, type_mime="application/pdf")

    def test_un_locataire_qui_n_est_pas_un_nom_de_dossier_est_refuse(
        self, tmp_path: Path
    ):
        with pytest.raises(CleInvalide):
            MagasinFichiersLocal(tmp_path, "../autre-cabinet")

    def test_deux_locataires_ne_partagent_pas_de_dossier(self, tmp_path: Path):
        """Une erreur de requête ne doit pas rendre le justificatif d'un autre."""
        cle = empreinte(PDF)
        MagasinFichiersLocal(tmp_path, "CGA-BRCG").deposer(
            cle, PDF, type_mime="application/pdf"
        )
        assert MagasinFichiersLocal(tmp_path, "CGA-CONCURRENT").existe(cle) is False


# ── À travers l'API ─────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> Iterator[TestClient]:
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_DOSSIER_FICHIERS", str(tmp_path))
    configuration.cache_clear()
    reinitialiser_atelier()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())
    configuration.cache_clear()


def _connecter(client: TestClient) -> None:
    reponse = client.post(
        "/transverse/session",
        json={"courriel": "l.fotso@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text


NIU = "M081234567890P"


class TestApi:
    def test_un_pdf_est_accepte_et_son_empreinte_rendue(self, client: TestClient):
        _connecter(client)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        )
        assert reponse.status_code == 201, reponse.text
        corps = reponse.json()
        # L'empreinte est **recalculée par le serveur** : la détection de
        # doublon ne dépend pas de ce que le client affirme.
        assert corps["empreinte"] == empreinte(PDF)
        assert corps["type_mime"] == "application/pdf"
        assert corps["deja_present"] is False

    def test_le_second_depot_du_meme_fichier_le_signale(self, client: TestClient):
        _connecter(client)
        envoi = {"fichier": ("facture.pdf", PDF, "application/pdf")}
        client.post("/collecte/fichiers", params={"entreprise": NIU}, files=envoi)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        )
        assert reponse.json()["deja_present"] is True

    def test_un_html_deguise_en_jpeg_est_refuse(self, client: TestClient):
        """Le type déclaré par le client n'est jamais cru.

        Sans ce refus, le document reviendrait plus tard étiqueté `image/jpeg`
        et son script s'exécuterait dans le domaine du cabinet.
        """
        _connecter(client)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={
                "fichier": (
                    "photo.jpg",
                    b"<html><script>alert(1)</script></html>",
                    "image/jpeg",
                )
            },
        )
        assert reponse.status_code == 415
        assert "par le contenu" in reponse.json()["detail"]

    def test_un_fichier_vide_est_refuse(self, client: TestClient):
        _connecter(client)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("vide.pdf", b"", "application/pdf")},
        )
        assert reponse.status_code == 422

    def test_un_fichier_trop_gros_est_refuse_avec_un_conseil(self, client: TestClient):
        """Et le message dit quoi faire : un refus sans conseil fait rappeler
        le cabinet."""
        from app.contextes.collecte.adaptateurs.entrant.routes_http import (
            TAILLE_MAXIMALE,
        )

        _connecter(client)
        gros = PDF + b"\x00" * (TAILLE_MAXIMALE + 1)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("enorme.pdf", gros, "application/pdf")},
        )
        assert reponse.status_code == 413
        assert "résolution" in reponse.json()["detail"]

    def test_un_depot_sur_un_dossier_hors_perimetre_rend_404(self, client: TestClient):
        """Un 403 apprendrait à l'appelant que ce dossier existe."""
        _connecter(client)
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": "M09XXXXXXXXXXQ"},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        )
        assert reponse.status_code == 404

    def test_un_depot_sans_session_est_refuse(self, client: TestClient):
        reponse = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        )
        assert reponse.status_code == 401

    def test_le_telechargement_porte_les_en_tetes_qui_empechent_l_execution(
        self, client: TestClient
    ):
        """`attachment` + `nosniff` : le document ne s'ouvre pas dans la page.

        Et `no-store` : un justificatif est nominatif, le cloisonnement ne vaut
        plus rien si un mandataire le ressert à la requête suivante.
        """
        _connecter(client)
        depot = client.post(
            "/collecte/fichiers",
            params={"entreprise": NIU},
            files={"fichier": ("facture.pdf", PDF, "application/pdf")},
        ).json()

        # ⚠️ Le corps ne porte plus que ce qu'un déposant peut déclarer :
        # l'identifiant, la date de réception et la taille sont posés par le
        # serveur. Voir `test_depot_de_piece.py`.
        piece = client.post(
            "/collecte/pieces",
            json={
                "entreprise": NIU,
                "canal": "PORTAIL",
                "nom_fichier": "facture.pdf",
                "empreinte": depot["empreinte"],
            },
        )
        assert piece.status_code == 201, piece.text
        identifiant = piece.json()["piece"]["identifiant"]

        reponse = client.get(f"/collecte/pieces/{identifiant}/fichier")
        assert reponse.status_code == 200
        assert reponse.content == PDF
        assert reponse.headers["content-type"].startswith("application/pdf")
        assert reponse.headers["content-disposition"].startswith("attachment")
        assert reponse.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in reponse.headers["cache-control"]

    def test_une_piece_sans_fichier_rend_404_avec_une_explication(
        self, client: TestClient
    ):
        _connecter(client)

        # ⚠️ **La pièce est posée par le dépôt, pas par la route.**
        #
        # Le dépôt exige désormais une empreinte : une pièce sans document n'est
        # plus déposable, et c'est voulu — ce cas-là s'appelle une demande de
        # pièce. Mais des pièces sans fichier existent : celles entrées par
        # reprise d'historique, ou par un autre chemin.
        #
        # C'est cette route de **lecture** que ce cas garde, et elle doit
        # continuer de répondre proprement à un état que le dépôt ne produit plus.
        from datetime import date, datetime

        from app.contextes.collecte.adaptateurs.entrant.routes_http import depot_pieces
        from app.contextes.collecte.api import CanalDepot, PieceJustificative

        depot_pieces().enregistrer(
            PieceJustificative(
                identifiant="PJ-SANS-FICHIER",
                entreprise=NIU,
                canal=CanalDepot.PORTAIL,
                depose_le=date(2026, 8, 15),
                recue_le=datetime(2026, 8, 15, 10, 0),
            )
        )
        reponse = client.get("/collecte/pieces/PJ-SANS-FICHIER/fichier")
        assert reponse.status_code == 404
        assert "pièce interne" in reponse.json()["detail"]

    def test_le_nom_de_fichier_est_assaini(self, client: TestClient):
        """Le nom est choisi par l'appelant et finit dans un en-tête.

        Un retour chariot y injecterait un en-tête de son choix.
        """
        from app.contextes.collecte.adaptateurs.entrant.routes_http import (
            _nom_assaini,
        )

        # Le retour chariot et le guillemet **disparaissent** plutôt que d'être
        # remplacés : ce qui reste est un nom, pas une syntaxe d'en-tête.
        assert _nom_assaini('fac"ture\r\nX-Injecte: oui.pdf') == "factureX-Injecte oui.pdf"
        assert _nom_assaini("../../etc/passwd") == "....etcpasswd"
        assert _nom_assaini(None) == "document"
        assert _nom_assaini("   ") == "document"
