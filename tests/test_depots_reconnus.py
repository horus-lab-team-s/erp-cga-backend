"""Une déclaration déposée cesse d'être en retard, partout où le retard se lit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 58)

L'échéancier se recalcule à chaque lecture et ne lisait pas les accusés. Une TVA de
juillet déposée par la route de dépôt, accusé consigné, restait donc « à faire, en
retard » : la relance J+1 partait, le jour où la pénalité commence à courir, vers un
adhérent à jour, et le tableau de bord de la direction comptait le retard.

Mesuré par une sonde avant la correction : `('A_FAIRE', True)` à l'échéancier, et
`('TVA', '2026-07-31', 1)` aux relances, pour une déclaration déposée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.test_teledeclaration import NIU, PARAMS, _renforcer


@pytest.fixture
def client() -> TestClient:
    """Le montage de `test_teledeclaration.py` : atelier neuf, réviseur connecté.

    Recopié plutôt qu'importé : une fixture importée sous son nom est redéfinie par
    chaque paramètre qui la reçoit, et le linter a raison de le signaler.
    """
    reinitialiser_atelier()
    application = TestClient(creer_application())
    reponse = application.post(
        "/transverse/session",
        json={"courriel": "a.bouba@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return application


DEPOT = {"numero": "DGI-2026-0007741", "depose_le": "2026-08-14T10:22:00"}


def _deposer_juillet(session):
    _renforcer(session)
    reponse = session.post(f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS, json=DEPOT)
    assert reponse.status_code == 200, reponse.text


def _tva_juillet(session, a_la_date="2026-09-20"):
    lignes = session.get(
        f"/obligations/dossiers/{NIU}/echeancier",
        params={"exercice": "2026", "a_la_date": a_la_date},
    ).json()
    (ligne,) = [
        ligne
        for ligne in lignes
        if ligne["obligation"]["code_obligation"] == "TVA"
        and ligne["obligation"]["periode_fin"] == PARAMS["periode_fin"]
    ]
    return ligne


def _relances_tva_juillet(session):
    reponse = session.get("/obligations/relances", params={"a_la_date": "2026-08-16"})
    assert reponse.status_code == 200, reponse.text
    return [
        r
        for r in reponse.json()
        if r["obligation"]["entreprise"] == NIU
        and r["obligation"]["code_obligation"] == "TVA"
        and r["obligation"]["periode_fin"] == PARAMS["periode_fin"]
    ]


@pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
class TestParLesRoutes:
    def test_avant_le_depot_la_tva_echue_est_en_retard_et_relancee(self, client):
        """⚠️ La contre-épreuve : sans elle, un échéancier qui ne relancerait plus rien
        ferait passer les cas suivants."""
        assert _tva_juillet(client)["en_retard"] is True
        assert [r["jalon"] for r in _relances_tva_juillet(client)] == [1]

    def test_apres_le_depot_l_echeancier_la_dit_declaree(self, client):
        _deposer_juillet(client)
        ligne = _tva_juillet(client)
        assert ligne["obligation"]["statut"] == "DECLAREE"
        assert ligne["obligation"]["declaree_le"] == "2026-08-14"
        assert ligne["obligation"]["reference_depot"] == DEPOT["numero"]
        assert ligne["en_retard"] is False

    def test_l_empreinte_du_bordereau_est_inscrite_au_journal(self, client):
        """⚠️ Pas 106 : elle était expurgée avant l'écriture, sous la clé `empreinte`."""
        _deposer_juillet(client)
        [entree] = [
            e for e in client.get("/transverse/audit").json() if e["action"] == "obligation.deposee"
        ]
        empreinte = entree["apres"]["sha256_du_bordereau"]
        assert len(empreinte) == 64 and empreinte != "«expurgé»"

    def test_apres_le_depot_aucune_relance_ne_part(self, client):
        _deposer_juillet(client)
        assert _relances_tva_juillet(client) == []

    def test_apres_le_depot_le_pilotage_ne_compte_plus_le_retard(self, client):
        def retards_tva_juillet():
            direction = TestClient(client.app)
            session = direction.post(
                "/transverse/session",
                json={"courriel": "b.mballa@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
            )
            assert session.status_code == 200, session.text
            corps = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-09-20").json()
            (ligne,) = [r for r in corps["risques"] if r["entreprise"] == NIU]
            return [e for m in ligne["mesures"] for e in m["elements"] if e == "TVA 07/2026"]

        assert retards_tva_juillet() == ["TVA 07/2026"], "la contre-épreuve, avant le dépôt"
        _deposer_juillet(client)
        assert retards_tva_juillet() == []

    def test_le_controle_deja_declaree_se_declenche_enfin(self, client):
        """⚠️ **Un contrôle qui ne pouvait jamais parler.**

        La recevabilité refuse une obligation « portant déjà sa date de dépôt ».
        L'échéancier naissant toujours « à faire », ce contrôle ne se déclenchait par
        aucune route : seul le refus de l'accusé existant protégeait du double dépôt.
        Né d'une mutation survivante, qui retirait les accusés au dossier de TVA sans
        rien casser.
        """
        _deposer_juillet(client)
        dossier = client.get(f"/obligations/dossiers/{NIU}/depot-tva", params=PARAMS).json()
        codes = {a["code"] for a in dossier["recevabilite"]["bloquants"]}
        assert {"DEPOT-DEJA-EFFECTUE", "OBLIGATION-DEJA-DECLAREE"} <= codes, codes
