"""Lot 1 de la réunion du 4 novembre 2026 avec la responsable du cabinet (8 novembre 2026).

* la localisation choisie dans le référentiel géographique (région › … › quartier) ;
* le loyer à la place du bail, les statuts du client facultatifs ;
* le promoteur étranger, la profession à Ordre, l'activité minière, la demande pour un tiers :
  chacun ajoute ses pièces au dossier de formalité ;
* toutes les formes que le portefeuille connaît, l'association (ou ONG) comprise.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.creation_entreprise.adaptateurs.entrant.abonne_de_souscription import (
    _reference_du_dossier,
    ouvrir_le_dossier_de_creation,
)
from app.contextes.creation_entreprise.api import SituationDuFondateur, checklist_de
from app.contextes.portefeuille.contrats import FormeJuridique
from app.orchestration.boite_d_envoi import EvenementSortant
from tests.test_dossier_de_creation_a_l_encaissement import DOSSIER, FAITS, magasin  # noqa: F401


def _codes(pieces) -> list[str]:
    return [p.code for p in pieces]


# ── La liste des pièces ─────────────────────────────────────────────────────


def test_sans_situation_la_liste_d_avant_est_inchangee():
    assert checklist_de(FormeJuridique.SARL) == checklist_de(
        FormeJuridique.SARL, SituationDuFondateur()
    )
    codes = _codes(checklist_de(FormeJuridique.SARL))
    for absente in ("PROCURATION", "ATTESTATION_ORDRE", "AUTORISATION_EXERCER_ETRANGER"):
        assert absente not in codes


def test_un_promoteur_etranger_fournit_son_passeport_et_son_autorisation():
    pieces = checklist_de(FormeJuridique.SARL, SituationDuFondateur(etranger=True))
    par_code = {p.code: p for p in pieces}
    assert "Passeport" in par_code["ID_FONDATEUR"].libelle
    assert par_code["AUTORISATION_EXERCER_ETRANGER"].obligatoire
    # Une seule pièce d'identité du fondateur : le passeport REMPLACE la CNI.
    assert _codes(pieces).count("ID_FONDATEUR") == 1


def test_une_profession_a_ordre_ajoute_son_attestation():
    pieces = checklist_de(
        FormeJuridique.ETS, SituationDuFondateur(profession_reglementee="VETERINAIRE")
    )
    (attestation,) = [p for p in pieces if p.code == "ATTESTATION_ORDRE"]
    assert "vétérinaire" in attestation.libelle and attestation.obligatoire


def test_une_activite_miniere_signale_sa_piece_sans_bloquer():
    """Les pièces exactes restent à préciser par le cabinet : informer, pas interdire."""
    pieces = checklist_de(FormeJuridique.SARL, SituationDuFondateur(activite_miniere=True))
    (miniere,) = [p for p in pieces if p.code == "AUTORISATION_MINIERE"]
    assert not miniere.obligatoire


def test_une_demande_pour_un_tiers_demande_la_procuration():
    pieces = checklist_de(FormeJuridique.SARL, SituationDuFondateur(pour_le_compte_d_un_tiers=True))
    (procuration,) = [p for p in pieces if p.code == "PROCURATION"]
    assert procuration.obligatoire


def test_le_bail_n_est_plus_demande_comme_tel():
    """Il ne se signe qu'au nom de la société : le libellé le dit, au dossier de constitution."""
    (siege,) = [p for p in checklist_de(FormeJuridique.SARL) if p.code == "JUSTIFICATIF_SIEGE"]
    assert "au nom de la société" in siege.libelle


@pytest.mark.parametrize(
    ("faits", "attendue"),
    [
        ({}, SituationDuFondateur()),
        (
            {
                "nationalite_promoteur": "ETRANGERE",
                "profession_reglementee": "AVOCAT",
                "activite_miniere": "true",
                "pour_le_compte_d_un_tiers": True,
            },
            SituationDuFondateur(
                etranger=True,
                profession_reglementee="AVOCAT",
                activite_miniere=True,
                pour_le_compte_d_un_tiers=True,
            ),
        ),
        (
            {"nationalite_promoteur": "CAMEROUNAISE", "profession_reglementee": "AUCUNE"},
            SituationDuFondateur(),
        ),
        ({"activite_miniere": "false"}, SituationDuFondateur()),
    ],
)
def test_la_situation_se_lit_dans_les_reponses(faits, attendue):
    assert SituationDuFondateur.depuis_les_faits(faits) == attendue


def test_une_creation_payee_par_un_etranger_ouvre_un_dossier_avec_ses_pieces(magasin):  # noqa: F811
    faits = {**FAITS, "nationalite_promoteur": "ETRANGERE", "pour_le_compte_d_un_tiers": "true"}
    ouvrir_le_dossier_de_creation(
        EvenementSortant(
            identifiant="ev-etranger",
            nom="PaiementEncaissé",
            cle=DOSSIER,
            charge={
                "service": "CREATION",
                "faits_fondateurs": faits,
                "nom_titulaire": "Jean DUPONT",
                "courriel_titulaire": "",
            },
        )
    )
    codes = _codes(magasin.lire(_reference_du_dossier(DOSSIER)).pieces)
    assert "AUTORISATION_EXERCER_ETRANGER" in codes
    assert "PROCURATION" in codes


# ── Le questionnaire, version 3 ─────────────────────────────────────────────


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_le_site_recoit_la_localisation_et_les_nouvelles_questions(client):
    reponse = client.get("/acquisition/questionnaires/CREATION/visiteur")
    assert reponse.status_code == 200
    questions = {q["code"]: q for q in reponse.json()["questions"]}
    assert questions["arrondissement_siege"]["saisie"] == "LOCALISATION"
    for code in (
        "ville_siege",
        "quartier_siege",
        "loyer_mensuel",
        "nationalite_promoteur",
        "profession_reglementee",
        "activite_miniere",
        "pour_le_compte_d_un_tiers",
    ):
        assert code in questions, code
    formes = {v["valeur"] for v in questions["forme_juridique"]["valeurs"]}
    assert {"SCI", "ASSOCIATION"} <= formes
    # Chaque forme proposée est une forme du portefeuille, sinon le dossier ne s'ouvre pas.
    assert formes <= {f.value for f in FormeJuridique}


# ── La géographie, servie au site ───────────────────────────────────────────


def test_le_decoupage_donne_la_valeur_de_la_question_region(client):
    reponse = client.get("/referentiel/geographie/decoupage")
    assert reponse.status_code == 200
    regions = reponse.json()["regions"]
    assert len(regions) == 10
    questions = client.get("/acquisition/questionnaires/CREATION/visiteur").json()["questions"]
    (region,) = [q for q in questions if q["code"] == "region_siege"]
    # ⚠️ La cascade remplit `region_siege` avec `cle` : chaque région DOIT être une valeur admise.
    assert {r["cle"] for r in regions} == {v["valeur"] for v in region["valeurs"]}


def test_douala_premier_propose_douala_et_ses_quartiers(client):
    regions = client.get("/referentiel/geographie/decoupage").json()["regions"]
    (douala_1,) = [
        a
        for r in regions
        for d in r["departements"]
        for a in d["arrondissements"]
        if a["nom"] == "Douala 1er"
    ]
    lieux = client.get(f"/referentiel/geographie/arrondissements/{douala_1['code']}/lieux").json()
    assert "Douala" in [v["nom"] for v in lieux["villes"]]
    assert "Bonapriso" in [q["nom"] for q in lieux["quartiers"]]


def test_un_arrondissement_inconnu_rend_404(client):
    assert client.get("/referentiel/geographie/arrondissements/CM999/lieux").status_code == 404


def test_la_consultation_prealable_est_proposee_en_premier(client):
    """« Il faut ajouter une option de consultation pour la création » (réunion, 25:37)."""
    questions = client.get("/acquisition/questionnaires/CREATION/visiteur").json()["questions"]
    assert questions[0]["code"] == "consultation_prealable"
    assert questions[0]["type"] == "BOOLEEN"
