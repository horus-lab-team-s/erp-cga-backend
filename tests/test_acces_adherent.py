"""Rendre l'accès à un adhérent, par son chargé de clientèle (pas 116).

Maquette « Espace adhérent CGA », vue A, note 5 : « réinitialisation par le chargé de clientèle ».

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Le type du lien** se déduit de l'état du compte : activation, réinitialisation, rien.
2. **Le geste de bout en bout** : le lien d'activation renvoyé à A-003 (« payé, mot de passe jamais
   défini ») sert vraiment à définir le mot de passe et à se connecter.
3. **Les garde-fous** : l'adresse du compte seulement, la vérification écrite, la limite du jour,
   le compte suspendu, le compte d'un autre dossier, le rôle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    EtatCompte,
    RenvoiRefuse,
    TypeJeton,
    atelier,
    reinitialiser_atelier,
    renvois_du_jour,
    type_de_lien,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee

BATIMENT = "M081234567890P"
TCHOUMBA = "P019876543210K"
CHARGEE = "p.moukouri@cga-brcg.cm"
VERIFICATION = "Rappelé au numéro du dossier, date de création confirmée"


class TestLeTypeDuLien:
    def test_selon_l_etat_du_compte(self):
        assert type_de_lien(EtatCompte.EN_ATTENTE_ACTIVATION) is TypeJeton.ACTIVATION
        assert type_de_lien(EtatCompte.ACTIF) is TypeJeton.REINITIALISATION
        with pytest.raises(RenvoiRefuse, match="suspendu"):
            type_de_lien(EtatCompte.SUSPENDU)

    def test_les_renvois_du_jour(self):
        jour = date(2026, 9, 17)
        assert renvois_du_jour([jour, date(2026, 9, 16), jour], jour) == 2


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    with horloge_figee(datetime(2026, 9, 17, 10, 0)):
        yield


def _client(courriel=CHARGEE):
    client = TestClient(creer_application())
    reponse = client.post("/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO})
    assert reponse.status_code == 200, reponse.text
    return client


def _derniers_liens():
    from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

    return service_de_notification().derniers("compte.acces_renvoye")


class TestLeGeste:
    def test_l_activation_renvoyee_ouvre_vraiment_l_espace(self):
        chargee = _client()
        (acces,) = chargee.get(f"/portefeuille/entreprises/{TCHOUMBA}/acces-adherents").json()
        assert acces["compte"] == "A-003" and acces["etat"] == "EN_ATTENTE_ACTIVATION"
        assert acces["liens_renvoyes"] == []

        envoi = chargee.post(
            f"/portefeuille/entreprises/{TCHOUMBA}/acces-adherents/A-003/lien",
            json={"verification": VERIFICATION},
        )
        assert envoi.status_code == 201, envoi.text
        assert envoi.json()["type"] == "ACTIVATION" and envoi.json()["par"] == "Patricia MOUKOURI"

        (courriel,) = _derniers_liens()
        assert courriel.destinataire == "e.tchoumba@tchoumbaetfils.cm"
        assert courriel.contexte["validite"] == "7 jours"
        assert courriel.contexte["lien"].startswith("/activation?jeton=")
        secret = courriel.contexte["lien"].split("jeton=", 1)[1]

        anonyme = TestClient(creer_application())
        defini = anonyme.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": secret, "mot_de_passe": "Quincaillerie-Akwa-2026!"},
        )
        assert defini.status_code == 200, defini.text
        assert anonyme.post(
            "/transverse/session",
            json={"courriel": "e.tchoumba@tchoumbaetfils.cm", "mot_de_passe": "Quincaillerie-Akwa-2026!"},
        ).status_code == 200

        (apres,) = chargee.get(f"/portefeuille/entreprises/{TCHOUMBA}/acces-adherents").json()
        assert apres["etat"] == "ACTIF"
        assert apres["liens_renvoyes"][0]["verification"] == VERIFICATION
        entree = next(
            e for e in atelier().journal.lister(objet_type="compte", objet_id="A-003")
            if e.action == "compte.lien_d_acces_renvoye"
        )
        assert entree.acteur == "C-007" and entree.apres["dossier"] == TCHOUMBA
        # Le jeton lui-même est émis au nom de la chargée, pas du « système » : qui a ouvert l'accès.
        emission = [
            e for e in atelier().journal.lister(objet_type="compte", objet_id="A-003") if e.action == "jeton.emis"
        ]
        assert emission[-1].acteur == "C-007"

    def test_un_compte_actif_recoit_une_reinitialisation(self):
        envoi = _client().post(
            f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-001/lien", json={"verification": VERIFICATION}
        )
        assert envoi.status_code == 201 and envoi.json()["type"] == "REINITIALISATION"
        (courriel,) = _derniers_liens()
        assert courriel.destinataire == "jp.nkoa@batimentplus.cm"
        assert courriel.contexte["validite"] == "2 heures"
        assert courriel.contexte["lien"].startswith("/reinitialisation?jeton=")


class TestLesGardeFous:
    def test_la_verification_est_ecrite(self):
        refus = _client().post(
            f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-001/lien", json={"verification": "ok"}
        )
        assert refus.status_code == 422 and "comment vous avez reconnu" in refus.json()["detail"]
        assert _derniers_liens() == []

    def test_la_limite_du_jour_puis_le_lendemain(self):
        chargee = _client()
        chemin = f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-001/lien"
        for _ in range(2):
            assert chargee.post(chemin, json={"verification": VERIFICATION}).status_code == 201
        troisieme = chargee.post(chemin, json={"verification": VERIFICATION})
        assert troisieme.status_code == 409 and "faites venir l'adhérent" in troisieme.json()["detail"]
        assert len(_derniers_liens()) == 2
        with horloge_figee(datetime(2026, 9, 18, 9, 0)):
            assert _client().post(chemin, json={"verification": VERIFICATION}).status_code == 201

    def test_le_compte_suspendu_et_le_compte_d_un_autre_dossier(self):
        boutique = atelier()
        boutique.comptes.enregistrer(boutique.comptes.lire("A-001").suspendre())
        chargee = _client()
        suspendu = chargee.post(
            f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-001/lien", json={"verification": VERIFICATION}
        )
        assert suspendu.status_code == 409 and "suspendu" in suspendu.json()["detail"]
        # A-002 est l'adhérente de LA COLOMBE, pas de BATIMENT PLUS.
        autre = chargee.post(
            f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-002/lien", json={"verification": VERIFICATION}
        )
        assert autre.status_code == 404
        assert _derniers_liens() == []

    def test_seul_le_charge_de_clientele(self):
        for courriel in ("l.fotso@cga-brcg.cm", "jp.nkoa@batimentplus.cm"):
            client = _client(courriel)
            assert client.get(f"/portefeuille/entreprises/{BATIMENT}/acces-adherents").status_code in (403, 404)
            assert client.post(
                f"/portefeuille/entreprises/{BATIMENT}/acces-adherents/A-001/lien", json={"verification": VERIFICATION}
            ).status_code in (403, 404)
