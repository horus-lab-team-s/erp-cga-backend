"""Retirer une décision validée : une valeur du référentiel, une règle du cabinet (pas 98).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Aux pas 95 et 97, un cabinet valide une valeur du référentiel ou une règle de conformité,
avec effet immédiat. Aucune route ne permettait d'y revenir : une erreur validée était
définitive, et la seule issue était d'éditer la base.

Le retrait est **daté vers l'avant** : à compter de la date, la valeur du référentiel commun
reprend, ou la règle cesse de contrôler. Les calculs et contrôles déjà rendus ne changent pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.conformite.api import (
    FACTURES_DEMO,
    regles_du_cabinet_en_vigueur,
    vider_les_regles_du_cabinet,
)
from app.contextes.referentiel.api import (
    DecisionSurLeReferentiel,
    Fondement,
    ServiceParametres,
    SorteDeDecision,
    StatutDecision,
    parametres_communs,
    vider_les_decisions,
)
from app.contextes.referentiel.domaine.surcouche import appliquer_la_surcouche
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, atelier, reinitialiser_atelier
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.horloge import horloge_figee
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

FISCALISTE = "r.ebolo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
LE = datetime(2026, 9, 15, 10, 0)
MOTIF = "Le texte a été abrogé par la loi rectificative."


#: Le jour où ce fichier a été écrit : voir `neuf`.
ECRIT_LE = datetime(2026, 9, 15, 10, 0)


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_decisions()
    vider_les_regles_du_cabinet()
    # ⚠️ Horloge figée au jour où ces tests ont été écrits (pas 100). Ils posent des dates
    # d'effet en dur (« 2026-09-15 ») et le système refuse, à raison, une règle qui prend
    # effet dans le passé : sans horloge figée, ils échouaient dès le lendemain. Une
    # bombe à retardement, trouvée en lançant la suite le 16/09/2026.
    with horloge_figee(ECRIT_LE):
        yield
    vider_les_decisions()
    vider_les_regles_du_cabinet()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _tva(jour: date, decisions) -> float:
    return (
        ServiceParametres(appliquer_la_surcouche(parametres_communs(), decisions))
        .resoudre("TVA_TAUX_GENERAL", jour)
        .valeur
    )


def _decision(**surcharges) -> DecisionSurLeReferentiel:
    valeurs = {
        "identifiant": "DEC-1",
        "code": "TVA_TAUX_GENERAL",
        "sorte": SorteDeDecision.NOUVELLE_VERSION,
        "applicable_du": date(2027, 1, 1),
        "valeur": 20.0,
        "fondement": Fondement(texte="Loi de finances 2027", source="Journal officiel"),
        "motif": "Loi de finances 2027.",
        "propose_par": "C-006",
        "propose_par_nom": "Roger EBOLO",
        "propose_le": LE,
        "statut": StatutDecision.APPLIQUEE,
        "tranche_par": "C-003",
        "tranche_par_nom": "Aïcha BOUBA",
        "tranche_le": LE,
    }
    return DecisionSurLeReferentiel(**{**valeurs, **surcharges})


# ── La surcouche retirée ──────────────────────────────────────────────────────


class TestLaValeurCommuneReprend:
    def test_a_compter_de_la_fin_la_valeur_du_fichier_reprend(self):
        retiree = [_decision(fin_d_effet=date(2027, 6, 1))]
        assert _tva(date(2026, 12, 31), retiree) == 19.25
        assert _tva(date(2027, 5, 31), retiree) == 20.0
        assert _tva(date(2027, 6, 1), retiree) == 19.25
        assert _tva(date(2031, 1, 1), retiree) == 19.25

    def test_retiree_a_sa_date_d_effet_elle_ne_s_applique_jamais(self):
        assert _tva(date(2027, 2, 1), [_decision(fin_d_effet=date(2027, 1, 1))]) == 19.25

    def test_une_validation_retiree_redevient_a_valider(self):
        [version] = [p for p in parametres_communs() if p.code == "DSF_DELAI_JOURS_APRES_CLOTURE"][
            0
        ].versions
        validation = _decision(
            code="DSF_DELAI_JOURS_APRES_CLOTURE",
            sorte=SorteDeDecision.VALIDATION,
            applicable_du=version.applicable_du,
            valeur=None,
            fondement=None,
        )
        resolu = lambda decisions: ServiceParametres(  # noqa: E731
            appliquer_la_surcouche(parametres_communs(), decisions)
        ).resoudre("DSF_DELAI_JOURS_APRES_CLOTURE", date(2026, 9, 15))
        assert resolu([validation]).statut.value == "VALIDE"
        assert (
            resolu([validation.model_copy(update={"fin_d_effet": date(2026, 9, 15)})]).statut.value
            == "A_VALIDER"
        )


# ── Par l'API : le référentiel ────────────────────────────────────────────────


def _valeur_validee(fiscaliste: TestClient, reviseur: TestClient) -> str:
    proposee = fiscaliste.post(
        "/transverse/referentiel/parametres/TVA_TAUX_GENERAL/propositions",
        json={
            "valeur": 20.0,
            "applicable_du": "2027-01-01",
            "fondement": {
                "texte": "Loi de finances 2027, article 7",
                "source": "Journal officiel du 30/12",
            },
            "motif": "Loi de finances 2027 publiée au Journal officiel.",
        },
    )
    assert proposee.status_code == 201, proposee.text
    identifiant = proposee.json()["identifiant"]
    assert (
        reviseur.post(
            f"/transverse/referentiel/decisions/{identifiant}/tranchage",
            json={"decision": "VALIDER", "motif": "Relu sur le Journal officiel, taux conforme."},
        ).status_code
        == 200
    )
    return identifiant


def _lire(client: TestClient, jour: str) -> float:
    return client.get(
        "/referentiel/parametres/TVA_TAUX_GENERAL", params={"a_la_date": jour}
    ).json()["valeur"]


class TestRetirerUneValeurValidee:
    def test_le_reviseur_retire_la_valeur_a_compter_d_une_date(self):
        fiscaliste, reviseur = _client(FISCALISTE), _client(REVISEUR)
        identifiant = _valeur_validee(fiscaliste, reviseur)
        retrait = reviseur.post(
            f"/transverse/referentiel/decisions/{identifiant}/retrait",
            json={"a_compter_du": "2027-06-01", "motif": MOTIF},
        )
        assert retrait.status_code == 200, retrait.text
        assert retrait.json()["fin_d_effet"] == "2027-06-01"
        assert retrait.json()["retire_par_nom"] == "Aïcha BOUBA"
        assert (_lire(reviseur, "2027-05-31"), _lire(reviseur, "2027-06-01")) == (20.0, 19.25)

        assert [
            e.action
            for e in atelier().journal.lister()
            if e.action == "referentiel.decision_retiree"
        ]
        titres = [
            n["titre"] for n in fiscaliste.get("/transverse/notifications").json()["notifications"]
        ]
        assert "TVA_TAUX_GENERAL : décision du cabinet retirée" in titres

    def test_on_ne_retire_pas_dans_le_passe_ni_deux_fois(self):
        fiscaliste, reviseur = _client(FISCALISTE), _client(REVISEUR)
        identifiant = _valeur_validee(fiscaliste, reviseur)
        adresse = f"/transverse/referentiel/decisions/{identifiant}/retrait"
        passe = reviseur.post(adresse, json={"a_compter_du": "2026-01-01", "motif": MOTIF})
        assert passe.status_code == 409 and "passé" in passe.json()["detail"]
        avant = reviseur.post(adresse, json={"a_compter_du": "2026-12-01", "motif": MOTIF})
        assert avant.status_code == 409 and "ne prend effet que le" in avant.json()["detail"]
        assert (
            reviseur.post(adresse, json={"a_compter_du": "2027-01-01", "motif": MOTIF}).status_code
            == 200
        )
        assert (
            reviseur.post(adresse, json={"a_compter_du": "2027-02-01", "motif": MOTIF}).status_code
            == 409
        )
        assert _lire(reviseur, "2027-03-01") == 19.25

    def test_hors_du_circuit_on_ne_retire_pas(self):
        identifiant = _valeur_validee(_client(FISCALISTE), _client(REVISEUR))
        comptable = _client(COMPTABLE)
        assert (
            comptable.post(
                f"/transverse/referentiel/decisions/{identifiant}/retrait",
                json={"a_compter_du": "2027-06-01", "motif": MOTIF},
            ).status_code
            == 403
        )

    def test_une_proposition_non_validee_ne_se_retire_pas(self):
        proposee = (
            _client(FISCALISTE)
            .post(
                "/transverse/referentiel/parametres/TVA_TAUX_GENERAL/propositions",
                json={
                    "valeur": 20.0,
                    "applicable_du": "2027-01-01",
                    "fondement": {"texte": "Loi de finances 2027", "source": "Journal officiel"},
                    "motif": "Loi de finances 2027 publiée au Journal officiel.",
                },
            )
            .json()
        )
        refus = _client(REVISEUR).post(
            f"/transverse/referentiel/decisions/{proposee['identifiant']}/retrait",
            json={"a_compter_du": "2027-06-01", "motif": MOTIF},
        )
        assert refus.status_code == 409


# ── Par l'API : les règles du cabinet ─────────────────────────────────────────

REGLE = {
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
    "fondement": {"texte": "CGI article 143", "source": "Code général des impôts"},
    "message": "Règlement en espèces au-delà du seuil légal.",
    "remediation": "Produire la preuve d'un règlement par virement.",
    "applicable_du": "2026-09-15",
}


def _regle_validee(fiscaliste: TestClient, reviseur: TestClient) -> tuple[str, str]:
    proposee = fiscaliste.post(
        "/conformite/regles/propositions",
        json={"construction": REGLE, "motif": "Traduit l'article 143 du CGI pour le cabinet."},
    )
    assert proposee.status_code == 201, proposee.text
    identifiant = proposee.json()["identifiant"]
    assert (
        reviseur.post(
            f"/conformite/regles/propositions/{identifiant}/tranchage",
            json={"decision": "CONFIRMER", "motif": "Essai relu : réagit sur les bonnes factures."},
        ).status_code
        == 200
    )
    return identifiant, proposee.json()["regle"]["code"]


def _constats(client: TestClient, jour: str) -> list[str]:
    facture = FACTURES_DEMO["F-2026-0412"].model_dump(mode="json")
    facture["document"]["date_emission"] = jour
    rapport = client.post("/conformite/controler", json=facture).json()["rapport"]
    return [c["code_regle"] for c in rapport["constats"]]


class TestRetirerUneRegleValidee:
    def test_la_regle_ne_controle_plus_a_compter_de_la_date(self):
        reviseur = _client(REVISEUR)
        identifiant, code = _regle_validee(_client(FISCALISTE), reviseur)
        assert code in _constats(reviseur, "2026-10-01")
        retrait = reviseur.post(
            f"/conformite/regles/propositions/{identifiant}/retrait",
            json={"a_compter_du": "2026-11-01", "motif": MOTIF},
        )
        assert retrait.status_code == 200, retrait.text
        assert code in _constats(reviseur, "2026-10-31"), "le passé ne change pas"
        assert code not in _constats(reviseur, "2026-11-01")
        assert [
            e.action for e in atelier().journal.lister() if e.action == "conformite.regle_retiree"
        ]

    def test_retiree_a_sa_date_d_effet_elle_ne_controle_jamais(self):
        reviseur = _client(REVISEUR)
        identifiant, _ = _regle_validee(_client(FISCALISTE), reviseur)
        reviseur.post(
            f"/conformite/regles/propositions/{identifiant}/retrait",
            json={"a_compter_du": "2026-09-15", "motif": MOTIF},
        )
        with etabli(configuration().locataire_par_defaut):
            assert regles_du_cabinet_en_vigueur() == []

    def test_on_ne_retire_ni_dans_le_passe_ni_hors_du_circuit(self):
        reviseur = _client(REVISEUR)
        identifiant, _ = _regle_validee(_client(FISCALISTE), reviseur)
        adresse = f"/conformite/regles/propositions/{identifiant}/retrait"
        assert (
            reviseur.post(adresse, json={"a_compter_du": "2026-01-01", "motif": MOTIF}).status_code
            == 409
        )
        assert (
            _client(COMPTABLE)
            .post(adresse, json={"a_compter_du": "2026-11-01", "motif": MOTIF})
            .status_code
            == 403
        )


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_le_retrait_d_une_valeur_se_conserve(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, FISCALISTE)
        proposee = client.post(
            "/transverse/referentiel/parametres/TVA_TAUX_GENERAL/propositions",
            json={
                "valeur": 20.0,
                "applicable_du": "2027-01-01",
                "fondement": {"texte": "Loi de finances 2027", "source": "Journal officiel"},
                "motif": "Loi de finances 2027 publiée au Journal officiel.",
            },
        ).json()
        ouvrir_une_session(client, REVISEUR)
        client.post(
            f"/transverse/referentiel/decisions/{proposee['identifiant']}/tranchage",
            json={"decision": "VALIDER", "motif": "Relu sur le Journal officiel, taux conforme."},
        )
        retrait = client.post(
            f"/transverse/referentiel/decisions/{proposee['identifiant']}/retrait",
            json={"a_compter_du": "2027-06-01", "motif": MOTIF},
        )
        assert retrait.status_code == 200, retrait.text
        assert _lire(client, "2027-06-02") == 19.25
