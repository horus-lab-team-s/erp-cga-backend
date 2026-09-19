"""Construire une règle de conformité sans syntaxe, l'éprouver, la faire valider (pas 97).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **La traduction** : la négation est posée une fois autour de l'anomalie ; chaque condition
  est confrontée au schéma des faits et au référentiel (fait, opérateur, valeur, unité du
  paramètre) ; un motif ne s'écrit jamais à la main.
- **L'essai** : il juge la condition sur les factures connues, quelle que soit la date d'effet
  de la règle, et respecte sa portée. La règle construite « espèces au-delà du seuil »
  réagit exactement comme la règle du fichier qui dit la même chose.
- **Le circuit** : aucun effet avant validation, quatre yeux, une règle qui accuse toutes les
  factures refusée à la proposition, effet pour le seul cabinet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.conformite.api import (
    FACTURES_DEMO,
    moteur_par_defaut,
    regles_du_cabinet_en_vigueur,
    vider_les_regles_du_cabinet,
)
from app.contextes.conformite.domaine.constructeur import (
    Combinaison,
    ConditionDAnomalie,
    ConstructionRefusee,
    compiler_l_anomalie,
)
from app.contextes.conformite.domaine.schema_faits import SCHEMA_FACTURE
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, atelier, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application
from app.moteur.jsonlogic import evaluer
from app.partage.horloge import horloge_figee
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

FISCALISTE = "r.ebolo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
UNITES = {"SEUIL_ESPECES_DEDUCTIBILITE_TVA": "FCFA", "FORMAT_NIU": "REGEX"}

ESPECES = {
    "libelle": "Facture réglée en espèces au-delà du seuil",
    "severite": "MAJEUR",
    "types_document": ["FACTURE_ACHAT"],
    "regimes_destinataire": ["REEL"],
    "combinaison": "TOUTES",
    "conditions": [
        {"fait": "reglement.mode", "operateur": "EGAL", "valeur": "ESPECES"},
        {
            "fait": "montants.total_ttc",
            "operateur": "SUPERIEUR_OU_EGAL",
            "parametre": "SEUIL_ESPECES_DEDUCTIBILITE_TVA",
        },
    ],
    "tva_non_deductible": True,
    "fondement": {"texte": "CGI article 143", "source": "Code général des impôts, consulté"},
    "message": "Règlement en espèces au-delà du seuil légal.",
    "remediation": "Produire la preuve d'un règlement par virement.",
    "applicable_du": "2026-09-15",
}
MOTIF = "Traduit l'article 143 du CGI pour les achats du cabinet."


#: Le jour où ce fichier a été écrit : voir `neuf`.
ECRIT_LE = datetime(2026, 9, 15, 10, 0)


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_regles_du_cabinet()
    # ⚠️ Horloge figée au jour où ces tests ont été écrits (pas 100). Ils posent des dates
    # d'effet en dur (« 2026-09-15 ») et le système refuse, à raison, une règle qui prend
    # effet dans le passé : sans horloge figée, ils échouaient dès le lendemain. Une
    # bombe à retardement, trouvée en lançant la suite le 16/09/2026.
    with horloge_figee(ECRIT_LE):
        yield
    vider_les_regles_du_cabinet()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _c(**champs) -> ConditionDAnomalie:
    return ConditionDAnomalie(**champs)


# ── La traduction ─────────────────────────────────────────────────────────────


class TestLaTraduction:
    def test_la_negation_est_posee_une_fois_autour_de_l_anomalie(self):
        predicat = compiler_l_anomalie(
            [_c(fait="reglement.mode", operateur="EGAL", valeur="ESPECES")],
            Combinaison.TOUTES,
            schema=SCHEMA_FACTURE,
            unite_du_parametre=UNITES,
        )
        assert predicat == {"!": [{"==": [{"var": "reglement.mode"}, "ESPECES"]}]}
        # VRAI = conforme : une facture en espèces est non conforme, une par virement conforme.
        assert evaluer(predicat, {"reglement": {"mode": "ESPECES"}}) is False
        assert evaluer(predicat, {"reglement": {"mode": "VIREMENT"}}) is True

    def test_au_moins_une_devient_un_ou_dans_l_anomalie(self):
        predicat = compiler_l_anomalie(
            [
                _c(fait="emetteur.niu", operateur="EST_VIDE"),
                _c(fait="emetteur.rccm", operateur="EST_VIDE"),
            ],
            Combinaison.AU_MOINS_UNE,
            schema=SCHEMA_FACTURE,
            unite_du_parametre=UNITES,
        )
        assert evaluer(predicat, {"emetteur": {"niu": "M1", "rccm": None}}) is False
        assert evaluer(predicat, {"emetteur": {"niu": "M1", "rccm": "RC"}}) is True

    def test_un_fait_absent_n_accuse_pas_une_comparaison(self):
        predicat = compiler_l_anomalie(
            [_c(fait="montants.total_ttc", operateur="SUPERIEUR", valeur="1000")],
            Combinaison.TOUTES,
            schema=SCHEMA_FACTURE,
            unite_du_parametre=UNITES,
        )
        assert evaluer(predicat, {"montants": {}}) is True

    @pytest.mark.parametrize(
        ("condition", "attendu"),
        [
            ({"fait": "montants.total_htt", "operateur": "EST_VIDE"}, "n'est pas un fait"),
            ({"fait": "lignes[].designation", "operateur": "EST_VIDE"}, "n'est pas un fait"),
            (
                {"fait": "reglement.mode", "operateur": "SUPERIEUR", "valeur": "ESPECES"},
                "ne se compare pas",
            ),
            (
                {"fait": "reglement.mode", "operateur": "EGAL", "valeur": "BITCOIN"},
                "ne prend pas la valeur",
            ),
            (
                {"fait": "montants.total_ttc", "operateur": "SUPERIEUR", "valeur": "beaucoup"},
                "attend un nombre",
            ),
            (
                {"fait": "montants.total_ttc", "operateur": "SUPERIEUR", "parametre": "FORMAT_NIU"},
                "ne convient pas",
            ),
            (
                {"fait": "emetteur.niu", "operateur": "CORRESPOND_AU_MOTIF", "valeur": "^M"},
                "référentiel",
            ),
            (
                {
                    "fait": "montants.total_ttc",
                    "operateur": "EGAL",
                    "autre_fait": "document.date_emission",
                },
                "même nature",
            ),
        ],
    )
    def test_une_condition_impossible_est_refusee_en_clair(self, condition, attendu):
        with pytest.raises(ConstructionRefusee, match=attendu):
            compiler_l_anomalie(
                [ConditionDAnomalie(**condition)],
                Combinaison.TOUTES,
                schema=SCHEMA_FACTURE,
                unite_du_parametre=UNITES,
            )

    def test_une_condition_compare_a_une_seule_chose(self):
        with pytest.raises(ValueError):
            ConditionDAnomalie(
                fait="montants.total_ttc", operateur="SUPERIEUR", valeur="1", parametre="X"
            )
        with pytest.raises(ValueError):
            ConditionDAnomalie(fait="emetteur.niu", operateur="EST_VIDE", valeur="x")


# ── L'essai ───────────────────────────────────────────────────────────────────


class TestLEssai:
    def test_la_regle_construite_reagit_comme_la_regle_du_fichier(self):
        """« Espèces au-delà du seuil », construite, contre FAC-ACH-007, écrite en fichier."""
        essai = _client(FISCALISTE).post("/conformite/constructeur/essai", json=ESPECES)
        assert essai.status_code == 200, essai.text
        fichier = sorted(
            r
            for r, f in FACTURES_DEMO.items()
            if any(c.code_regle == "FAC-ACH-007" for c in moteur_par_defaut().controler(f).constats)
        )
        assert essai.json()["reagit_sur"] == fichier
        # Seules les factures dans la portée comptent : achats, adhérents au réel.
        dans_la_portee = [
            f
            for f in FACTURES_DEMO.values()
            if f.document.type.value == "FACTURE_ACHAT"
            and f.destinataire is not None
            and f.destinataire.regime.value == "REEL"
        ]
        assert essai.json()["eprouvees"] == len(dans_la_portee) < len(FACTURES_DEMO)
        assert "Constat si mode de règlement est égal à ESPECES et" in essai.json()["phrase"]

    def test_l_essai_juge_la_condition_meme_si_la_regle_prend_effet_plus_tard(self):
        partout = {
            **ESPECES,
            "regimes_destinataire": None,
            "conditions": [{"fait": "montants.total_ttc", "operateur": "SUPERIEUR", "valeur": "0"}],
            "applicable_du": "2030-01-01",
        }
        essai = _client(FISCALISTE).post("/conformite/constructeur/essai", json=partout).json()
        assert essai["reagit_partout"] is True and essai["eprouvees"] > 0

    def test_une_construction_impossible_rend_422(self):
        faux = {
            **ESPECES,
            "conditions": [{"fait": "reglement.mode", "operateur": "EGAL", "valeur": "BITCOIN"}],
        }
        assert (
            _client(FISCALISTE).post("/conformite/constructeur/essai", json=faux).status_code == 422
        )


# ── Le circuit ────────────────────────────────────────────────────────────────


def _proposer(client: TestClient, construction=ESPECES, motif=MOTIF):
    return client.post(
        "/conformite/regles/propositions", json={"construction": construction, "motif": motif}
    )


def _trancher(client: TestClient, identifiant: str, decision="CONFIRMER"):
    return client.post(
        f"/conformite/regles/propositions/{identifiant}/tranchage",
        json={"decision": decision, "motif": "Essai relu : réagit sur les bonnes factures."},
    )


class TestLeCircuit:
    def test_proposee_sans_effet_validee_par_un_autre_elle_controle_le_cabinet(self):
        fiscaliste = _client(FISCALISTE)
        proposee = _proposer(fiscaliste)
        assert proposee.status_code == 201, proposee.text
        identifiant, code = proposee.json()["identifiant"], proposee.json()["regle"]["code"]
        assert proposee.json()["essai"]["reagit_sur"]
        with etabli(configuration().locataire_par_defaut):
            assert regles_du_cabinet_en_vigueur() == []

        soi_meme = _trancher(fiscaliste, identifiant)
        assert soi_meme.status_code == 409 and "autre personne" in soi_meme.json()["detail"]

        validee = _trancher(_client(REVISEUR), identifiant)
        assert validee.status_code == 200, validee.text
        with etabli(configuration().locataire_par_defaut):
            [regle] = regles_du_cabinet_en_vigueur()
            assert regle.code == code and regle.valide_par == "Aïcha BOUBA"
            assert regle.statut.value == "VALIDE"
        assert code in [r["code"] for r in fiscaliste.get("/conformite/regles").json()]

        actions = [
            e.action for e in atelier().journal.lister() if e.action.startswith("conformite.regle")
        ]
        assert actions == ["conformite.regle_proposee", "conformite.regle_validee"]

    def test_une_regle_qui_accuse_toutes_les_factures_est_refusee_a_la_proposition(self):
        partout = {
            **ESPECES,
            "regimes_destinataire": None,
            "conditions": [{"fait": "montants.total_ttc", "operateur": "SUPERIEUR", "valeur": "0"}],
        }
        refus = _proposer(_client(FISCALISTE), partout)
        assert refus.status_code == 409 and "accuserait chaque facture" in refus.json()["detail"]

    def test_une_date_d_effet_passee_est_refusee(self):
        assert (
            _proposer(_client(FISCALISTE), {**ESPECES, "applicable_du": "2026-01-01"}).status_code
            == 409
        )

    @pytest.mark.parametrize("courriel", [COMPTABLE, DIRECTION])
    def test_hors_du_circuit_on_ne_propose_pas(self, courriel):
        assert _proposer(_client(courriel)).status_code == 403

    def test_l_adherent_n_entre_pas_au_constructeur(self):
        assert _client(ADHERENT).get("/conformite/constructeur/catalogue").status_code == 403

    def test_la_direction_ne_valide_pas_une_regle(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        assert _trancher(_client(DIRECTION), identifiant).status_code == 403

    def test_un_refus_ne_change_rien(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        assert _trancher(_client(REVISEUR), identifiant, "REFUSER").json()["statut"] == "REFUSEE"
        with etabli(configuration().locataire_par_defaut):
            assert regles_du_cabinet_en_vigueur() == []

    def test_un_autre_cabinet_ne_recoit_pas_la_regle(self):
        identifiant = _proposer(_client(FISCALISTE)).json()["identifiant"]
        _trancher(_client(REVISEUR), identifiant)
        with etabli("un-autre-cabinet"):
            assert regles_du_cabinet_en_vigueur() == []

    def test_le_catalogue_dit_ses_limites_et_les_droits_de_la_session(self):
        catalogue = _client(FISCALISTE).get("/conformite/constructeur/catalogue").json()
        assert not any("[]" in f["code"] or f["type"] == "LISTE" for f in catalogue["faits"])
        assert "lignes" in catalogue["limites"]
        assert catalogue["peut_proposer"] is True and catalogue["quatre_yeux"] is True


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_une_regle_validee_controle_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, FISCALISTE)
        proposee = _proposer(client)
        assert proposee.status_code == 201, proposee.text
        ouvrir_une_session(client, REVISEUR)
        assert _trancher(client, proposee.json()["identifiant"]).status_code == 200
        codes = [r["code"] for r in client.get("/conformite/regles").json()]
        assert proposee.json()["regle"]["code"] in codes
