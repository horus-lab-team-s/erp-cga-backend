"""Le rapport mensuel de la direction (pas 106).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Les mêmes chiffres que les écrans** : chaque section est égale, champ pour champ, à la
   réponse de la route de l'écran correspondant, à la même date.
2. **Figé** : une dérogation levée après la génération change l'écran, pas le rapport.
3. **Intègre** : l'empreinte est canonique, recalculée à la lecture, et écrite au journal ; un
   contenu altéré se voit.
4. **Archivé** : regénérer crée une version suivante ; un mois à venir est refusé.
5. **Réservé** à qui lit le pilotage **et** l'audit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.contextes.conformite.api import vider_les_ecarts
from app.contextes.pilotage.adaptateurs.sortant.depots_rapports import (
    DepotRapportsMemoire,
    vider_les_rapports,
)
from app.contextes.pilotage.api import vider_les_decisions_de_direction
from app.contextes.pilotage.application.rapport_mensuel import (
    RapportRefuse,
    bornes_du_mois,
    generer_le_rapport,
)
from app.contextes.pilotage.domaine.rapport_mensuel import (
    ReglagesDuRapport,
    SectionDuRapport,
    empreinte_du_contenu,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

INSTANT = datetime(2026, 8, 20, 9, 0)
MOTIF = "Désignations précisées par les bons de livraison joints."


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_rapports()
    vider_les_ecarts()
    vider_les_decisions_de_direction()
    yield
    vider_les_rapports()
    vider_les_ecarts()
    vider_les_decisions_de_direction()


class Lu(BaseModel):
    valeur: str


def _generer(depot, mois="2026-07", jour=date(2026, 8, 20), reglages=None, appels=None):
    def lecture(section):
        def lire(du, au, a_la_date):
            if appels is not None:
                appels.append((section, du, au, a_la_date))
            return Lu(valeur=f"{section.value} {du} {au} {a_la_date}")

        return lire

    return generer_le_rapport(
        mois=mois,
        jour=jour,
        lectures={s: lecture(s) for s in SectionDuRapport},
        reglages=reglages or ReglagesDuRapport(),
        par="C-001",
        par_nom="Bernadette MBALLA",
        le=INSTANT,
        depot=depot,
    )


@pytest.mark.usefixtures("cabinet")
class TestLeDomaine:
    def test_les_bornes_et_la_date_des_chiffres(self):
        appels = []
        rapport = _generer(DepotRapportsMemoire(), appels=appels)
        assert (rapport.du, rapport.au, rapport.a_la_date) == (
            date(2026, 7, 1),
            date(2026, 7, 31),
            date(2026, 7, 31),
        )
        # Le mois en cours s'arrête au jour de la génération.
        en_cours = _generer(DepotRapportsMemoire(), mois="2026-08", appels=[])
        assert en_cours.a_la_date == date(2026, 8, 20)
        assert bornes_du_mois("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
        assert [a[0] for a in appels] == list(SectionDuRapport)

    def test_les_sections_suivent_le_referentiel(self):
        reglages = ReglagesDuRapport(
            sections=(SectionDuRapport.DEROGATIONS, SectionDuRapport.RISQUE)
        )
        rapport = _generer(DepotRapportsMemoire(), reglages=reglages)
        assert list(rapport.sections) == ["DEROGATIONS", "RISQUE"]
        assert rapport.sections["RISQUE"] == {"valeur": "RISQUE 2026-07-01 2026-07-31 2026-07-31"}
        with pytest.raises(ValidationError):
            ReglagesDuRapport.model_validate({"sections": ["RENTABILITE"]})

    def test_un_mois_a_venir_est_refuse(self):
        with pytest.raises(RapportRefuse, match="n'a pas commencé"):
            _generer(DepotRapportsMemoire(), mois="2026-09")

    def test_regenerer_cree_une_version_suivante(self):
        depot = DepotRapportsMemoire()
        premier = _generer(depot)
        second = _generer(depot)
        autre_mois = _generer(depot, mois="2026-06")
        assert [premier.identifiant, second.identifiant, autre_mois.identifiant] == [
            "RM-2026-07-1",
            "RM-2026-07-2",
            "RM-2026-06-1",
        ]
        assert [r.identifiant for r in depot.tous()] == [
            "RM-2026-07-2",
            "RM-2026-07-1",
            "RM-2026-06-1",
        ]
        assert depot.trouver("RM-2026-07-1") == premier

    def test_l_empreinte_est_canonique_et_denonce_une_alteration(self):
        rapport = _generer(DepotRapportsMemoire())
        assert rapport.integre
        melange = dict(reversed(list(rapport.sections.items())))
        assert empreinte_du_contenu(rapport.mois, rapport.a_la_date, melange) == rapport.empreinte
        altere = rapport.model_copy(
            update={"sections": {**rapport.sections, "RISQUE": {"valeur": "0"}}}
        )
        assert not altere.integre
        date_changee = rapport.model_copy(update={"a_la_date": date(2026, 7, 30)})
        assert not date_changee.integre

    def test_un_autre_cabinet_ne_voit_pas_le_rapport(self):
        depot = DepotRapportsMemoire()
        _generer(depot)
        with etabli("AUTRE-CABINET"):
            assert depot.tous() == [] and depot.trouver("RM-2026-07-1") is None


@pytest.fixture
def cabinet():
    with etabli("CGA-BRCG"):
        yield


# ── Par les routes ────────────────────────────────────────────────────────────


@pytest.fixture
def application():
    """⚠️ Pas `plateforme` : ce nom est celui de la fixture PostgreSQL du `conftest`."""
    with horloge_figee(INSTANT):
        yield creer_application()


def _client(application, courriel):
    client = TestClient(application)
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


class TestLesRoutes:
    def test_chaque_section_est_la_reponse_de_son_ecran(self, application):
        reviseur = _client(application, "a.bouba@cga-brcg.cm")
        assert (
            reviseur.post(
                "/conformite/regles/FAC-DOC-011/ecarts",
                json={"pieces": ["F-2026-0415", "F-2026-0425"], "motif": MOTIF},
            ).status_code
            == 200
        )
        direction = _client(application, "b.mballa@cga-brcg.cm")
        reponse = direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-08"})
        assert reponse.status_code == 201, reponse.text
        rapport = reponse.json()
        assert rapport["a_la_date"] == "2026-08-20" and rapport["integre"] is True
        sections = rapport["sections"]
        assert (
            sections["RISQUE"]
            == direction.get(
                "/pilotage/tableau-de-bord", params={"a_la_date": "2026-08-20", "exercice": "2026"}
            ).json()
        )
        assert (
            sections["CHARGE"]
            == direction.get(
                "/pilotage/charge-et-production", params={"a_la_date": "2026-08-20"}
            ).json()
        )
        assert (
            sections["DEROGATIONS"]
            == direction.get(
                "/conformite/derogations", params={"du": "2026-08-01", "au": "2026-08-31"}
            ).json()
        )
        assert sections["DEROGATIONS"]["total"] == 2
        assert (
            sections["QUALITE_DES_REGLES"]
            == reviseur.get(
                "/conformite/regles/qualite", params={"du": "2026-08-01", "au": "2026-08-31"}
            ).json()
        )
        assert sections["DECISIONS"] == {"decisions": []}

    def test_le_rapport_est_fige_et_son_empreinte_au_journal(self, application):
        reviseur = _client(application, "a.bouba@cga-brcg.cm")
        ecart = reviseur.post(
            "/conformite/regles/FAC-DOC-011/ecarts",
            json={"pieces": ["F-2026-0415"], "motif": MOTIF},
        ).json()["ecarts"][0]
        direction = _client(application, "b.mballa@cga-brcg.cm")
        rapport = direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-08"}).json()
        # La dérogation est levée ensuite : l'écran change, le rapport archivé non.
        assert (
            reviseur.post(
                f"/conformite/pieces/F-2026-0415/ecarts/{ecart['identifiant']}/levee",
                json={"motif": "Bon de livraison finalement non probant."},
            ).status_code
            == 200
        )
        ecran = direction.get(
            "/conformite/derogations", params={"du": "2026-08-01", "au": "2026-08-31"}
        ).json()
        relu = direction.get(f"/pilotage/rapports-mensuels/{rapport['identifiant']}").json()
        assert ecran["effectives"] == 0
        assert relu["sections"]["DEROGATIONS"]["effectives"] == 1
        assert relu == rapport and relu["integre"] is True
        [entree] = [
            e
            for e in direction.get("/transverse/audit").json()
            if e["action"] == "pilotage.rapport_genere"
        ]
        assert entree["apres"]["sha256_du_contenu"] == rapport["empreinte"]
        assert entree["objet_id"] == "RM-2026-08-1"

    def test_un_rapport_ne_retient_que_les_faits_de_son_mois(self, application):
        """Les dérogations et décisions d'un autre mois n'entrent pas dans le rapport."""
        reviseur = _client(application, "a.bouba@cga-brcg.cm")
        assert (
            reviseur.post(
                "/conformite/regles/FAC-DOC-011/ecarts",
                json={"pieces": ["F-2026-0415"], "motif": MOTIF},
            ).status_code
            == 200
        )
        direction = _client(application, "b.mballa@cga-brcg.cm")
        decision = direction.post(
            "/pilotage/dossiers/M081234567890P/decisions",
            json={
                "mesure": "RENFORCER_SUIVI",
                "motif": "Retards déclaratifs accumulés depuis janvier.",
            },
        )
        assert decision.status_code == 200, decision.text
        juillet = direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-07"}).json()
        assert juillet["sections"]["DEROGATIONS"]["total"] == 0
        assert juillet["sections"]["DECISIONS"] == {"decisions": []}
        # Et une dérogation de juillet n'a rien à faire dans le rapport d'août.
        with horloge_figee(datetime(2026, 7, 25, 9, 0)):
            assert (
                reviseur.post(
                    "/conformite/regles/FAC-DOC-011/ecarts",
                    json={"pieces": ["F-2026-0425"], "motif": MOTIF},
                ).status_code
                == 200
            )
        aout = direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-08"}).json()
        assert aout["sections"]["DEROGATIONS"]["total"] == 1
        assert [
            d["reference_document"] for d in aout["sections"]["DEROGATIONS"]["derogations"]
        ] == ["F-2026-0415"]
        assert [d["mesure"] for d in aout["sections"]["DECISIONS"]["decisions"]] == [
            "RENFORCER_SUIVI"
        ]

    def test_liste_versions_et_refus(self, application):
        direction = _client(application, "b.mballa@cga-brcg.cm")
        direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-07"})
        direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-07"})
        liste = direction.get("/pilotage/rapports-mensuels").json()
        assert [(r["identifiant"], r["integre"]) for r in liste] == [
            ("RM-2026-07-2", True),
            ("RM-2026-07-1", True),
        ]
        futur = direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-09"})
        assert futur.status_code == 422 and "n'a pas commencé" in futur.json()["detail"]
        assert (
            direction.post("/pilotage/rapports-mensuels", json={"mois": "2026-13"}).status_code
            == 422
        )
        assert direction.get("/pilotage/rapports-mensuels/RM-INCONNU").status_code == 404

    def test_reserve_a_qui_lit_le_pilotage_et_l_audit(self, application):
        for courriel in ("a.bouba@cga-brcg.cm", "l.fotso@cga-brcg.cm", "g.atangana@inspection.cm"):
            client = _client(application, courriel)
            assert (
                client.post("/pilotage/rapports-mensuels", json={"mois": "2026-07"}).status_code
                == 403
            )
            assert client.get("/pilotage/rapports-mensuels").status_code == 403


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_le_rapport_relu_en_base_reste_integre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        genere = client.post("/pilotage/rapports-mensuels", json={"mois": "2026-07"})
        assert genere.status_code == 201, genere.text
        relu = client.get(f"/pilotage/rapports-mensuels/{genere.json()['identifiant']}").json()
        assert relu["integre"] is True
        assert relu["empreinte"] == genere.json()["empreinte"]
