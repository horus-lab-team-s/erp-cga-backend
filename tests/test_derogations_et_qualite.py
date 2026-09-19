"""Le journal des dérogations et la qualité des règles (pas 99).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **Le journal** : chaque écart du périmètre, avec l'enjeu relevé **au moment de l'écart**,
  le nom de ses auteurs, des filtres, un total d'enjeu levé qui ne compte que les dérogations
  effectives, et un export CSV lisible par un tableur (accents compris). Il est ouvert à qui
  audite (`LIRE_AUDIT`), pas à qui contrôle seulement.
- **La qualité des règles** : constats comptés sur le rapport brut, écartés sur le rapport
  arbitré, et une lecture selon des seuils lus au référentiel, jamais sur trop peu de constats.
- **Le signalement** : un fait au journal, qui notifie le fiscaliste.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.conformite.api import FACTURES_DEMO, moteur_par_defaut, vider_les_ecarts
from app.contextes.conformite.domaine.ecarts import RevueDesRegles
from app.contextes.conformite.domaine.qualite_des_regles import (
    LectureDeRegle,
    lire_la_regle,
    mesurer_les_regles,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

REVISEUR = "a.bouba@cga-brcg.cm"
FISCALISTE = "r.ebolo@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
MOTIF = "Désignation détaillée au bon de commande joint."


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_ecarts()
    yield
    vider_les_ecarts()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _ecarter(client: TestClient, piece: str, regle: str):
    reponse = client.post(
        f"/conformite/pieces/{piece}/ecarts", json={"code_regle": regle, "motif": MOTIF}
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


# ── La lecture d'une règle ────────────────────────────────────────────────────


class TestLaLecture:
    REVUE = RevueDesRegles(a_recalibrer=0.34, trop_bruyante=0.60, constats_minimum=5)

    @pytest.mark.parametrize(
        ("constats", "ecartes", "attendue"),
        [
            (4, 4, LectureDeRegle.NON_JUGEE),
            (10, 3, LectureDeRegle.PEU_CONTESTEE),
            (100, 34, LectureDeRegle.A_RECALIBRER),
            (10, 5, LectureDeRegle.A_RECALIBRER),
            (10, 6, LectureDeRegle.TROP_BRUYANTE),
        ],
    )
    def test_les_seuils_sont_atteints_des_leur_valeur(self, constats, ecartes, attendue):
        assert lire_la_regle(constats, ecartes, self.REVUE) is attendue

    def test_des_seuils_inverses_sont_refuses(self):
        with pytest.raises(ValidationError):
            RevueDesRegles(a_recalibrer=0.7, trop_bruyante=0.5)

    def test_les_constats_se_comptent_sur_le_brut_et_les_ecartes_sur_l_arbitre(self):
        moteur = moteur_par_defaut()
        brut = moteur.controler(FACTURES_DEMO["F-2026-0415"])
        [constat] = brut.constats
        from app.contextes.conformite.domaine.entites import ConstatEcarte

        arbitre = brut.model_copy(
            update={
                "constats": [],
                "constats_ecartes": [ConstatEcarte(constat=constat, identifiant_ecart="x")],
            }
        )
        [stat] = [
            s
            for s in mesurer_les_regles(moteur.regles, [(brut, arbitre)], self.REVUE)
            if s.code == constat.code_regle
        ]
        assert (stat.constats, stat.ecartes, stat.taux_d_ecartement) == (1, 1, 1.0)
        assert stat.enjeu_retenu == Decimal(0)


# ── Le journal ────────────────────────────────────────────────────────────────


class TestLeJournal:
    def test_il_porte_l_enjeu_au_moment_de_l_ecart_et_ne_totalise_que_les_effectives(self):
        reviseur = _client(REVISEUR)
        _ecarter(reviseur, "F-2026-0415", "FAC-DOC-011")
        majeur = _ecarter(reviseur, "F-2026-0412", "FAC-ACH-007")
        assert majeur["ecart"]["statut"] == "EN_ATTENTE"
        assert Decimal(majeur["ecart"]["enjeu"]) > 0

        journal = reviseur.get("/conformite/derogations").json()
        assert journal["total"] == 2 and journal["effectives"] == 1
        assert Decimal(journal["enjeu_leve"]) == 0, "l'écart en attente n'a rien levé"
        assert journal["auteurs"] == {"C-003": "Aïcha BOUBA"}

        _client(FISCALISTE).post(
            f"/conformite/pieces/F-2026-0412/ecarts/{majeur['ecart']['identifiant']}/second-regard",
            json={"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."},
        )
        journal = reviseur.get("/conformite/derogations").json()
        assert Decimal(journal["enjeu_leve"]) == Decimal(majeur["ecart"]["enjeu"])
        assert journal["auteurs"]["C-006"] == "Roger EBOLO"

    def test_les_filtres(self):
        reviseur = _client(REVISEUR)
        _ecarter(reviseur, "F-2026-0415", "FAC-DOC-011")
        _ecarter(reviseur, "F-2026-0412", "FAC-ACH-007")
        par_regle = reviseur.get("/conformite/derogations", params={"regle": "FAC-ACH-007"}).json()
        assert [d["code_regle"] for d in par_regle["derogations"]] == ["FAC-ACH-007"]
        par_statut = reviseur.get("/conformite/derogations", params={"statut": "EFFECTIF"}).json()
        assert [d["code_regle"] for d in par_statut["derogations"]] == ["FAC-DOC-011"]

    def test_l_export_se_lit_dans_un_tableur(self):
        reviseur = _client(REVISEUR)
        _ecarter(reviseur, "F-2026-0415", "FAC-DOC-011")
        export = reviseur.get("/conformite/derogations/export")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert export.content.startswith("﻿".encode())
        lignes = export.content.decode("utf-8-sig").strip().splitlines()
        assert lignes[0].startswith("Date;Dossier;Pièce;Règle")
        assert "Désignation détaillée" in lignes[1] and "Aïcha BOUBA" in lignes[1]

    def test_l_inspecteur_ne_lit_que_les_derogations_de_son_dossier(self):
        """Il audite une mission sur AGRO-NKOLO : les dérogations des autres dossiers lui
        apprendraient ce que le cabinet accepte ailleurs."""
        reviseur = _client(REVISEUR)
        _ecarter(reviseur, "F-2026-0415", "FAC-DOC-011")  # AGRO
        _ecarter(reviseur, "F-2026-0435", "FAC-DOC-011")  # BATIMENT
        inspecteur = _client("g.atangana@inspection.cm").get("/conformite/derogations").json()
        assert {d["dossier"] for d in inspecteur["derogations"]} == {"M065544332211L"}

    def test_qui_controle_sans_auditer_ne_lit_pas_le_journal(self):
        assert _client(COMPTABLE).get("/conformite/derogations").status_code == 403
        assert _client(COMPTABLE).get("/conformite/derogations/export").status_code == 403
        assert _client(DIRECTION).get("/conformite/derogations").status_code == 200


# ── La qualité et le signalement ──────────────────────────────────────────────


class TestLaQualiteDesRegles:
    def test_les_ecarts_du_cabinet_se_lisent_dans_la_mesure(self):
        reviseur = _client(REVISEUR)
        avant = {r["code"]: r for r in reviseur.get("/conformite/regles/qualite").json()["regles"]}
        _ecarter(reviseur, "F-2026-0415", "FAC-DOC-011")
        apres = reviseur.get("/conformite/regles/qualite").json()
        par_code = {r["code"]: r for r in apres["regles"]}
        assert par_code["FAC-DOC-011"]["constats"] == avant["FAC-DOC-011"]["constats"]
        assert par_code["FAC-DOC-011"]["ecartes"] == avant["FAC-DOC-011"]["ecartes"] + 1
        assert apres["regles"][0]["code"] == "FAC-DOC-011", "la plus contestée vient d'abord"
        assert apres["seuils"]["constats_minimum"] == 5

    def test_la_periode_borne_les_pieces(self):
        reviseur = _client(REVISEUR)
        vide = reviseur.get(
            "/conformite/regles/qualite", params={"du": "2020-01-01", "au": "2020-12-31"}
        ).json()
        assert vide["pieces_controlees"] == 0 and vide["taux_global"] is None
        assert (
            reviseur.get(
                "/conformite/regles/qualite", params={"du": "2026-09-01", "au": "2026-01-01"}
            ).status_code
            == 422
        )

    def test_le_perimetre_borne_les_pieces(self):
        tout = _client(REVISEUR).get("/conformite/regles/qualite").json()["pieces_controlees"]
        portefeuille = (
            _client(COMPTABLE).get("/conformite/regles/qualite").json()["pieces_controlees"]
        )
        assert 0 < portefeuille < tout

    def test_le_signalement_notifie_le_fiscaliste(self):
        reponse = _client(REVISEUR).post(
            "/conformite/regles/FAC-DOC-011/signalement",
            json={"motif": "Écartée deux fois sur trois."},
        )
        assert reponse.status_code == 200, reponse.text
        titres = [
            n["titre"]
            for n in _client(FISCALISTE).get("/transverse/notifications").json()["notifications"]
        ]
        assert "Règle signalée : FAC-DOC-011" in titres
        assert (
            _client(REVISEUR)
            .post(
                "/conformite/regles/FAC-XXX-999/signalement",
                json={"motif": "Règle qui n'existe pas."},
            )
            .status_code
            == 404
        )


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_le_journal_lit_les_ecarts_en_base(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        _ecarter(client, "F-2026-0415", "FAC-DOC-011")
        journal = client.get("/conformite/derogations").json()
        assert journal["total"] == 1 and journal["derogations"][0]["code_regle"] == "FAC-DOC-011"
