"""La lecture préparée au dépôt (18 octobre 2026, point 13).

⚠️ CE QUI EST GARDÉ
* les points à vérifier sont constatés sans l'agent : NIU absent, HT + TVA ≠ TTC ;
* rien ne se lit sans les trois conditions : fonctionnalité active, accord de
  l'adhérent, clé de l'agent ;
* l'adhérent seul donne son accord ; la direction active ; le comptable lit la proposition ;
* une panne de l'agent est rangée avec son incident, et ne bloque rien.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.contextes.collecte.domaine.lecture_au_depot import ValeurPreparee, points_a_verifier
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_facture_vers_ecriture import LUE, PDF, FauxClient, _reponse

BATIMENT = NIU_DEMO["BATIMENT"]
ADHERENT = "jp.nkoa@batimentplus.cm"
DIRECTRICE = "b.mballa@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"


def _v(champ: str, valeur, a_relire: bool = False) -> ValeurPreparee:
    return ValeurPreparee(champ=champ, valeur=valeur, confiance=Decimal("0.9"), a_relire=a_relire)


class TestLesPointsAVerifier:
    def test_ils_se_constatent_sans_l_agent(self):
        points = points_a_verifier(
            [
                _v("reference_document", "FA-1"),
                _v("date_emission", "2026-10-01"),
                _v("montant_ht", "100000"),
                _v("montant_tva", "19250"),
                _v("montant_ttc", "120000", a_relire=True),
            ]
        )
        assert any("NIU" in p for p in points)
        assert any("ne fait pas le TTC" in p for p in points)
        assert any("montant_ttc" in p for p in points)

    def test_une_facture_coherente_n_a_que_ses_champs_a_relire(self):
        points = points_a_verifier(
            [
                _v("niu_emetteur", "M012345678901A"),
                _v("reference_document", "FA-1"),
                _v("date_emission", "2026-10-01"),
                _v("montant_ht", "100000"),
                _v("montant_tva", "19250"),
                _v("montant_ttc", "119250"),
            ]
        )
        assert points == []


def _depot_de_l_adherent(client) -> str:
    ouvrir_une_session(client, ADHERENT)
    fichier = client.post(
        "/collecte/fichiers",
        params={"entreprise": BATIMENT},
        files={
            "fichier": ("facture.pdf", PDF + datetime.now().isoformat().encode(), "application/pdf")
        },
    )
    assert fichier.status_code in (200, 201), fichier.text
    depot = client.post(
        "/collecte/pieces",
        json={"entreprise": BATIMENT, "canal": "PORTAIL", "empreinte": fichier.json()["empreinte"]},
    )
    assert depot.status_code in (200, 201), depot.text
    return depot.json()["piece"]["identifiant"]


def _un_passage() -> str:
    from app.contextes.collecte.adaptateurs.entrant.routes_lecture_au_depot import (
        lire_les_pieces_deposees,
    )
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
        return lire_les_pieces_deposees()


@exige_postgresql
class TestParLesRoutes:
    def test_rien_ne_se_lit_sans_l_accord_puis_tout_se_prepare(self, plateforme, monkeypatch):
        from app.contextes.collecte.adaptateurs.entrant import routes_lecture_au_depot as module
        from app.contextes.collecte.adaptateurs.sortant.extraction_claude import ExtractionClaude

        monkeypatch.setattr(
            module,
            "service_de_lecture",
            lambda: ExtractionClaude(
                cle_api="x", modele="claude-opus-5-5", client=FauxClient(reponse=_reponse(LUE))
            ),
        )
        client = plateforme
        piece = _depot_de_l_adherent(client)
        assert _un_passage() == "lecture au dépôt coupée"

        # Le cabinet active ; l'adhérent n'a pas encore consenti : rien ne sort.
        client.cookies.clear()
        ouvrir_une_session(client, DIRECTRICE)
        active = client.put(
            "/collecte/lecture-au-depot",
            json={"actif": True, "motif": "décision de la direction du 18 octobre"},
        )
        assert active.status_code == 200 and active.json()["actif"] is True, active.text
        assert _un_passage() == "0 pièce(s) lue(s)"
        # Le cabinet ne consent pas à la place de l'adhérent.
        refus = client.put(
            f"/collecte/dossiers/{BATIMENT}/consentement-de-lecture", json={"accorde": True}
        )
        assert refus.status_code in (403, 404), refus.text

        client.cookies.clear()
        ouvrir_une_session(client, ADHERENT)
        accord = client.put(
            f"/collecte/dossiers/{BATIMENT}/consentement-de-lecture", json={"accorde": True}
        )
        assert accord.status_code == 200 and accord.json()["accorde"] is True, accord.text
        assert _un_passage().startswith(("1 pièce", "2 pièce", "3 pièce", "4 pièce", "5 pièce"))

        client.cookies.clear()
        ouvrir_une_session(client, COMPTABLE)
        lecture = client.get(f"/collecte/pieces/{piece}/lecture-preparee")
        assert lecture.status_code == 200, lecture.text
        valeurs = {v["champ"]: v for v in lecture.json()["valeurs"]}
        assert valeurs["reference_document"]["valeur"] == "FA-2026-0815"
        # La pièce n'a pas bougé : la lecture propose, elle n'identifie rien.
        assert client.get(f"/collecte/pieces/{piece}").json()["piece"]["etat"] == "RECUE"

    def test_une_panne_de_l_agent_est_rangee_et_ne_bloque_rien(self, plateforme, monkeypatch):
        from app.contextes.collecte.adaptateurs.entrant import routes_lecture_au_depot as module

        class EnPanne:
            def extraire(self, *_a, **_k):
                raise RuntimeError("réseau coupé")

        monkeypatch.setattr(module, "service_de_lecture", lambda: EnPanne())
        client = plateforme
        piece = _depot_de_l_adherent(client)
        client.put(f"/collecte/dossiers/{BATIMENT}/consentement-de-lecture", json={"accorde": True})
        client.cookies.clear()
        ouvrir_une_session(client, DIRECTRICE)
        client.put("/collecte/lecture-au-depot", json={"actif": True, "motif": "essai de la panne"})

        _un_passage()

        client.cookies.clear()
        ouvrir_une_session(client, COMPTABLE)
        lecture = client.get(f"/collecte/pieces/{piece}/lecture-preparee").json()
        assert "réseau" not in lecture["incident"] and "RuntimeError" in lecture["incident"]
