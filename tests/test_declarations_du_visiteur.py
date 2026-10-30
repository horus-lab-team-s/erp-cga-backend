"""Ce que le client a écrit sur le site n'est pas redemandé au téléphone.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROTÈGE

Le cabinet, le 14 octobre 2026 : « il ne faut pas qu'une information soit saisie
deux fois ». Le visiteur donnait son nom et un message libre ; le responsable lui
redemandait ensuite sa forme juridique, son capital et son siège pour les taper
dans la qualification.

Le site pose désormais les questions que le référentiel lui désigne, et leurs
réponses ouvrent la qualification déjà remplie.

⚠️ TROIS PROPRIÉTÉS, ET LES TROIS SONT DES REFUS.

Ce qui entre par une route publique ne doit ni remplir des champs réservés au
responsable, ni faire échouer un dépôt, ni écraser ce qu'un humain a saisi. Les
cas ci-dessous sont éprouvés dans les deux sens : en retirant la liste blanche,
la capture du refus et la garde d'écrasement de `preremplir`, un à un, chaque
mutant fait rougir son cas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    recharger_le_referentiel,
    reinitialiser_dossiers,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.qualification import (
    SourceReponse,
    ouvrir_une_qualification,
    preremplir,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.infrastructure.config import configuration
from app.main import app

T0 = datetime(2026, 10, 14, 9, 0)
QUALIFICATEUR = "b.mballa@cga-brcg.cm"


@pytest.fixture
def questionnaire():
    dossier = Path(configuration().dossier_referentiel) / "qualification"
    return RepertoireDeQuestionnaires.depuis(dossier).prendre("CREATION")


class TestLePreremplissage:
    def test_les_declarations_deviennent_des_reponses_typees(self, questionnaire):
        qualification, ecartees = preremplir(
            ouvrir_une_qualification("dos-1", questionnaire),
            questionnaire,
            {
                "forme_juridique": "SARL",
                "associes": "3",
                "apports_en_nature": "non",
                "pieces_deja_reunies": "CNI des associés, plan de localisation",
            },
            T0,
        )

        assert ecartees == ()
        assert qualification.faits() == {
            "forme_juridique": "SARL",
            "associes": 3,
            "apports_en_nature": False,
            "pieces_deja_reunies": ("CNI des associés", "plan de localisation"),
        }
        assert {r.source for r in qualification.reponses} == {SourceReponse.DECLAREE}

    def test_une_question_non_posee_au_visiteur_est_ecartee(self, questionnaire):
        """⚠️ LA LISTE BLANCHE. `pages_statuts` est au questionnaire, et le site
        ne la pose pas : la recevoir de l'extérieur laisserait n'importe qui
        remplir les champs que seul le responsable doit tenir."""
        assert questionnaire.question("pages_statuts") is not None
        assert "pages_statuts" not in {q.code for q in questionnaire.pour_le_visiteur}

        qualification, ecartees = preremplir(
            ouvrir_une_qualification("dos-1", questionnaire),
            questionnaire,
            {"pages_statuts": "12", "champ_invente": "x"},
            T0,
        )

        assert qualification.reponses == ()
        assert set(ecartees) == {"pages_statuts", "champ_invente"}

    def test_une_reponse_inexploitable_est_ecartee_sans_lever(self, questionnaire):
        qualification, ecartees = preremplir(
            ouvrir_une_qualification("dos-1", questionnaire),
            questionnaire,
            {"capital_social": "je ne sais pas encore", "associes": "2"},
            T0,
        )

        assert ecartees == ("capital_social",)
        assert qualification.faits() == {"associes": 2}

    def test_ce_que_le_responsable_a_saisi_n_est_pas_ecrase(self, questionnaire):
        """Le responsable a entendu le client ; le formulaire, non."""
        deja = ouvrir_une_qualification("dos-1", questionnaire).repondre(
            questionnaire, "associes", 5, T0, source=SourceReponse.CONSTATEE
        )

        qualification, _ = preremplir(
            deja, questionnaire, {"associes": "2", "siege": "Akwa, Douala"}, T0
        )

        assert qualification.reponse("associes").valeur == 5
        assert qualification.reponse("associes").source is SourceReponse.CONSTATEE
        assert qualification.reponse("siege").valeur == "Akwa, Douala"


class TestLaDemandeBorneCeQuElleRecoit:
    def _demande(self, declarations):
        return DemandeDeContact(
            identifiant="dc-1",
            deposee_le=T0,
            nom="Abena Ndzana",
            telephone="699112233",
            service_souhaite="CREATION",
            canal_prefere=Canal.APPEL,
            consentement=Consentement(
                accorde=False, recueilli_le=T0, version_du_texte="v1"
            ),
            declarations=declarations,
        )

    def test_les_reponses_vides_ne_sont_pas_conservees(self):
        demande = self._demande({"associes": "  ", "siege": " Akwa "})
        assert demande.declarations == {"siege": "Akwa"}

    def test_une_demande_sans_declaration_reste_valide(self):
        assert self._demande({}).declarations == {}

    def test_trop_de_reponses_est_refuse(self):
        with pytest.raises(ValueError, match="trop de réponses"):
            self._demande({f"q{i}": "x" for i in range(41)})

    def test_une_reponse_demesuree_est_refusee(self):
        with pytest.raises(ValueError, match="trop longue"):
            self._demande({"activite": "x" * 301})


class TestParLaRoutePublique:
    @pytest.fixture(autouse=True)
    def parcours_neuf(self):
        reinitialiser_dossiers()
        recharger_le_referentiel()
        yield
        reinitialiser_dossiers()
        recharger_le_referentiel()

    @pytest.fixture
    def client(self):
        with TestClient(app) as client:
            yield client

    def _deposer(self, client, **declarations):
        return client.post(
            "/acquisition/demandes",
            json={
                "nom": "Abena Ndzana",
                "telephone": "699112233",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
                "declarations": declarations,
            },
        )

    def _qualificateur(self, client):
        session = client.post(
            "/transverse/session",
            json={"courriel": QUALIFICATEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert session.status_code == 200, session.text
        return client

    def test_le_site_lit_ses_questions_sans_session(self, client):
        reponse = client.get("/acquisition/questionnaires/CREATION/visiteur")

        assert reponse.status_code == 200, reponse.text
        questions = {q["code"]: q for q in reponse.json()["questions"]}
        assert "forme_juridique" in questions and "pages_statuts" not in questions
        # ⚠️ Rien de ce qui sert au juriste ne sort par une route publique.
        assert set(questions["forme_juridique"]) == {
            "code", "libelle", "type", "valeurs", "unite", "aide",
        }
        assert {"valeur": "SARL", "libelle": "SARL (plusieurs associés)"} in (
            questions["forme_juridique"]["valeurs"]
        )

    def test_un_service_sans_questionnaire_rend_une_liste_vide(self, client):
        reponse = client.get("/acquisition/questionnaires/INCONNU/visiteur")
        assert reponse.status_code == 200
        assert reponse.json()["questions"] == []

    def test_la_qualification_s_ouvre_deja_remplie(self, client):
        depot = self._deposer(
            client, forme_juridique="SARL", associes="3", siege="Bonapriso, Douala"
        )
        assert depot.status_code == 201, depot.text

        responsable = self._qualificateur(client)
        dossier = responsable.get("/acquisition/dossiers").json()[0]
        etat = responsable.get(
            f"/acquisition/dossiers/{dossier['reference']}/qualification"
        ).json()

        assert etat["commencee"] is True
        assert etat["faits"] == {
            "forme_juridique": "SARL",
            "associes": "3",
            "siege": "Bonapriso, Douala",
        }
        assert etat["avancement"]["repondues"] == 3
        # ⚠️ Le formulaire n'est pas un échange : le dossier n'a pas avancé.
        assert dossier["etat"] in ("DEPOSEE", "AFFECTEE"), dossier["etat"]

    def test_une_reponse_inexploitable_n_empeche_pas_le_depot(self, client):
        depot = self._deposer(client, capital_social="à voir", associes="2")
        assert depot.status_code == 201, depot.text

        responsable = self._qualificateur(client)
        dossier = responsable.get("/acquisition/dossiers").json()[0]
        etat = responsable.get(
            f"/acquisition/dossiers/{dossier['reference']}/qualification"
        ).json()
        assert etat["faits"] == {"associes": "2"}

    def test_sans_declaration_rien_ne_s_ouvre(self, client):
        assert self._deposer(client).status_code == 201

        responsable = self._qualificateur(client)
        dossier = responsable.get("/acquisition/dossiers").json()[0]
        etat = responsable.get(
            f"/acquisition/dossiers/{dossier['reference']}/qualification"
        ).json()
        assert etat["commencee"] is False
