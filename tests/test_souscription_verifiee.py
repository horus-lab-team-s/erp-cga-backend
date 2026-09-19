"""Payer ne prouve pas qu'on est l'entreprise : l'accès attend la vérification du cabinet.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 83)

La souscription publique demandait un NIU, déclaratif, et l'encaissement ouvrait aussitôt
un compte adhérent **portant ce NIU**. Rien ne rattachait le payeur à l'entreprise. Le
défaut a été rejoué de bout en bout avant d'être corrigé : un devis d'adhésion au NIU de
SARL BATIMENT PLUS (un vrai dossier du cabinet), un paiement, un lien d'activation dans
la boîte du payeur, un mot de passe, et le payeur lisait la fiche de l'entreprise, ses
pièces, son échéancier, et pouvait y déposer des fichiers. 12 500 FCFA pour lire la
comptabilité d'autrui.

Ce que la correction change :

- l'encaissement reste acquis (l'argent n'est ni rendu ni perdu), la souscription reste
  PAYÉE, et le journal note `souscription.verification_requise` ;
- l'accès ne s'ouvre que par la route du cabinet, réservée à `GERER_COMPTES`, qui exige
  une description de la vérification faite, et nomme son auteur au journal.

Comment prouver l'identité d'une entreprise en ligne reste une question ouverte (Q24).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.souscription.adaptateurs.entrant.routes_http import reinitialiser_comptoir
from app.contextes.souscription.api import VerificationDIdentite, activer_souscription
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, NIU_DEMO, atelier, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 9, 14, 10, 0)
#: SARL BATIMENT PLUS, un vrai dossier des données de démonstration.
NIU_D_AUTRUI = NIU_DEMO["BATIMENT"]
COURRIEL_DE_L_INCONNU = "curieux@exemple.cm"
VERIFICATION = "RCCM et CNI du gérant présentés au cabinet ce jour."


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    """En mode démonstration : le paiement s'y valide d'office, ce qui est exactement le
    chemin le plus court pour l'inconnu. Si la faille tient fermée ici, elle tient
    fermée avec un vrai paiement."""
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true")
    configuration.cache_clear()
    reinitialiser_atelier()
    reinitialiser_comptoir()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())
    configuration.cache_clear()


def _payer_au_niu_d_autrui(client: TestClient) -> str:
    """Le parcours de l'inconnu : devis au NIU d'un dossier du cabinet, puis paiement.
    Rend la référence de la souscription."""
    devis = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "CURIEUX",
                "prenom": "Un",
                "courriel": COURRIEL_DE_L_INCONNU,
                "telephone": "699000111",
                "denomination": "SARL BATIMENT PLUS",
                "niu": NIU_D_AUTRUI,
                "chiffre_affaires_declare": "20000000",
            },
            "lignes": [{"service": "ADHESION"}],
        },
    )
    assert devis.status_code == 201, devis.text
    engagement = client.post(f"/souscription/devis/{devis.json()['reference']}/engagement", json={})
    assert engagement.status_code == 201, engagement.text
    assert engagement.json()["paiement"]["statut"] == "VALIDE"
    return engagement.json()["souscription"]["reference"]


def _cabinet(courriel: str) -> TestClient:
    """Un client HTTP à part pour le cabinet : sa session ne doit pas remplacer celle du
    visiteur."""
    cabinet = TestClient(creer_application())
    reponse = cabinet.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return cabinet


class TestLaFailleResteFermee:
    def test_payer_au_niu_d_autrui_n_ouvre_aucun_compte(self, client: TestClient):
        reference = _payer_au_niu_d_autrui(client)

        assert atelier().comptes.par_courriel(COURRIEL_DE_L_INCONNU) is None
        # Aucun lien d'activation n'est parti : c'est lui qui menait au dossier.
        assert client.get("/transverse/courriels").json() == []
        souscription = client.get(f"/souscription/souscriptions/{reference}").json()
        assert souscription["etat"] == "PAYEE"
        assert souscription["compte"] is None

    def test_l_inconnu_ne_lit_pas_le_dossier(self, client: TestClient):
        """La preuve par l'usage : même en tentant de se connecter, rien ne s'ouvre."""
        _payer_au_niu_d_autrui(client)
        session = client.post(
            "/transverse/session",
            json={"courriel": COURRIEL_DE_L_INCONNU, "mot_de_passe": "n importe quoi 12"},
        )
        assert session.status_code == 401
        assert client.get(f"/portefeuille/entreprises/{NIU_D_AUTRUI}").status_code == 401

    def test_le_paiement_reste_acquis_et_le_cabinet_le_voit(self, client: TestClient):
        """Refuser l'accès ne doit pas perdre l'argent ni le client légitime : la
        souscription apparaît dans la liste de surveillance du cabinet."""
        reference = _payer_au_niu_d_autrui(client)
        a_activer = _cabinet("b.mballa@cga-brcg.cm").get("/souscription/a-activer")
        assert a_activer.status_code == 200
        assert [s["reference"] for s in a_activer.json()] == [reference]

    def test_l_administration_des_comptes_lit_ce_qu_elle_doit_ouvrir(self, client: TestClient):
        """⚠️ Avant le pas 83, seule la direction lisait la liste, et seule
        l'administration ouvrait : personne ne pouvait faire les deux moitiés du geste."""
        reference = _payer_au_niu_d_autrui(client)
        a_activer = _cabinet("s.onana@cga-brcg.cm").get("/souscription/a-activer")
        assert a_activer.status_code == 200
        assert [s["reference"] for s in a_activer.json()] == [reference]
        # Et un rôle qui n'a ni l'une ni l'autre permission reste dehors.
        assert _cabinet("l.fotso@cga-brcg.cm").get("/souscription/a-activer").status_code == 403

    def test_le_journal_dit_qu_une_verification_est_requise(self, client: TestClient):
        _payer_au_niu_d_autrui(client)
        from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

        actions = [e.action for e in comptoir().journal.lister()]
        assert "souscription.verification_requise" in actions
        assert "souscription.activee" not in actions


class TestLaRouteDuCabinet:
    def test_anonyme_refuse(self, client: TestClient):
        reference = _payer_au_niu_d_autrui(client)
        reponse = client.post(
            f"/souscription/souscriptions/{reference}/activation",
            json={"verification": VERIFICATION},
        )
        assert reponse.status_code == 401

    def test_un_comptable_ne_peut_pas_ouvrir_un_acces(self, client: TestClient):
        """Ouvrir un dossier à un tiers est un acte d'administration des comptes."""
        reference = _payer_au_niu_d_autrui(client)
        reponse = _cabinet("l.fotso@cga-brcg.cm").post(
            f"/souscription/souscriptions/{reference}/activation",
            json={"verification": VERIFICATION},
        )
        assert reponse.status_code == 403
        assert atelier().comptes.par_courriel(COURRIEL_DE_L_INCONNU) is None

    @pytest.mark.parametrize(
        "corps",
        [
            {},
            {"verification": ""},
            {"verification": "vu"},
            {"verification": VERIFICATION, "par": "C-001"},
        ],
        ids=["sans-verification", "vide", "trop-courte", "auteur-impose"],
    )
    def test_la_verification_se_decrit(self, client: TestClient, corps):
        """⚠️ `par` n'est pas accepté du corps : l'auteur est la session, sinon un
        administrateur ferait porter l'ouverture à un collègue."""
        reference = _payer_au_niu_d_autrui(client)
        reponse = _cabinet("s.onana@cga-brcg.cm").post(
            f"/souscription/souscriptions/{reference}/activation", json=corps
        )
        assert reponse.status_code == 422
        assert atelier().comptes.par_courriel(COURRIEL_DE_L_INCONNU) is None

    def test_reference_inconnue(self, client: TestClient):
        reponse = _cabinet("s.onana@cga-brcg.cm").post(
            "/souscription/souscriptions/SO-INCONNUE/activation",
            json={"verification": VERIFICATION},
        )
        assert reponse.status_code == 404

    def test_la_verification_ouvre_et_nomme_son_auteur(self, client: TestClient):
        reference = _payer_au_niu_d_autrui(client)
        reponse = _cabinet("s.onana@cga-brcg.cm").post(
            f"/souscription/souscriptions/{reference}/activation",
            json={"verification": VERIFICATION},
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["etat"] == "ACTIVEE"
        assert client.get("/transverse/courriels", params={"code": "compte.activation"}).json()

        from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

        activee = [e for e in comptoir().journal.lister() if e.action == "souscription.activee"]
        assert len(activee) == 1
        assert activee[0].apres["verifiee_par"] == "C-002"
        assert activee[0].apres["verification"] == VERIFICATION


class TestLeContratDuCasDUsage:
    def test_la_verification_est_obligatoire_a_l_appel(self):
        """Sans valeur par défaut : oublier la vérification est une erreur à l'écriture
        du code, pas une ouverture silencieuse."""
        with pytest.raises(TypeError):
            activer_souscription(  # type: ignore[call-arg]
                "SO-1", souscriptions=None, acces=None, journal=None, a_l_instant=INSTANT
            )

    @pytest.mark.parametrize(
        "champs",
        [{"par": "", "comment": VERIFICATION}, {"par": "C-002", "comment": "trop court"}],
        ids=["sans-auteur", "description-courte"],
    )
    def test_une_verification_vide_est_refusee(self, champs):
        with pytest.raises(ValidationError):
            VerificationDIdentite(**champs)
