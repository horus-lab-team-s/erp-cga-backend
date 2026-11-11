"""Les pièces à joindre à la DSF, selon le tableau du guide de la DGI (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le système décide de la liste : Normal 1 à 9 et 16 à 19, SMT 1 à 3 et 8 à 18 ;
* les pièces que la plateforme produit ne se versent pas ;
* une pièce versée est un PDF ou un scan ; « sans objet » exige son motif ;
* la dernière marque fait foi ; un retrait remet la pièce à fournir ;
* un adhérent ne prépare pas la DSF, un autre dossier n'y accède pas.
"""

from __future__ import annotations

from datetime import datetime

from app.contextes.cloture.domaine.entites import SystemeDsf
from app.contextes.cloture.domaine.pieces_dsf import EtatPiece, Marque, etat_des_pieces
from tests.conftest import exige_postgresql, ouvrir_une_session

PDF = b"%PDF-1.4\n% attestation de visa\n%%EOF\n"
LE = datetime(2026, 10, 15, 10, 0)


class TestLaListe:
    def test_le_normal_et_le_smt_n_ont_pas_les_memes_pieces(self):
        normal = [x.piece.numero for x in etat_des_pieces(SystemeDsf.NORMAL, {})]
        smt = [x.piece.numero for x in etat_des_pieces(SystemeDsf.MINIMAL, {})]

        assert normal == [1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17, 18, 19]
        assert smt == [1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]

    def test_la_page_suit_le_systeme(self):
        def page(systeme, numero):
            return next(x.page for x in etat_des_pieces(systeme, {}) if x.piece.numero == numero)

        assert page(SystemeDsf.NORMAL, 2) == "Note 7" and page(SystemeDsf.MINIMAL, 2) == "Note 2"

    def test_ce_que_la_plateforme_produit_est_produit(self):
        produites = {
            x.piece.numero
            for x in etat_des_pieces(SystemeDsf.NORMAL, {})
            if x.etat is EtatPiece.PRODUITE
        }
        # 7 : le tableau des amortissements, produit du registre des immobilisations (17 octobre).
        assert produites == {2, 3, 7, 8, 9}

    def test_la_derniere_marque_fait_foi(self):
        jointe = Marque(etat=EtatPiece.JOINTE, par="a.bouba", le=LE, nom_du_fichier="visa.pdf")
        retiree = Marque(etat=EtatPiece.A_FOURNIR, par="a.bouba", le=LE)
        assert etat_des_pieces(SystemeDsf.NORMAL, {1: jointe})[0].etat is EtatPiece.JOINTE
        assert etat_des_pieces(SystemeDsf.NORMAL, {1: retiree})[0].etat is EtatPiece.A_FOURNIR


@exige_postgresql
class TestParLesRoutes:
    NGUEMA = "/cloture/dossiers/P027788990011M/dsf/2025/pieces-jointes"
    BATIMENT = "/cloture/dossiers/M081234567890P/dsf/2026/pieces-jointes"

    def test_le_dossier_au_reel_suit_le_normal_celui_a_l_igs_le_smt(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        assert client.get(self.BATIMENT).json()["systeme"] == "NORMAL"
        smt = client.get(self.NGUEMA).json()
        assert smt["systeme"] == "MINIMAL" and smt["complet"] is False

    def test_verser_declarer_sans_objet_et_retirer(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        avant = client.get(self.BATIMENT).json()["a_fournir"]

        versee = client.post(
            f"{self.BATIMENT}/1/fichier", files={"fichier": ("visa.pdf", PDF, "application/pdf")}
        )
        assert versee.status_code == 200, versee.text
        piece = next(p for p in versee.json()["pieces"] if p["numero"] == 1)
        assert piece["etat"] == "JOINTE" and piece["nom_du_fichier"] == "visa.pdf"
        assert client.get(f"{self.BATIMENT}/1/fichier").content == PDF

        court = client.post(f"{self.BATIMENT}/19/sans-objet", json={"motif": "néant"})
        assert court.status_code == 422
        neant = client.post(
            f"{self.BATIMENT}/19/sans-objet",
            json={"motif": "Aucun produit soumis aux droits d'accises dans l'activité."},
        )
        assert neant.json()["a_fournir"] == avant - 2

        retrait = client.post(f"{self.BATIMENT}/1/retrait")
        assert retrait.json()["a_fournir"] == avant - 1
        assert client.get(f"{self.BATIMENT}/1/fichier").status_code == 404

    def test_ce_qui_ne_se_verse_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")

        produite = client.post(
            f"{self.BATIMENT}/2/fichier", files={"fichier": ("b.pdf", PDF, "application/pdf")}
        )
        assert produite.status_code == 409  # la plateforme la produit
        hors_systeme = client.post(f"{self.BATIMENT}/12/sans-objet", json={"motif": "x" * 20})
        assert hors_systeme.status_code == 404  # pièce du SMT seulement
        html = client.post(
            f"{self.BATIMENT}/1/fichier",
            files={"fichier": ("v.pdf", b"<html></html>", "application/pdf")},
        )
        assert html.status_code == 415

    def test_un_adherent_ne_prepare_pas_la_dsf(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        verse = client.post(
            f"{self.BATIMENT}/1/fichier", files={"fichier": ("visa.pdf", PDF, "application/pdf")}
        )
        assert verse.status_code == 403
        # Et rien n'a été inscrit : un 403 rendu APRÈS l'écriture passerait sinon pour un
        # refus (constaté le 15 octobre 2026, à l'épreuve par mutation).
        client.cookies.clear()
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        visa = next(p for p in client.get(self.BATIMENT).json()["pieces"] if p["numero"] == 1)
        assert visa["etat"] == "A_FOURNIR"
        client.cookies.clear()
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        assert client.get(self.BATIMENT).status_code == 403
