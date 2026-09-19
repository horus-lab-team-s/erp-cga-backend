"""La vue risque d'un dossier et les décisions de la direction (pas 100).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **Le catalogue** : lu au référentiel, prudent sans fichier (aucune mesure), strict sur un
  fichier mal écrit (clé inconnue, code en double, niveau inconnu).
- **La prise de décision** : refusée hors des niveaux prévus, sans motif suffisant, sans
  échéance quand la mesure l'exige, avec une échéance déjà passée, ou en double d'une mesure
  en cours. Acceptée, elle garde l'instantané du score qui l'a fondée.
- **La clôture** : motivée, une seule fois ; la même mesure peut ensuite être reprise.
- **Les accès** : la vue risque à la direction ; décider exige `DECIDER_SUR_DOSSIER` ; la
  liste des décisions au cabinet qui porte le dossier, jamais à l'adhérent.
- **Le même score** que le tableau de bord : deux calculs finiraient par diverger.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.pilotage.adaptateurs.sortant.depots_decisions import DepotDecisionsMemoire
from app.contextes.pilotage.api import (
    CatalogueDesMesures,
    Composante,
    DecisionRefusee,
    MesureComposante,
    NiveauRisque,
    ScoreRisque,
    charger_le_catalogue_des_mesures,
    clore_une_decision,
    prendre_une_mesure,
    vider_les_decisions_de_direction,
)
from app.contextes.pilotage.domaine.decisions import DecisionIntrouvable
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

DIRECTION = "b.mballa@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
COMPTABLE_AGRO = "l.fotso@cga-brcg.cm"
COMPTABLE_NGUEMA = "c.ndongo@cga-brcg.cm"
ADHERENT_BATIMENT = "jp.nkoa@batimentplus.cm"
INSPECTEUR = "g.atangana@inspection.cm"
NGUEMA = "P027788990011M"
BATIMENT = "M081234567890P"
MOTIF = "Trois retards déclaratifs et une patente non déposée depuis janvier."
JOUR = date(2026, 9, 16)
INSTANT = datetime(2026, 9, 16, 10, 0)


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_decisions_de_direction()
    yield
    vider_les_decisions_de_direction()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


# ── Le catalogue ──────────────────────────────────────────────────────────────


def _catalogue() -> CatalogueDesMesures:
    return CatalogueDesMesures.model_validate(
        {
            "motif_minimum": 30,
            "source": "essai",
            "mesures": [
                {
                    "code": "RENFORCER_SUIVI",
                    "libelle": "Renforcer le suivi",
                    "description": "Revue hebdomadaire du dossier.",
                    "niveaux": ["MODERE", "ELEVE"],
                },
                {
                    "code": "EXIGER_REGULARISATION",
                    "libelle": "Exiger une régularisation datée",
                    "description": "Régulariser avant la date fixée.",
                    "niveaux": ["MODERE", "ELEVE"],
                    "echeance_requise": True,
                },
                {
                    "code": "ENVISAGER_FIN_ADHESION",
                    "libelle": "Envisager la fin d'adhésion",
                    "description": "Inscrit au prochain comité.",
                    "niveaux": ["ELEVE"],
                },
            ],
        }
    )


class TestLeCatalogue:
    def test_le_catalogue_du_referentiel_se_charge(self):
        catalogue = charger_le_catalogue_des_mesures(configuration().dossier_referentiel)
        assert catalogue.source == "pilotage/mesures.yaml"
        assert [m.code for m in catalogue.mesures] == [
            "RENFORCER_SUIVI",
            "EXIGER_REGULARISATION",
            "CONVOQUER_CADRAGE",
            "ENVISAGER_FIN_ADHESION",
        ]

    def test_sans_fichier_aucune_mesure_n_est_proposee(self, tmp_path: Path):
        catalogue = charger_le_catalogue_des_mesures(tmp_path)
        assert catalogue.mesures == ()
        assert catalogue.proposees_pour(NiveauRisque.ELEVE) == ()
        assert "aucun catalogue" in catalogue.source

    def test_une_cle_mal_ecrite_fait_echouer_le_chargement(self, tmp_path: Path):
        (tmp_path / "pilotage").mkdir()
        (tmp_path / "pilotage" / "mesures.yaml").write_text(
            "mesures:\n  - code: EXIGER_REGULARISATION\n    libelle: Exiger\n"
            "    description: Régulariser avant la date.\n    niveaux: [ELEVE]\n"
            "    echeance_requis: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            charger_le_catalogue_des_mesures(tmp_path)

    def test_deux_mesures_de_meme_code_sont_refusees(self):
        mesure = _catalogue().mesures[0].model_dump()
        with pytest.raises(ValidationError, match="en double"):
            CatalogueDesMesures.model_validate({"mesures": [mesure, mesure]})

    def test_les_mesures_proposees_suivent_le_niveau_et_l_ordre_du_fichier(self):
        catalogue = _catalogue()
        assert [m.code for m in catalogue.proposees_pour(NiveauRisque.MODERE)] == [
            "RENFORCER_SUIVI",
            "EXIGER_REGULARISATION",
        ]
        assert catalogue.proposees_pour(NiveauRisque.FAIBLE) == ()
        assert len(catalogue.proposees_pour(NiveauRisque.ELEVE)) == 3


# ── La prise de décision, sans base ni route ──────────────────────────────────


def _score(anomalies: int) -> ScoreRisque:
    """Un score de 25 points par anomalie : 0 est faible, 1 modéré (≥ 25), 3 élevé (≥ 60)."""
    return ScoreRisque(
        entreprise=NGUEMA,
        denomination="CABINET NGUEMA CONSEIL",
        mesures=(
            MesureComposante(
                composante=Composante.ANOMALIES_BLOQUANTES,
                libelle="anomalies",
                occurrences=anomalies,
                poids=Decimal(25),
                elements=tuple(f"PJ-{i}" for i in range(anomalies)),
                action="Lever.",
            ),
        ),
    )


@pytest.fixture
def cabinet():
    """Le dépôt en mémoire est rangé par locataire : il faut en établir un, comme le fait
    l'intergiciel d'unité de travail pour une requête."""
    with etabli("CGA-BRCG"):
        yield


def _prendre(depot, *, anomalies=3, code="RENFORCER_SUIVI", motif=MOTIF, echeance=None):
    return prendre_une_mesure(
        score=_score(anomalies),
        a_la_date=JOUR,
        catalogue=_catalogue(),
        code=code,
        motif=motif,
        echeance=echeance,
        par="C-001",
        par_nom="Bernadette MBALLA",
        le=INSTANT,
        depot=depot,
    )


@pytest.mark.usefixtures("cabinet")
class TestLaPriseDeDecision:
    def test_une_decision_garde_l_instantane_du_score(self):
        depot = DepotDecisionsMemoire()
        decision = _prendre(depot)
        assert decision.identifiant == f"{NGUEMA}:RENFORCER_SUIVI:1"
        assert decision.score.total == Decimal(75)
        assert decision.score.niveau is NiveauRisque.ELEVE
        assert decision.score.occurrences == {"ANOMALIES_BLOQUANTES": 3}
        assert decision.libelle == "Renforcer le suivi"
        assert decision.en_cours
        assert depot.du_dossier(NGUEMA) == [decision]

    def test_une_mesure_est_refusee_hors_de_ses_niveaux(self):
        depot = DepotDecisionsMemoire()
        with pytest.raises(DecisionRefusee, match="à traiter ; ce dossier est à surveiller"):
            _prendre(depot, anomalies=1, code="ENVISAGER_FIN_ADHESION")
        with pytest.raises(DecisionRefusee, match="sous contrôle"):
            _prendre(depot, anomalies=0)
        assert depot.du_dossier(NGUEMA) == []

    def test_la_mesure_permise_au_niveau_modere_passe(self):
        assert _prendre(DepotDecisionsMemoire(), anomalies=1).score.niveau is NiveauRisque.MODERE

    def test_une_mesure_inconnue_est_refusee(self):
        with pytest.raises(DecisionRefusee, match="n'existe pas au catalogue"):
            _prendre(DepotDecisionsMemoire(), code="SUSPENDRE_TOUT")

    def test_le_motif_est_mesure_sans_ses_espaces(self):
        with pytest.raises(DecisionRefusee, match="au moins 30"):
            _prendre(DepotDecisionsMemoire(), motif="   " + "x" * 29 + "   ")
        assert _prendre(DepotDecisionsMemoire(), motif="  " + "x" * 30 + " ").motif == "x" * 30

    def test_l_echeance_est_exigee_quand_la_mesure_le_dit(self):
        with pytest.raises(DecisionRefusee, match="avec une échéance"):
            _prendre(DepotDecisionsMemoire(), code="EXIGER_REGULARISATION")

    def test_une_echeance_du_jour_ou_passee_est_refusee(self):
        for echeance in (JOUR, date(2026, 9, 1)):
            with pytest.raises(DecisionRefusee, match="pas postérieure"):
                _prendre(DepotDecisionsMemoire(), code="EXIGER_REGULARISATION", echeance=echeance)
        demain = date(2026, 9, 17)
        decision = _prendre(DepotDecisionsMemoire(), code="EXIGER_REGULARISATION", echeance=demain)
        assert decision.echeance == demain

    def test_une_mesure_deja_en_cours_est_refusee(self):
        depot = DepotDecisionsMemoire()
        _prendre(depot)
        with pytest.raises(DecisionRefusee, match="déjà en cours"):
            _prendre(depot)
        # Une autre mesure, elle, reste possible.
        _prendre(depot, code="ENVISAGER_FIN_ADHESION")
        assert len(depot.du_dossier(NGUEMA)) == 2

    def test_un_autre_cabinet_ne_voit_pas_la_decision(self):
        depot = DepotDecisionsMemoire()
        _prendre(depot)
        with etabli("AUTRE-CABINET"):
            assert depot.du_dossier(NGUEMA) == []
            # Et il peut décider la même mesure sans être tenu par la première.
            assert _prendre(depot).identifiant == f"{NGUEMA}:RENFORCER_SUIVI:1"
        assert len(depot.du_dossier(NGUEMA)) == 1

    def test_une_mesure_echue_se_voit(self):
        decision = _prendre(
            DepotDecisionsMemoire(), code="EXIGER_REGULARISATION", echeance=date(2026, 9, 30)
        )
        assert not decision.echue(date(2026, 9, 30))
        assert decision.echue(date(2026, 10, 1))
        close = decision.clore(
            par="C-001", par_nom="B. MBALLA", le=INSTANT, motif=MOTIF, motif_minimum=30
        )
        assert not close.echue(date(2026, 10, 1))


@pytest.mark.usefixtures("cabinet")
class TestLaCloture:
    def test_clore_puis_reprendre_la_meme_mesure_fait_une_nouvelle_ligne(self):
        depot = DepotDecisionsMemoire()
        premiere = _prendre(depot)
        close = clore_une_decision(
            dossier=NGUEMA,
            identifiant=premiere.identifiant,
            motif="Le dirigeant a remis les pièces en souffrance.",
            par="C-001",
            par_nom="Bernadette MBALLA",
            le=INSTANT,
            catalogue=_catalogue(),
            depot=depot,
        )
        assert not close.en_cours
        assert close.motif_de_cloture == "Le dirigeant a remis les pièces en souffrance."
        seconde = _prendre(depot)
        assert seconde.identifiant == f"{NGUEMA}:RENFORCER_SUIVI:2"
        assert [d.en_cours for d in depot.du_dossier(NGUEMA)] == [False, True]

    def test_on_ne_clot_pas_deux_fois_ni_sans_motif(self):
        depot = DepotDecisionsMemoire()
        decision = _prendre(depot)
        arguments = dict(
            dossier=NGUEMA,
            identifiant=decision.identifiant,
            par="C-001",
            par_nom="B. MBALLA",
            le=INSTANT,
            catalogue=_catalogue(),
            depot=depot,
        )
        with pytest.raises(DecisionRefusee, match="au moins 30"):
            clore_une_decision(motif="fait", **arguments)
        clore_une_decision(motif=MOTIF, **arguments)
        with pytest.raises(DecisionRefusee, match="déjà close"):
            clore_une_decision(motif=MOTIF, **arguments)

    def test_une_decision_inconnue_ou_d_un_autre_dossier_est_introuvable(self):
        depot = DepotDecisionsMemoire()
        decision = _prendre(depot)
        with pytest.raises(DecisionIntrouvable):
            clore_une_decision(
                dossier=BATIMENT,
                identifiant=decision.identifiant,
                motif=MOTIF,
                par="C-001",
                par_nom="B. MBALLA",
                le=INSTANT,
                catalogue=_catalogue(),
                depot=depot,
            )


# ── Les routes ────────────────────────────────────────────────────────────────


def _decider(client: TestClient, niu: str = NGUEMA, **corps):
    return client.post(
        f"/pilotage/dossiers/{niu}/decisions",
        json={"mesure": "RENFORCER_SUIVI", "motif": MOTIF, **corps},
    )


class TestLesRoutes:
    def test_la_vue_risque_rend_le_score_du_tableau_de_bord(self):
        direction = _client(DIRECTION)
        tableau = direction.get("/pilotage/tableau-de-bord?a_la_date=2026-08-17").json()
        vue = direction.get(
            f"/pilotage/dossiers/{BATIMENT}/risque?a_la_date=2026-08-17&exercice=2026"
        ).json()
        ligne = next(r for r in tableau["risques"] if r["entreprise"] == BATIMENT)
        assert Decimal(vue["total"]) == Decimal(ligne["total"])
        assert vue["niveau"] == ligne["niveau"]
        # Toutes les composantes, y compris celles à zéro, de la plus lourde à la moins lourde.
        assert len(vue["composantes"]) == len(Composante)
        contributions = [Decimal(c["poids"]) * c["occurrences"] for c in vue["composantes"]]
        assert contributions == sorted(contributions, reverse=True)
        assert vue["source_du_catalogue"] == "pilotage/mesures.yaml"
        assert vue["mesures_proposees"]

    def test_un_dossier_inconnu_rend_404(self):
        assert _client(DIRECTION).get("/pilotage/dossiers/M000000000000X/risque").status_code == 404

    def test_decider_puis_relire_puis_clore(self):
        direction = _client(DIRECTION)
        reponse = _decider(direction)
        assert reponse.status_code == 200, reponse.text
        decision = reponse.json()["decision"]
        assert decision["prise_par_nom"] == "Bernadette MBALLA"
        vue = direction.get(f"/pilotage/dossiers/{NGUEMA}/risque").json()
        assert [d["decision"]["identifiant"] for d in vue["decisions"]] == [decision["identifiant"]]
        assert _decider(direction).status_code == 422
        close = direction.post(
            f"/pilotage/dossiers/{NGUEMA}/decisions/{decision['identifiant']}/cloture",
            json={"motif": "Le dirigeant a remis les pièces en souffrance."},
        )
        assert close.status_code == 200, close.text
        assert close.json()["decision"]["statut"] == "CLOSE"
        inconnue = direction.post(
            f"/pilotage/dossiers/{NGUEMA}/decisions/{NGUEMA}:INCONNUE:1/cloture",
            json={"motif": MOTIF},
        )
        assert inconnue.status_code == 404

    def test_les_decisions_sont_rendues_de_la_plus_recente_a_la_plus_ancienne(self):
        direction = _client(DIRECTION)
        _decider(direction)
        _decider(direction, mesure="CONVOQUER_CADRAGE")
        decisions = direction.get(f"/pilotage/dossiers/{NGUEMA}/decisions").json()
        assert [d["decision"]["mesure"] for d in decisions] == [
            "CONVOQUER_CADRAGE",
            "RENFORCER_SUIVI",
        ]

    def test_la_decision_est_au_journal_avec_son_motif_hors_des_donnees(self):
        direction = _client(DIRECTION)
        _decider(direction)
        journal = _client(REVISEUR).get("/transverse/audit?objet_type=decision_de_direction")
        assert journal.status_code == 200, journal.text
        entree = next(e for e in journal.json() if e["action"] == "pilotage.decision_prise")
        assert entree["motif"] == MOTIF
        assert MOTIF not in str(entree["apres"])

    def test_le_reviseur_ne_lit_pas_la_vue_risque_et_ne_decide_pas(self):
        reviseur = _client(REVISEUR)
        assert reviseur.get(f"/pilotage/dossiers/{NGUEMA}/risque").status_code == 403
        assert _decider(reviseur).status_code == 403

    def test_le_collaborateur_du_dossier_lit_les_decisions_et_est_notifie(self):
        _decider(_client(DIRECTION))
        comptable = _client(COMPTABLE_NGUEMA)
        decisions = comptable.get(f"/pilotage/dossiers/{NGUEMA}/decisions")
        assert decisions.status_code == 200
        assert decisions.json()[0]["decision"]["mesure"] == "RENFORCER_SUIVI"
        titres = [
            n["titre"] for n in comptable.get("/transverse/notifications").json()["notifications"]
        ]
        assert "Mesure de direction : Renforcer le suivi du dossier" in titres

    def test_hors_perimetre_les_decisions_n_existent_pas(self):
        _decider(_client(DIRECTION))
        comptable_agro = _client(COMPTABLE_AGRO)
        assert comptable_agro.get(f"/pilotage/dossiers/{NGUEMA}/decisions").status_code == 404
        titres = [
            n["titre"]
            for n in comptable_agro.get("/transverse/notifications").json()["notifications"]
        ]
        assert not any(t.startswith("Mesure de direction") for t in titres)

    def test_l_adherent_ne_lit_jamais_les_decisions_de_son_dossier(self):
        _decider(_client(DIRECTION), niu=BATIMENT)
        adherent = _client(ADHERENT_BATIMENT)
        assert adherent.get(f"/pilotage/dossiers/{BATIMENT}/decisions").status_code == 404
        assert adherent.get(f"/pilotage/dossiers/{BATIMENT}/risque").status_code == 403

    def test_l_inspecteur_ne_lit_pas_les_decisions_meme_dans_son_perimetre(self):
        agro = "M065544332211L"
        _decider(_client(DIRECTION), niu=agro)
        assert _client(INSPECTEUR).get(f"/pilotage/dossiers/{agro}/decisions").status_code == 404


# ── Sur PostgreSQL : la décision survit à la requête ─────────────────────────


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_decider_relire_et_clore_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, DIRECTION)
        prise = _decider(client, mesure="EXIGER_REGULARISATION", echeance="2099-01-31")
        assert prise.status_code == 200, prise.text
        identifiant = prise.json()["decision"]["identifiant"]
        vue = client.get(f"/pilotage/dossiers/{NGUEMA}/risque").json()
        assert vue["decisions"][0]["decision"]["echeance"] == "2099-01-31"
        close = client.post(
            f"/pilotage/dossiers/{NGUEMA}/decisions/{identifiant}/cloture",
            json={"motif": "Le dirigeant a remis les pièces en souffrance."},
        )
        assert close.status_code == 200, close.text
        ouvrir_une_session(client, COMPTABLE_NGUEMA)
        relues = client.get(f"/pilotage/dossiers/{NGUEMA}/decisions").json()
        assert [d["decision"]["statut"] for d in relues] == ["CLOSE"]
        assert relues[0]["decision"]["score"]["niveau"] in {"MODERE", "ELEVE"}
