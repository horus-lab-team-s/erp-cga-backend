"""Les obligations hors TVA peuvent enfin cesser d'être en retard.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 59)

Le pas 58 a fait reconnaître un dépôt à l'échéancier dès que son accusé existe. Mais
seule la TVA savait consigner le sien. La CNPS, visible depuis le pas 56, restait donc
« en retard » pour toujours, relancée chaque jour, et le registre d'accusés refusait
de toute façon tout accusé CNPS : il était rattaché au seul guichet de la DGI.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    AccuseIncoherent,
    AccuseReception,
    DocumentATransmettre,
    FormatTransmission,
    ModeDepot,
    Portail,
    PortailManuel,
    reinitialiser_atelier,
)
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_teledeclaration import _renforcer

EMPLOYEUR = "M081234567890P"  # un salarié en CDI depuis 2022
SANS_PERSONNEL = "P019876543210K"
CNPS_JUILLET = {
    "code_obligation": "CNPS",
    "periode_debut": "2026-07-01",
    "periode_fin": "2026-07-31",
    "numero": "CNPS-2026-DIPE-00418",
    "depose_le": "2026-08-12T09:30:00",
    "montant_constate": "184500",
}


def _session(application, courriel="a.bouba@cga-brcg.cm"):
    client = TestClient(application)
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


@pytest.fixture
def application():
    reinitialiser_atelier()
    return creer_application()


@pytest.fixture
def reviseur(application):
    client = _session(application)
    _renforcer(client)
    return client


def _constater(client, niu=EMPLOYEUR, **surcharges):
    return client.post(f"/obligations/dossiers/{niu}/depots", json={**CNPS_JUILLET, **surcharges})


def _cnps_juillet(client, niu=EMPLOYEUR):
    lignes = client.get(
        f"/obligations/dossiers/{niu}/echeancier",
        params={"exercice": "2026", "a_la_date": "2026-09-20"},
    ).json()
    (ligne,) = [
        ligne for ligne in lignes
        if ligne["obligation"]["code_obligation"] == "CNPS"
        and ligne["obligation"]["periode_debut"] == "2026-07-01"
    ]
    return ligne


class TestLeRegistreTientTousLesGuichets:
    """⚠️ Le contrôle se fait contre le guichet du document, plus contre celui du registre."""

    @staticmethod
    def _document(portail):
        from datetime import date

        return DocumentATransmettre(
            portail=portail, code_document="CNPS", entreprise=EMPLOYEUR,
            periode_debut=date(2026, 7, 1), periode_fin=date(2026, 7, 31),
            format=FormatTransmission.SAISIE_MANUELLE, contenu='{"constat":1}',
        )

    @staticmethod
    def _accuse(document, portail):
        from datetime import datetime

        return AccuseReception(
            numero="N-1", portail=portail, reference_document=document.reference,
            depose_le=datetime(2026, 8, 12, 9, 30), mode=ModeDepot.MANUEL,
            empreinte_deposee=document.empreinte, depose_par="a.bouba",
        )

    def test_un_accuse_cnps_se_consigne_sur_un_document_cnps(self):
        registre = PortailManuel()  # le registre de l'atelier, créé pour la DGI
        document = self._document(Portail.CNPS_DIPE)
        registre.enregistrer_accuse(self._accuse(document, Portail.CNPS_DIPE), document=document)
        assert registre.retrouver(document.reference) is not None

    def test_un_accuse_cnps_ne_s_attache_toujours_pas_a_un_document_dgi(self):
        """La contre-épreuve : la garde n'a pas été retirée, elle a changé d'appui."""
        registre = PortailManuel(Portail.CNPS_DIPE)
        document = self._document(Portail.DGI_TELEDECLARATION)
        with pytest.raises(AccuseIncoherent, match="ne prouve rien"):
            registre.enregistrer_accuse(
                self._accuse(document, Portail.CNPS_DIPE), document=document
            )


class TestLeConstat:
    def test_avant_le_constat_la_cnps_de_juillet_est_en_retard(self, reviseur):
        assert _cnps_juillet(reviseur)["en_retard"] is True

    def test_la_cnps_constatee_est_declaree_partout(self, reviseur):
        reponse = _constater(reviseur)
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert corps["accuse"]["portail"] == "CNPS_DIPE", "le guichet vient du catalogue"
        assert corps["accuse"]["verifiable"] is False, "sans pièce jointe, il le dit"

        ligne = _cnps_juillet(reviseur)
        assert ligne["obligation"]["statut"] == "DECLAREE"
        assert ligne["obligation"]["declaree_le"] == "2026-08-12"
        assert ligne["en_retard"] is False

        relances = reviseur.get("/obligations/relances", params={"a_la_date": "2026-08-16"}).json()
        assert not [
            r for r in relances
            if r["obligation"]["entreprise"] == EMPLOYEUR
            and r["obligation"]["code_obligation"] == "CNPS"
            and r["obligation"]["periode_debut"] == "2026-07-01"
        ]

    def test_la_patente_payable_d_avance_se_constate_pendant_sa_periode(self, reviseur):
        reponse = _constater(
            reviseur, code_obligation="PATENTE", periode_debut="2026-01-01",
            periode_fin="2026-12-31", numero="PAT-2026-0091", depose_le="2026-02-10T11:00:00",
        )
        assert reponse.status_code == 200, reponse.text


class TestLesRefus:
    def test_la_tva_a_son_parcours(self, reviseur):
        reponse = _constater(reviseur, code_obligation="TVA")
        assert reponse.status_code == 409, reponse.text
        assert f"/obligations/dossiers/{EMPLOYEUR}/depot-tva" in reponse.text, (
            "le chemin du parcours dédié est rendu pour ce dossier, pas en gabarit"
        )

    def test_un_second_constat_est_refuse(self, reviseur):
        assert _constater(reviseur).status_code == 200
        second = _constater(reviseur, numero="CNPS-2026-DIPE-00419")
        assert second.status_code == 409, second.text
        assert "déjà déclarée" in second.text

    def test_un_depot_avant_la_fin_de_la_periode_est_refuse(self, reviseur):
        reponse = _constater(reviseur, depose_le="2026-07-20T10:00:00")
        assert reponse.status_code == 409, reponse.text
        assert "période écoulée" in reponse.text

    def test_le_dernier_jour_de_la_periode_est_refuse_aussi(self, reviseur):
        reponse = _constater(reviseur, depose_le="2026-07-31T18:00:00")
        assert reponse.status_code == 409, reponse.text

    def test_un_depot_date_de_l_avenir_est_refuse(self, reviseur):
        reponse = _constater(reviseur, depose_le="2099-01-15T10:00:00")
        assert reponse.status_code == 409, reponse.text
        assert "pas encore eu lieu" in reponse.text

    def test_une_obligation_absente_de_l_echeancier_repond_404(self, reviseur):
        """La CNPS d'un dossier sans personnel n'existe pas : la consigner inventerait un dépôt."""
        reponse = _constater(reviseur, niu=SANS_PERSONNEL)
        assert reponse.status_code == 404, reponse.text
        assert "Périodes connues : aucune" in reponse.text

    def test_le_guichet_ne_se_choisit_pas(self, reviseur):
        reponse = _constater(reviseur, portail="DGI_TELEDECLARATION")
        assert reponse.status_code == 422, reponse.text

    def test_sans_second_facteur_c_est_refuse(self, application):
        reponse = _constater(_session(application))
        assert reponse.status_code == 403, reponse.text
        assert "SecondFacteurRequis" in reponse.text

    def test_le_comptable_ne_constate_pas(self, application):
        reponse = _constater(_session(application, "l.fotso@cga-brcg.cm"))
        assert reponse.status_code in (403, 404), reponse.text


class TestSurPostgresql:
    """Le registre SQL a son propre contrôle de guichet et sa contrainte d'unicité."""

    pytestmark = exige_postgresql

    def test_la_cnps_constatee_persiste_et_se_relit_declaree(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        _renforcer(client)
        assert _cnps_juillet(client)["en_retard"] is True
        reponse = _constater(client)
        assert reponse.status_code == 200, reponse.text
        assert _cnps_juillet(client)["obligation"]["statut"] == "DECLAREE"
        second = _constater(client, numero="CNPS-2026-DIPE-00419")
        assert second.status_code == 409, second.text
