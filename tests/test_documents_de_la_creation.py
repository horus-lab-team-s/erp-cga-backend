"""Les documents d'une création : versés par le cabinet, relus par lui et par elle.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Les documents envoyés par WhatsApp sont versés par le juriste ; côté
administration, aperçu et téléchargement des documents déposés. »

Ce qui manquait, constaté en relisant le code :

* le chargé de formalités ne pouvait ouvrir AUCUN document de ses dossiers : la
  seule porte, `/collecte/fichiers/{empreinte}`, exige `LIRE_PIECE`, qu'il n'a
  pas ;
* une pièce reçue ne disait pas d'où elle venait ;
* marquer « reçue » sans fichier une pièce que la cliente avait déjà envoyée
  effaçait son document du dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from uuid import uuid4

# ⚠️ Le MODULE, et non ses classes : importées ici, elles seraient collectées deux fois.
from tests import test_compte_de_la_creation as compte
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CHARGEE = compte.CHARGEE
PDF = compte.PDF


def _verser(client, reference: str, code: str, *, canal: str | None = "WHATSAPP", fichier=PDF):
    """Ce que fait la juriste : le fichier reçu par messagerie, puis la pièce."""
    corps: dict[str, str] = {"code": code}
    if fichier is not None:
        envoi = client.post(
            "/collecte/fichiers-de-formalite",
            files={"fichier": ("capture-whatsapp.pdf", fichier, "application/pdf")},
        )
        assert envoi.status_code == 201, envoi.text
        corps["empreinte"] = envoi.json()["empreinte"]
    if canal:
        corps["canal"] = canal
    return client.post(f"/creations/{reference}/pieces", json=corps)


def _premiere_piece(client, reference: str) -> str:
    return client.get(f"/creations/{reference}").json()["dossier"]["pieces"][0]["code"]


class TestLaJuristeVerseEtRelit:
    def test_elle_verse_une_piece_recue_par_whatsapp_et_le_dossier_le_dit(self, plateforme):
        client = plateforme
        reference = compte._un_dossier(client, f"c.{uuid4().hex[:6]}@exemple.cm")
        ouvrir_une_session(client, CHARGEE)
        code = _premiere_piece(client, reference)

        versee = _verser(client, reference, code)

        assert versee.status_code == 200, versee.text
        (piece,) = [p for p in versee.json()["pieces"] if p["code"] == code]
        assert piece["canal"] == "WHATSAPP"
        assert piece["versee_par"], "on doit savoir qui l'a versée"
        assert piece["empreinte"]

    def test_elle_relit_le_document_de_son_dossier(self, plateforme):
        client = plateforme
        reference = compte._un_dossier(client, f"c.{uuid4().hex[:6]}@exemple.cm")
        ouvrir_une_session(client, CHARGEE)
        code = _premiere_piece(client, reference)
        _verser(client, reference, code)

        lu = client.get(f"/creations/{reference}/pieces/{code}/document")

        assert lu.status_code == 200, lu.text
        assert lu.content == PDF
        assert lu.headers["content-type"].startswith("application/pdf")
        assert lu.headers["content-disposition"].startswith("attachment")
        assert lu.headers["x-content-type-options"] == "nosniff"

    def test_une_piece_sans_document_et_une_piece_inconnue_rendent_404(self, plateforme):
        client = plateforme
        reference = compte._un_dossier(client, f"c.{uuid4().hex[:6]}@exemple.cm")
        ouvrir_une_session(client, CHARGEE)
        code = _premiere_piece(client, reference)

        sans = client.get(f"/creations/{reference}/pieces/{code}/document")
        inconnue = client.get(f"/creations/{reference}/pieces/INCONNUE/document")

        assert sans.status_code == inconnue.status_code == 404
        assert sans.json()["detail"] == inconnue.json()["detail"]

    def test_elle_ne_declare_pas_un_depot_de_la_cliente(self, plateforme):
        """⚠️ « Déposée par la cliente » écrit par un collaborateur attribuerait à
        la cliente un geste qu'elle n'a pas fait."""
        client = plateforme
        reference = compte._un_dossier(client, f"c.{uuid4().hex[:6]}@exemple.cm")
        ouvrir_une_session(client, CHARGEE)
        code = _premiere_piece(client, reference)

        refus = _verser(client, reference, code, canal="ESPACE_CLIENT")

        assert refus.status_code == 422, refus.text


class TestLeDocumentDeLaClienteNeSePerdPas:
    def test_cocher_recue_sans_fichier_garde_son_document(self, plateforme):
        """⚠️ L'empreinte était remise à `None` : son document disparaissait."""
        client = plateforme
        courriel = f"c.{uuid4().hex[:6]}@exemple.cm"
        reference = compte._un_dossier(client, courriel)
        compte._connectee(client, reference, courriel)
        code = client.get("/creations/mes-creations").json()[0]["pieces"][0]["code"]
        envoi = client.post(
            "/collecte/fichiers-de-creation",
            params={"reference": reference},
            files={"fichier": ("cni.pdf", PDF, "application/pdf")},
        )
        client.post(
            f"/creations/mes-creations/{reference}/pieces",
            json={"code": code, "empreinte": envoi.json()["empreinte"]},
        )

        client.cookies.clear()
        ouvrir_une_session(client, CHARGEE)
        _verser(client, reference, code, canal="GUICHET", fichier=None)

        (piece,) = [
            p for p in client.get(f"/creations/{reference}").json()["dossier"]["pieces"] if p["code"] == code
        ]
        assert piece["empreinte"] == envoi.json()["empreinte"]
        assert client.get(f"/creations/{reference}/pieces/{code}/document").content == PDF


class TestLaClienteRelitLeSien:
    def _avec_un_document(self, client):
        courriel = f"c.{uuid4().hex[:6]}@exemple.cm"
        sien = compte._un_dossier(client, courriel)
        autre = compte._un_dossier(client, "autre@exemple.cm")
        ouvrir_une_session(client, CHARGEE)
        code = _premiere_piece(client, sien)
        _verser(client, sien, code)
        _verser(client, autre, code)
        client.cookies.clear()
        compte._connectee(client, sien, courriel)
        return sien, autre, code

    def test_elle_relit_ce_que_le_cabinet_a_verse_pour_elle(self, plateforme):
        client = plateforme
        sien, _, code = self._avec_un_document(plateforme)

        (piece,) = [
            p
            for p in client.get("/creations/mes-creations").json()[0]["pieces"]
            if p["code"] == code
        ]
        assert piece["document"] is True
        assert "empreinte" not in piece, "elle n'a pas à manier un identifiant du magasin"
        lu = client.get(f"/creations/mes-creations/{sien}/pieces/{code}/document")
        assert lu.status_code == 200, lu.text
        assert lu.content == PDF

    def test_ni_le_document_d_une_autre_ni_la_porte_du_cabinet(self, plateforme):
        client = plateforme
        _, autre, code = self._avec_un_document(plateforme)

        assert (
            client.get(f"/creations/mes-creations/{autre}/pieces/{code}/document").status_code
            == 404
        )
        assert client.get(f"/creations/{autre}/pieces/{code}/document").status_code == 403
