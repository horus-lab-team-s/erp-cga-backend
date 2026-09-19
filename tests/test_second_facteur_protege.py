"""Le second facteur ne tombe plus devant le mot de passe.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 60)

`POST /transverse/second-facteur` écrasait le secret de tout compte connecté et rendait
le nouveau. Avec le seul mot de passe d'un réviseur, ou sa session volée, on enrôlait
son propre appareil, on renforçait la session, et l'on déposait des déclarations : le
second facteur ne protégeait de rien, et le titulaire perdait le sien sans le savoir.

Le code le justifiait : « ce qui protège, c'est que l'acte laisse une trace datée, pas
qu'il soit impossible ». La trace arrivait après les dégâts.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    atelier,
    code_attendu,
    reinitialiser_atelier,
)
from app.main import creer_application
from app.partage.horloge import maintenant
from tests.conftest import enroler_par_le_courriel, exige_postgresql, ouvrir_une_session

REVISEUR = ("C-003", "a.bouba@cga-brcg.cm")
ADMINISTRATEUR = ("C-002", "s.onana@cga-brcg.cm")
MOTIF = "Téléphone volé, signalé par écrit le 14/09 et vérifié par appel au titulaire."


@pytest.fixture
def application():
    reinitialiser_atelier()
    atelier().notifications.vider()
    return creer_application()


def _session(application, courriel):
    client = TestClient(application)
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _enroler(client):
    return client.post("/transverse/second-facteur")


def _renforcer(client, secret):
    return client.post(
        "/transverse/session/renforcement", json={"code": code_attendu(secret, maintenant())}
    )


def _courriels(code):
    return [m.destinataire for m in atelier().notifications.derniers(code)]


class TestLAttaque:
    def test_un_mot_de_passe_vole_ne_remplace_plus_le_second_facteur(self, application):
        """⚠️ **Le scénario de la faille, joué jusqu'au dépôt.**"""
        titulaire = _session(application, REVISEUR[1])
        secret_du_titulaire = enroler_par_le_courriel(titulaire)

        intrus = _session(application, REVISEUR[1])  # le même mot de passe, ailleurs
        tentative = _enroler(intrus)
        assert tentative.status_code == 409, tentative.text
        assert "secret" not in tentative.json(), "aucun nouveau secret ne doit sortir"
        assert "appareil actuel" in tentative.text

        # L'intrus ne peut toujours pas agir : il n'a aucun code valable.
        depot = intrus.post(
            "/obligations/dossiers/M081234567890P/depots",
            json={
                "code_obligation": "CNPS", "periode_debut": "2026-07-01",
                "periode_fin": "2026-07-31", "numero": "X", "depose_le": "2026-08-12T09:00:00",
            },
        )
        assert depot.status_code == 403, depot.text

        # ⚠️ La contre-épreuve : le secret du titulaire est intact et fonctionne toujours.
        assert _renforcer(titulaire, secret_du_titulaire).json()["facteur_fort"] is True


class TestLePremierEnrolementPasseParLaBoite:
    """⚠️ **La limite écrite au pas 60, fermée au pas 62.**

    Un compte jamais enrôlé s'enrôlait avec sa session : qui volait le mot de passe d'un
    réviseur qui n'avait pas encore enrôlé enrôlait à sa place. Le premier enrôlement se
    prouve désormais par la boîte aux lettres du compte.
    """

    @staticmethod
    def _jeton_envoye():
        (dernier, *_) = atelier().notifications.derniers("compte.second_facteur_confirmation")
        return dernier.contexte["lien"].split("jeton=", 1)[1]

    def test_un_mot_de_passe_seul_ne_rend_aucune_cle(self, application):
        intrus = _session(application, REVISEUR[1])
        reponse = _enroler(intrus)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["secret"] is None and corps["uri"] is None
        assert corps["confirmation_par_courriel"] is True
        # Le lien part à l'adresse du compte, pas à l'appelant.
        (message,) = atelier().notifications.derniers("compte.second_facteur_confirmation")
        assert message.destinataire == REVISEUR[1]
        assert intrus.get("/transverse/moi").json()["second_facteur_enrole"] is False

    def test_le_lien_d_un_autre_compte_est_refuse(self, application):
        _enroler(_session(application, REVISEUR[1]))
        jeton_du_reviseur = self._jeton_envoye()
        autre = _session(application, "l.fotso@cga-brcg.cm")
        reponse = autre.post(
            "/transverse/second-facteur/confirmation", json={"jeton": jeton_du_reviseur}
        )
        assert reponse.status_code == 410, reponse.text
        assert "C-003" not in reponse.text, "le refus ne dit pas à qui le lien appartenait"

    def test_un_lien_de_mot_de_passe_ne_confirme_pas_un_enrolement(self, application):
        """Les liens se ressemblent, leurs usages non : un lien « mot de passe oublié »
        consommé ici détruirait la réinitialisation et enrôlerait à sa place. Né d'une
        mutation survivante."""
        client = _session(application, REVISEUR[1])
        assert TestClient(application).post(
            "/transverse/mot-de-passe/oubli", json={"courriel": REVISEUR[1]}
        ).status_code == 202
        (message, *_) = atelier().notifications.derniers("compte.reinitialisation")
        jeton = message.contexte["lien"].split("jeton=", 1)[1]
        reponse = client.post("/transverse/second-facteur/confirmation", json={"jeton": jeton})
        assert reponse.status_code == 410, reponse.text

    def test_le_lien_ne_sert_qu_une_fois(self, application):
        client = _session(application, REVISEUR[1])
        _enroler(client)
        jeton = self._jeton_envoye()
        premier = client.post("/transverse/second-facteur/confirmation", json={"jeton": jeton})
        assert premier.status_code == 200, premier.text
        second = client.post("/transverse/second-facteur/confirmation", json={"jeton": jeton})
        assert second.status_code == 410, second.text

    def test_le_lien_expire_apres_trente_minutes(self, application):
        from datetime import timedelta

        from app.partage.horloge import horloge_figee

        client = _session(application, REVISEUR[1])
        _enroler(client)
        jeton = self._jeton_envoye()
        with horloge_figee(maintenant() + timedelta(minutes=31)):
            # La session tient douze heures ; seul le lien est périmé.
            reponse = client.post("/transverse/second-facteur/confirmation", json={"jeton": jeton})
        assert reponse.status_code == 410, reponse.text

    def test_le_lien_d_enrolement_ne_redefinit_pas_le_mot_de_passe(self, application):
        """⚠️ `definir_mot_de_passe` ignorait le type du jeton : ce lien aurait donné le compte."""
        _enroler(_session(application, REVISEUR[1]))
        jeton = self._jeton_envoye()
        reponse = TestClient(application).post(
            "/transverse/mot-de-passe/definition",
            json={"secret": jeton, "mot_de_passe": "Un-nouveau-mot-de-passe-2026!"},
        )
        assert reponse.status_code == 410, reponse.text

    def test_le_cas_d_usage_refuse_par_defaut_un_premier_enrolement_sans_lien(self):
        from datetime import datetime

        from app.contextes.transverse.api import (
            PreuveDeBoiteRequise,
            enroler_second_facteur,
        )

        reinitialiser_atelier()
        boutique = atelier()
        with pytest.raises(PreuveDeBoiteRequise):
            enroler_second_facteur(
                boutique.comptes.lire(REVISEUR[0]),
                secret="JBSWY3DPEHPK3PXP",
                session_renforcee=False,
                comptes=boutique.comptes,
                journal=boutique.journal,
                a_l_instant=datetime(2026, 9, 14, 10, 0),
            )


class TestLeTitulaire:
    def test_le_premier_enrolement_confirme_previent_le_titulaire(self, application):
        enroler_par_le_courriel(_session(application, REVISEUR[1]))
        assert _courriels("compte.second_facteur_enrole") == [REVISEUR[1]]

    def test_qui_a_l_appareil_peut_le_remplacer(self, application):
        client = _session(application, REVISEUR[1])
        ancien = enroler_par_le_courriel(client)
        assert _renforcer(client, ancien).json()["facteur_fort"] is True

        remplacement = _enroler(client)
        assert remplacement.status_code == 200, remplacement.text
        nouveau = remplacement.json()["secret"]
        assert nouveau != ancien

        autre = _session(application, REVISEUR[1])
        assert _renforcer(autre, ancien).status_code == 401, "l'ancien appareil ne vaut plus"
        assert _renforcer(autre, nouveau).json()["facteur_fort"] is True


class TestLaReinitialisation:
    def _reinitialiser(self, client, identifiant=REVISEUR[0], motif=MOTIF):
        return client.post(
            f"/transverse/comptes/{identifiant}/second-facteur/reinitialisation",
            json={"motif": motif},
        )

    def test_l_administrateur_reinitialise_ferme_les_sessions_et_previent(self, application):
        titulaire = _session(application, REVISEUR[1])
        _renforcer(titulaire, enroler_par_le_courriel(titulaire))

        reponse = self._reinitialiser(_session(application, ADMINISTRATEUR[1]))
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["second_facteur_actif"] is False
        assert _courriels("compte.second_facteur_reinitialise") == [REVISEUR[1]]

        # ⚠️ La session renforcée ouverte sur l'appareil perdu ne sert plus à rien.
        assert titulaire.post("/transverse/second-facteur").status_code == 401

        # Et le titulaire, reconnecté, enrôle son nouvel appareil par le lien reçu.
        enroler_par_le_courriel(_session(application, REVISEUR[1]))

    def test_on_ne_reinitialise_pas_son_propre_second_facteur(self, application):
        admin = _session(application, ADMINISTRATEUR[1])
        enroler_par_le_courriel(admin)
        reponse = self._reinitialiser(admin, identifiant=ADMINISTRATEUR[0])
        assert reponse.status_code == 409, reponse.text
        assert "propre" in reponse.text

    def test_rien_a_reinitialiser(self, application):
        reponse = self._reinitialiser(_session(application, ADMINISTRATEUR[1]))
        assert reponse.status_code == 409, reponse.text

    def test_le_reviseur_ne_reinitialise_pas(self, application):
        enroler_par_le_courriel(_session(application, "l.fotso@cga-brcg.cm"))
        reponse = self._reinitialiser(_session(application, REVISEUR[1]), identifiant="C-004")
        assert reponse.status_code == 403, reponse.text

    def test_un_motif_court_est_refuse(self, application):
        titulaire = _session(application, REVISEUR[1])
        enroler_par_le_courriel(titulaire)
        reponse = self._reinitialiser(_session(application, ADMINISTRATEUR[1]), motif="perdu")
        assert reponse.status_code == 422, reponse.text


class TestSurPostgresql:
    """Le secret est scellé en base : la garde doit tenir contre le compte relu, pas en mémoire."""

    pytestmark = exige_postgresql

    def test_le_remplacement_sans_code_est_refuse_et_le_secret_tient(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR[1])
        secret = enroler_par_le_courriel(client)
        ouvrir_une_session(client, REVISEUR[1])  # une seconde session, non renforcée
        assert _enroler(client).status_code == 409
        assert _renforcer(client, secret).json()["facteur_fort"] is True

    def test_la_reinitialisation_persiste(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR[1])
        enroler_par_le_courriel(client)
        ouvrir_une_session(client, ADMINISTRATEUR[1])
        reponse = client.post(
            f"/transverse/comptes/{REVISEUR[0]}/second-facteur/reinitialisation",
            json={"motif": MOTIF},
        )
        assert reponse.status_code == 200, reponse.text
        ouvrir_une_session(client, REVISEUR[1])
        relu = _enroler(client)
        assert relu.status_code == 200, relu.text
        assert relu.json()["confirmation_par_courriel"] is True, "relu en base, sans facteur"
