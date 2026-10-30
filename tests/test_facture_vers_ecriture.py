"""Une facture chargée devient vite une écriture : lue, confirmée, proposée.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Comptabilité complète, une facture chargée transformée rapidement en écriture »,
et la lecture automatique par Claude, un collaborateur confirmant toujours les
montants et le NIU.

Le moteur de proposition existait, mais ne travaillait que sur les factures de
démonstration : une pièce réelle ne portait ni lignes, ni HT, ni TVA, ni NIU.

⚠️ CE QUI EST GARDÉ

* une facture dont les montants ne se recoupent pas ne s'enregistre pas ;
* la lecture automatique ne lève jamais, et sans clé rien ne sort du serveur ;
* les montants, le NIU et la date sont toujours « à relire », quel que soit le score ;
* l'écriture se propose depuis la pièce par le même chemin que les autres ;
* une pièce d'un autre dossier, ou sans facture lue, ne propose rien ;
* une pièce comptabilisée ne voit plus sa facture changer.

⚠️ AUCUN APPEL RÉEL À L'API : le client d'Anthropic est remplacé par un faux qui
rend ce que l'API rendrait. Les essais ne coûtent rien et ne sortent aucun document.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from sqlalchemy import text

from app.contextes.collecte.adaptateurs.sortant.extraction_claude import ExtractionClaude
from app.contextes.collecte.domaine.facture_lue import FactureLue, LigneLue
from app.contextes.collecte.domaine.pieces import TypePiece
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = NIU_DEMO["BATIMENT"]
COLOMBE = NIU_DEMO["COLOMBE"]
COMPTABLE = "l.fotso@cga-brcg.cm"
PDF = b"%PDF-1.4\n%facture d'essai\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
TIFF = b"II*\x00" + b"\x00" * 64

LUE = {
    "est_une_facture": True,
    "emetteur": {"valeur": "QUINCAILLERIE DU WOURI", "confiance": 0.98},
    "niu_emetteur": {"valeur": "m0912 3456 7890q", "confiance": 0.95},
    "reference": {"valeur": "FA-2026-0815", "confiance": 0.99},
    "date_emission": {"valeur": "2026-09-12", "confiance": 0.97},
    "lignes": {
        "valeur": [
            {"designation": "Ciment 50 kg x 20", "montant_ht": 100000},
            {"designation": "Fer à béton", "montant_ht": 50000},
        ],
        "confiance": 0.9,
    },
    "montant_ht": {"valeur": 150000, "confiance": 0.99},
    "montant_tva": {"valeur": 28875, "confiance": 0.99},
    "montant_ttc": {"valeur": 178875, "confiance": 1.7},
}


def _facture(**autres):
    valeurs = {
        "emetteur": "QUINCAILLERIE DU WOURI",
        "reference": "FA-1",
        "date_emission": date(2026, 9, 12),
        "lignes": (LigneLue(designation="Ciment", montant_ht=Decimal(150000)),),
        "montant_ht": Decimal(150000),
        "montant_tva": Decimal(28875),
        "montant_ttc": Decimal(178875),
        "lue_par": "C-004",
        "lue_le": datetime(2026, 10, 14, 9),
    }
    valeurs.update(autres)
    return FactureLue(**valeurs)


class TestLesMontantsSeRecoupent:
    def test_une_facture_coherente_s_enregistre(self):
        assert _facture().montant_ttc == Decimal(178875)

    def test_ht_plus_tva_doit_faire_le_ttc(self):
        # Pydantic enveloppe `MontantsIncoherents` dans sa `ValidationError`, qui reste une
        # `ValueError` : c'est ce que la route rattrape.
        with pytest.raises(ValueError, match="≠ TTC"):
            _facture(montant_ttc=Decimal(180000))

    def test_les_lignes_doivent_faire_le_ht(self):
        with pytest.raises(ValueError, match="les lignes font"):
            _facture(lignes=(LigneLue(designation="Ciment", montant_ht=Decimal(100000)),))


class FauxClient:
    """Ce que rendrait `client.beta.messages.create`, et ce qu'on lui a demandé."""

    def __init__(self, *, reponse=None, echec=None):
        self.demandes: list[dict] = []
        self._reponse = reponse
        self._echec = echec
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **demande):
        self.demandes.append(demande)
        if self._echec:
            raise self._echec
        return self._reponse


def _reponse(lu: dict, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=json.dumps(lu))],
    )


def _lire(contenu: bytes, client: FauxClient):
    return ExtractionClaude(cle_api="x", modele="claude-opus-5-5", client=client).extraire(
        contenu, piece="PJ-1", type_attendu=TypePiece.FACTURE_ACHAT
    )


class TestLaLectureAutomatique:
    def test_elle_rend_chaque_champ_avec_sa_confiance_et_sous_la_forme_du_produit(self):
        client = FauxClient(reponse=_reponse(LUE))

        extraction = _lire(PDF, client)

        assert extraction.incident is None
        assert extraction.valeur("reference_document") == "FA-2026-0815"
        # Normalisé : sans espaces, en capitales ; montants entiers en texte.
        assert extraction.champs["niu_emetteur"].valeur_lue == "M091234567890Q"
        assert extraction.champs["montant_ht"].valeur_lue == "150000"
        assert len(extraction.champs["lignes"].valeur_lue) == 2
        # Une confiance hors de [0, 1] est ramenée à 1, jamais au-delà.
        assert extraction.champs["montant_ttc"].confiance == Decimal(1)

    def test_les_montants_le_niu_et_la_date_sont_toujours_a_relire(self):
        """⚠️ Même à confiance 1 : leur erreur se propage jusqu'à la déclaration."""
        extraction = _lire(PDF, FauxClient(reponse=_reponse(LUE)))

        for champ in ("montant_ht", "montant_tva", "montant_ttc", "niu_emetteur", "date_emission"):
            assert not extraction.champs[champ].acceptable_automatiquement, champ
        assert extraction.champs["reference_document"].acceptable_automatiquement

    def test_la_demande_est_celle_qu_on_a_decidee(self):
        client = FauxClient(reponse=_reponse(LUE))
        _lire(PDF, client)
        _lire(JPEG, client)

        pdf, jpeg = client.demandes
        assert pdf["model"] == "claude-opus-5-5"
        assert pdf["fallbacks"] == "default"
        assert "server-side-fallback-2026-07-01" in pdf["betas"]
        assert pdf["output_config"]["format"]["type"] == "json_schema"
        assert pdf["messages"][0]["content"][0]["type"] == "document"
        assert jpeg["messages"][0]["content"][0]["type"] == "image"
        assert jpeg["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"

    def test_une_valeur_absente_ou_mal_formee_tombe_a_confiance_zero(self):
        lu = {
            **LUE,
            "date_emission": {"valeur": "12 septembre", "confiance": 0.9},
            "niu_emetteur": {"valeur": None, "confiance": 0.8},
        }
        extraction = _lire(PDF, FauxClient(reponse=_reponse(lu)))

        assert extraction.champs["date_emission"].confiance == 0
        assert extraction.champs["niu_emetteur"].confiance == 0

    @pytest.mark.parametrize(
        "client, contenu",
        [
            (FauxClient(reponse=_reponse(LUE, stop_reason="refusal")), PDF),
            (FauxClient(reponse=_reponse(LUE, stop_reason="max_tokens")), PDF),
            (
                FauxClient(
                    echec=anthropic.APIConnectionError(
                        request=httpx2.Request("POST", "https://api.anthropic.com")
                    )
                ),
                PDF,
            ),
            (FauxClient(reponse=_reponse(LUE)), TIFF),
        ],
        ids=["refus", "trop-longue", "reseau", "tiff"],
    )
    def test_elle_ne_leve_jamais_et_dit_pourquoi(self, client, contenu):
        extraction = _lire(contenu, client)

        assert extraction.incident and "saisissez" in extraction.incident
        assert extraction.champs == {}


# ── Par les routes ────────────────────────────────────────────────────────────

FACTURE = {
    "emetteur": "QUINCAILLERIE DU WOURI",
    "niu_emetteur": "M091234567890Q",
    "niu_actif": True,
    "reference": "FA-2026-0815",
    "date_emission": "2026-09-12",
    "lignes": [
        {"designation": "Ciment 50 kg x 20", "montant_ht": "100000"},
        {"designation": "Fer à béton", "montant_ht": "50000"},
    ],
    "montant_ht": "150000",
    "montant_tva": "28875",
    "montant_ttc": "178875",
    "source": "LECTURE_AUTOMATIQUE",
}


def _piece(client, contenu=PDF) -> str:
    ouvrir_une_session(client, COMPTABLE)
    fichier = client.post(
        "/collecte/fichiers",
        params={"entreprise": BATIMENT},
        files={"fichier": ("facture.pdf", contenu, "application/pdf")},
    )
    assert fichier.status_code in (200, 201), fichier.text
    depot = client.post(
        "/collecte/pieces",
        json={
            "entreprise": BATIMENT,
            "canal": "DEPOT_CABINET",
            "empreinte": fichier.json()["empreinte"],
        },
    )
    assert depot.status_code in (200, 201), depot.text
    return depot.json()["piece"]["identifiant"]


@exige_postgresql
class TestDeLaPieceALEcriture:
    def test_la_facture_confirmee_fait_de_la_piece_une_facture_d_achat_lue(self, plateforme):
        client = plateforme
        piece = _piece(client)

        lue = client.post(f"/collecte/pieces/{piece}/facture", json=FACTURE)

        assert lue.status_code == 200, lue.text
        corps = lue.json()
        assert corps["etat"] == "LUE" and corps["type"] == "FACTURE_ACHAT"
        assert corps["facture"]["lue_par"] and len(corps["facture"]["lignes"]) == 2
        assert corps["reference_document"] == "FA-2026-0815"

    def test_des_montants_qui_ne_se_recoupent_pas_sont_refuses(self, plateforme):
        client = plateforme
        piece = _piece(client)

        refus = client.post(
            f"/collecte/pieces/{piece}/facture", json={**FACTURE, "montant_ttc": "180000"}
        )

        assert refus.status_code == 422, refus.text

    def test_l_ecriture_se_propose_depuis_la_piece(self, plateforme):
        client = plateforme
        piece = _piece(client)
        client.post(f"/collecte/pieces/{piece}/facture", json=FACTURE)

        proposition = client.post(
            f"/comptabilite/dossiers/{BATIMENT}/pieces/{piece}/proposition", json={}
        )

        assert proposition.status_code == 200, proposition.text
        corps = proposition.json()
        assert corps["comptabilisable"] is True, corps.get("empechements")
        lignes = corps["saisie"]["lignes"]
        credits = [ligne for ligne in lignes if ligne["compte"].startswith("401")]
        assert credits and Decimal(str(credits[0]["montant"])) == Decimal(178875)
        assert credits[0]["tiers"] == "M091234567890Q"
        assert corps["saisie"]["piece_justificative"] == "FA-2026-0815"

    def test_un_niu_non_verifie_bloque_avec_le_message_de_la_regle(self, plateforme):
        """⚠️ FAC-ID-003 : sans l'attestation « NIU actif », ni la TVA ni la charge ne se
        déduisent. La proposition reste bloquée et dit quoi faire, elle ne contourne pas."""
        client = plateforme
        piece = _piece(client)
        client.post(f"/collecte/pieces/{piece}/facture", json={**FACTURE, "niu_actif": None})

        corps = client.post(
            f"/comptabilite/dossiers/{BATIMENT}/pieces/{piece}/proposition", json={}
        ).json()

        assert corps["comptabilisable"] is False
        assert any("FAC-ID-003" in e for e in corps["empechements"])

    def test_une_piece_classee_ou_comptabilisee_ne_change_plus_de_facture(
        self, plateforme, moteur_test
    ):
        """⚠️ L'écriture cite la facture : la retoucher ferait mentir le lien entre les deux."""
        client = plateforme
        classee = _piece(client)
        client.post(f"/collecte/pieces/{classee}/facture", json=FACTURE)
        assert (
            client.post(
                f"/collecte/pieces/{classee}/classement",
                json={"motif": "doublon de la facture reçue par courriel"},
            ).status_code
            == 200
        )
        # La comptabilisation suit la validation de l'écriture, par la boîte d'envoi :
        # on pose l'état directement, c'est la garde de la route qu'on éprouve ici.
        comptabilisee = _piece(client, PDF + b"% autre\n")
        client.post(f"/collecte/pieces/{comptabilisee}/facture", json=FACTURE)
        with moteur_test.begin() as connexion:
            connexion.execute(
                text(
                    "UPDATE piece_justificative SET etat = 'COMPTABILISEE', "
                    "reference_ecriture = 'ECR-2026-AC-000004', donnees = "
                    "donnees::jsonb || '{\"etat\": \"COMPTABILISEE\", "
                    "\"reference_ecriture\": \"ECR-2026-AC-000004\"}'::jsonb "
                    "WHERE identifiant = :id"
                ),
                {"id": comptabilisee},
            )

        for piece in (classee, comptabilisee):
            refus = client.post(
                f"/collecte/pieces/{piece}/facture", json={**FACTURE, "montant_ttc": "999999"}
            )
            assert refus.status_code == 409, (piece, refus.text)
            facture = client.get(f"/collecte/pieces/{piece}").json()["piece"]["facture"]
            assert Decimal(facture["montant_ttc"]) == Decimal("178875")

    def test_ni_piece_d_un_autre_dossier_ni_piece_sans_facture(self, plateforme):
        client = plateforme
        piece = _piece(client)

        sans_facture = client.post(
            f"/comptabilite/dossiers/{BATIMENT}/pieces/{piece}/proposition", json={}
        )
        client.post(f"/collecte/pieces/{piece}/facture", json=FACTURE)
        # ⚠️ La réviseure couvre tous les dossiers : seul le contrôle « la pièce est de ce
        # dossier » peut refuser ici. Avec le comptable, un 403 de portée l'aurait masqué.
        client.cookies.clear()
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        autre_dossier = client.post(
            f"/comptabilite/dossiers/{COLOMBE}/pieces/{piece}/proposition", json={}
        )

        assert sans_facture.status_code == 404, sans_facture.text
        assert autre_dossier.status_code == 404, autre_dossier.text


@exige_postgresql
class TestLaLectureParLaRoute:
    def test_sans_cle_rien_ne_sort_du_serveur(self, plateforme):
        client = plateforme
        piece = _piece(client)

        assert client.get("/collecte/lecture-automatique").json() == {"disponible": False}
        refus = client.post(f"/collecte/pieces/{piece}/lecture-automatique")
        assert refus.status_code == 503, refus.text

    def test_avec_un_moteur_elle_propose_sans_rien_enregistrer(self, plateforme, monkeypatch):
        from app.contextes.collecte.adaptateurs.entrant import routes_facture_lue as collecte

        faux = FauxClient(reponse=_reponse(LUE))
        monkeypatch.setattr(
            collecte,
            "service_de_lecture",
            lambda: ExtractionClaude(cle_api="x", modele="claude-opus-5-5", client=faux),
        )
        client = plateforme
        piece = _piece(client)

        lecture = client.post(f"/collecte/pieces/{piece}/lecture-automatique")

        assert lecture.status_code == 200, lecture.text
        valeurs = {v["champ"]: v for v in lecture.json()["valeurs"]}
        assert valeurs["reference_document"]["valeur"] == "FA-2026-0815"
        assert valeurs["montant_ttc"]["a_relire"] is True
        assert valeurs["reference_document"]["a_relire"] is False
        # Rien n'est enregistré : la pièce est toujours reçue, sans facture.
        fiche = client.get(f"/collecte/pieces/{piece}").json()["piece"]
        assert fiche["facture"] is None and fiche["etat"] == "RECUE"
