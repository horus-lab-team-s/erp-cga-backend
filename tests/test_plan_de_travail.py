"""Le plan de travail d'un collaborateur (pas 104).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **L'ordre** : par échéance, puis par gravité selon l'ordre du référentiel ; les tâches
   sans échéance en dernier.
2. **Chaque nature de tâche** et son échéance : pièces (plus ancienne réception + délai),
   déclarations (en retard regroupées, à venir dans l'horizon), relevé à rapprocher et mois à
   transmettre (jour du mois courant), remarques à reprendre (aujourd'hui), relances.
3. **La priorité** selon les seuils ; les réglages incohérents refusés.
4. **La route** : restreinte au périmètre, fermée à qui ne saisit pas ; les passages de relais
   comptés à partir des revues ; une tâche qui disparaît quand l'état change.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import vider_les_revues
from app.contextes.comptabilite.api import vider_les_ecritures_en_memoire
from app.contextes.pilotage.adaptateurs.sortant.plan_de_travail_yaml import (
    charger_les_reglages_du_plan,
)
from app.contextes.pilotage.domaine.plan_de_travail import (
    DeclarationAPreparer,
    EtatDuDossierPourLePlan,
    NatureDeTache,
    PrioriteDeTache,
    ReglagesDuPlan,
    planifier,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application

JOUR = date(2026, 8, 8)
REGLAGES = ReglagesDuPlan()
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
CHARGEE_CLIENTELE = "p.moukouri@cga-brcg.cm"
BATIMENT = "M081234567890P"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_revues()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_revues()
    vider_les_ecritures_en_memoire()


def _etat(**autres) -> EtatDuDossierPourLePlan:
    return EtatDuDossierPourLePlan(niu="N1", denomination="DOSSIER UN", **autres)


def _declaration(code, jour_echeance, mois=8):
    return DeclarationAPreparer(
        code=code,
        libelle=f"Déclaration {code}",
        periode_debut=date(2026, mois - 1, 1),
        echeance=date(2026, mois, jour_echeance),
    )


class TestLesTaches:
    def test_les_pieces_ont_pour_echeance_la_plus_ancienne_reception_plus_le_delai(self):
        [tache] = planifier(
            [_etat(pieces_a_traiter=(("PJ-2", date(2026, 8, 6)), ("PJ-1", date(2026, 8, 1))))],
            REGLAGES,
            JOUR,
        )
        assert (tache.nature, tache.volume, tache.echeance) == (
            NatureDeTache.TRAITER_PIECES,
            "2 pièces",
            date(2026, 8, 6),
        )
        assert tache.elements == ("PJ-2", "PJ-1")
        assert tache.priorite is PrioriteDeTache.URGENTE and tache.en_retard

    def test_les_declarations_en_retard_sont_regroupees_et_celles_a_venir_filtrees_par_l_horizon(
        self,
    ):
        etat = _etat(
            declarations=(
                _declaration("TVA", 15, mois=3),
                _declaration("CNPS", 15, mois=2),
                _declaration("IRPP", 15),  # dans 7 jours : dans l'horizon
                _declaration("PATENTE", 28, mois=9),  # hors de l'horizon de 15 jours
                _declaration("DSF", 8),  # aujourd'hui : à venir, pas en retard
            )
        )
        taches = planifier([etat], REGLAGES, JOUR)
        assert [(t.libelle, t.volume, t.echeance) for t in taches] == [
            ("Régulariser les déclarations en retard", "2 déclarations", date(2026, 2, 15)),
            ("Préparer : Déclaration DSF", "juillet 2026", date(2026, 8, 8)),
            ("Préparer : Déclaration IRPP", "juillet 2026", date(2026, 8, 15)),
        ]
        assert taches[0].elements == ("CNPS 01/2026", "TVA 02/2026")
        assert not taches[1].en_retard and taches[1].priorite is PrioriteDeTache.URGENTE
        assert taches[2].priorite is PrioriteDeTache.ELEVEE

    def test_releve_mois_remarques_et_relances(self):
        etat = _etat(
            releves_a_rapprocher=("BQ",),
            mois_precedent_a_transmettre=True,
            revue_renvoyee=("REV-20260701-20260731", 2),
            demandes_echues=("DP-1",),
        )
        taches = {t.nature: t for t in planifier([etat], REGLAGES, JOUR)}
        assert (
            taches[NatureDeTache.RAPPROCHER_RELEVE].echeance,
            taches[NatureDeTache.RAPPROCHER_RELEVE].volume,
        ) == (
            date(2026, 8, 10),
            "juillet",
        )
        assert taches[NatureDeTache.TRANSMETTRE_MOIS].echeance == date(2026, 8, 20)
        remarques = taches[NatureDeTache.REPRENDRE_REMARQUES]
        assert (remarques.echeance, remarques.volume) == (JOUR, "2 remarques")
        assert remarques.lien == "/comptabilite/revues/REV-20260701-20260731?dossier=N1"
        relance = taches[NatureDeTache.RELANCER_PIECES]
        assert relance.echeance is None and relance.priorite is PrioriteDeTache.NORMALE

    def test_l_ordre_par_echeance_puis_gravite_puis_sans_echeance(self):
        a = EtatDuDossierPourLePlan(
            niu="A", denomination="A", releves_a_rapprocher=("BQ",), demandes_echues=("DP",)
        )
        b = EtatDuDossierPourLePlan(
            niu="B",
            denomination="B",
            pieces_a_traiter=(("PJ", date(2026, 8, 5)),),
        )
        natures = [t.nature for t in planifier([a, b], REGLAGES, JOUR)]
        # Rapprocher (10/08) et traiter (10/08) tombent le même jour : l'ordre de gravité
        # du référentiel place les pièces devant.
        assert natures == [
            NatureDeTache.TRAITER_PIECES,
            NatureDeTache.RAPPROCHER_RELEVE,
            NatureDeTache.RELANCER_PIECES,
        ]
        inverse = ReglagesDuPlan(ordre_de_gravite=tuple(reversed(REGLAGES.ordre_de_gravite)))
        assert [t.nature for t in planifier([a, b], inverse, JOUR)][:2] == [
            NatureDeTache.RAPPROCHER_RELEVE,
            NatureDeTache.TRAITER_PIECES,
        ]

    def test_le_jour_limite_tient_dans_tout_mois(self):
        reglages = ReglagesDuPlan(rapprochement_avant_le_jour=28, transmission_avant_le_jour=28)
        [tache] = planifier([_etat(releves_a_rapprocher=("BQ",))], reglages, date(2026, 2, 3))
        assert tache.echeance == date(2026, 2, 28)
        # Au-delà du 28, février ne l'aurait pas : le réglage le refuse au chargement.
        with pytest.raises(ValidationError):
            ReglagesDuPlan(rapprochement_avant_le_jour=29)

    def test_les_seuils_de_priorite(self):
        reglages = ReglagesDuPlan(urgente_sous_jours=1, elevee_sous_jours=5)

        def priorite(jour_reception):
            [t] = planifier([_etat(pieces_a_traiter=(("PJ", jour_reception),))], reglages, JOUR)
            return t.priorite

        # échéance = réception + 5 jours
        assert priorite(date(2026, 8, 4)) is PrioriteDeTache.URGENTE  # échéance 09/08, 1 jour
        assert priorite(date(2026, 8, 5)) is PrioriteDeTache.ELEVEE  # 2 jours
        assert priorite(date(2026, 8, 8)) is PrioriteDeTache.ELEVEE  # 5 jours
        assert priorite(date(2026, 8, 9)) is PrioriteDeTache.NORMALE  # 6 jours


class TestLesReglages:
    def test_le_referentiel_et_ses_refus(self, tmp_path: Path):
        assert charger_les_reglages_du_plan(configuration().dossier_referentiel).source == (
            "pilotage/plan_de_travail.yaml"
        )
        assert charger_les_reglages_du_plan(tmp_path) == ReglagesDuPlan()
        with pytest.raises(ValidationError, match="urgente_sous_jours dépasse"):
            ReglagesDuPlan(urgente_sous_jours=8, elevee_sous_jours=7)
        with pytest.raises(ValidationError, match="exactement une fois"):
            ReglagesDuPlan(ordre_de_gravite=(NatureDeTache.TRAITER_PIECES,))
        with pytest.raises(ValidationError):
            ReglagesDuPlan.model_validate({"delai_de_traitement_des_piece_jours": 3})


# ── La route ──────────────────────────────────────────────────────────────────


def _client(courriel):
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


class TestLAgregateur:
    def test_une_erreur_de_programmation_n_est_pas_un_contexte_indisponible(self):
        """⚠️ Pas 104 : l'agrégateur avalait tout, y compris une méthode qui n'existe pas. La
        composante des demandes sans réponse a valu zéro sans que personne ne le voie."""
        from app.contextes.pilotage.adaptateurs.entrant.routes_http import _sans_echouer

        def indisponible():
            raise ConnectionError("base injoignable")

        assert _sans_echouer(indisponible, ["repli"]) == ["repli"]
        with pytest.raises(AttributeError):
            _sans_echouer(lambda: object().du_dossier("N1"), [])  # type: ignore[attr-defined]
        with pytest.raises(TypeError):
            _sans_echouer(lambda: len(1), [])  # type: ignore[arg-type]


class TestLaRoute:
    def test_le_plan_du_comptable_ne_porte_que_sur_ses_dossiers(self):
        plan = _client(COMPTABLE).get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()
        assert {d["niu"] for d in plan["mes_dossiers"]} == {
            "M081234567890P",
            "M071122334455J",
            "M065544332211L",
        }
        assert {t["dossier"] for t in plan["taches"]} <= {d["niu"] for d in plan["mes_dossiers"]}
        echeances = [t["echeance"] for t in plan["taches"] if t["echeance"]]
        assert echeances == sorted(echeances)
        natures = {t["nature"] for t in plan["taches"]}
        assert {"PREPARER_DECLARATION", "TRAITER_PIECES", "TRANSMETTRE_MOIS"} <= natures
        autre = (
            _client(COMPTABLE_AUTRE).get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()
        )
        assert {d["niu"] for d in autre["mes_dossiers"]} == {"P019876543210K", "P027788990011M"}

    def test_les_relances_apparaissent_une_fois_les_demandes_echues(self):
        client = _client(COMPTABLE)
        avant = client.get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()
        apres = client.get("/pilotage/plan-de-travail?a_la_date=2026-08-17").json()

        def relances(plan):
            return {
                t["dossier"]: t["elements"]
                for t in plan["taches"]
                if t["nature"] == "RELANCER_PIECES"
            }

        assert BATIMENT not in relances(avant)
        assert sorted(relances(apres)[BATIMENT]) == ["DP-2026-002", "DP-2026-009"]

    def test_transmettre_fait_disparaitre_la_tache_et_compte_le_passage_de_relais(
        self, monkeypatch
    ):
        # ⚠️ Pas 107 : juillet a des pièces à comptabiliser, et la clôture mensuelle refuse de le
        # transmettre. Ce test garde la tâche du plan, pas la clôture : les points restent
        # calculés, seul leur caractère bloquant est levé (sauf les brouillons, qui bloquent
        # toujours). La clôture est gardée par `test_cloture_mensuelle.py`.
        from app.contextes.comptabilite.adaptateurs.entrant import routes_cloture_mensuelle
        from app.contextes.comptabilite.domaine.cloture_mensuelle import (
            CodePoint,
            ReglagesDeLaClotureMensuelle,
        )

        informatifs = ReglagesDeLaClotureMensuelle(
            points={code.value: {"bloquant": code is CodePoint.BROUILLONS} for code in CodePoint}
        )
        monkeypatch.setattr(routes_cloture_mensuelle, "_reglages", lambda: informatifs)
        comptable = _client(COMPTABLE)

        def plan():
            return comptable.get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()

        avant = plan()
        assert any(
            t["nature"] == "TRANSMETTRE_MOIS" and t["dossier"] == BATIMENT for t in avant["taches"]
        )
        assert (
            comptable.post(
                f"/comptabilite/dossiers/{BATIMENT}/revues",
                json={"du": "2026-07-01", "au": "2026-07-31"},
            ).status_code
            == 201
        )
        apres = plan()
        assert not any(
            t["nature"] == "TRANSMETTRE_MOIS" and t["dossier"] == BATIMENT for t in apres["taches"]
        )
        assert apres["chez_le_reviseur"] == 1
        ligne = next(d for d in apres["mes_dossiers"] if d["niu"] == BATIMENT)
        assert ligne["revue_du_mois_precedent"] == "TRANSMISE"
        # Le réviseur renvoie avec une remarque : la tâche « reprendre » apparaît, datée du jour.
        reviseur = _client(REVISEUR)
        reviseur.post(
            f"/comptabilite/dossiers/{BATIMENT}/revues/REV-20260701-20260731/remarques",
            json={
                "nature": "COMPTE",
                "reference": "401",
                "texte": "Compte fournisseur à justifier.",
            },
        )
        reviseur.post(
            f"/comptabilite/dossiers/{BATIMENT}/revues/REV-20260701-20260731/renvoi", json={}
        )
        renvoye = plan()
        [reprendre] = [t for t in renvoye["taches"] if t["nature"] == "REPRENDRE_REMARQUES"]
        assert (reprendre["volume"], reprendre["echeance"]) == ("1 remarque", "2026-08-08")
        assert (renvoye["renvoyes"], renvoye["chez_le_reviseur"]) == (1, 0)

    def test_seules_les_pieces_non_traitees_sont_a_traiter(self):
        client = _client(COMPTABLE)
        plan = client.get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()
        [tache] = [
            t
            for t in plan["taches"]
            if t["nature"] == "TRAITER_PIECES" and t["dossier"] == BATIMENT
        ]
        pieces = client.get("/collecte/pieces?a_la_date=2026-08-08").json()
        attendues = sorted(
            p["identifiant"] for p in pieces if p["entreprise"] == BATIMENT and not p["traitee"]
        )
        assert sorted(tache["elements"]) == attendues
        assert "PJ-2026-0001" not in tache["elements"]  # traitée

    # Pas 109 : le dépôt sans l'exigence du mois transmis, ce cas éprouve le plan.
    @pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
    def test_une_declaration_deposee_sort_du_plan(self):
        from tests.test_depots_reconnus import _deposer_juillet
        from tests.test_teledeclaration import NIU

        reviseur = _client(REVISEUR)

        def tva_juillet():
            plan = reviseur.get("/pilotage/plan-de-travail?a_la_date=2026-08-08").json()
            return [
                t for t in plan["taches"] if t["dossier"] == NIU and "TVA 07/2026" in t["elements"]
            ]

        assert tva_juillet(), "la contre-épreuve, avant le dépôt"
        _deposer_juillet(reviseur)
        assert tva_juillet() == []

    def test_qui_ne_saisit_pas_n_a_pas_de_plan(self):
        assert _client(CHARGEE_CLIENTELE).get("/pilotage/plan-de-travail").status_code == 403
