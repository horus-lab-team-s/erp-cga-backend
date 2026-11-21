"""Un NIU, un RCCM, une adresse ou un capital faux sont refusés à la saisie (21 novembre 2026).

Recette en ligne du 20 novembre 2026 : un NIU à treize chiffres passait la création, puis
le portefeuille le marquait bloquant. La revue qui a suivi a trouvé la même brèche au
devis, à l'engagement, à l'inscription au portefeuille et au registre des tiers ; une
adresse « a@b » passait le paiement puis empêchait l'ouverture du compte ; un capital
négatif faisait tomber la conversion en erreur 500.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.creation_entreprise.adaptateurs.entrant.abonne_de_souscription import _capital
from app.contextes.referentiel.api import (
    IdentifiantHorsFormat,
    identifiant_au_format,
    service_parametres,
)
from app.contextes.souscription.domaine.devis import Prospect
from tests.test_inscription_d_une_souscription import (
    NIU,
    _cabinet,
    _inscription,
    _souscrire_et_ouvrir,
)
from tests.test_inscription_d_une_souscription import client as client  # noqa: F401

JOUR = date(2026, 11, 21)
FAUX_NIU = "P1234567890123"


class TestLaRegleCommune:
    def test_remet_au_format_d_ecriture(self):
        assert (
            identifiant_au_format("NIU", " m 112600090017 r ", JOUR, service_parametres())
            == "M112600090017R"
        )
        assert (
            identifiant_au_format("RCCM", "rc/dla/2026/b/9001", JOUR, service_parametres())
            == "RC/DLA/2026/B/9001"
        )

    @pytest.mark.parametrize(
        ("nature", "valeur"), [("NIU", FAUX_NIU), ("RCCM", "RC/DLA/2026/2/2026")]
    )
    def test_refuse_avec_le_modele(self, nature, valeur):
        with pytest.raises(IdentifiantHorsFormat, match="format attendu"):
            identifiant_au_format(nature, valeur, JOUR, service_parametres())


def _devis(client: TestClient, **prospect) -> object:
    return client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ETOA",
                "prenom": "Paul",
                "courriel": "p.etoa@exemple.cm",
                "telephone": "677889911",
                "denomination": "QUINCAILLERIE DU WOURI SARL",
                **prospect,
            },
            "lignes": [{"service": "ADHESION", "formule": "LIBERATOIRE"}],
        },
    )


class TestLaSouscriptionEnLigne:
    def test_un_niu_faux_est_refuse_au_devis(self, client: TestClient):
        reponse = _devis(client, niu=FAUX_NIU)
        assert reponse.status_code == 422, reponse.text
        assert "format attendu" in reponse.json()["detail"]

    def test_un_niu_faux_est_refuse_a_l_engagement(self, client: TestClient):
        devis = _devis(client)
        assert devis.status_code == 201, devis.text
        reponse = client.post(
            f"/souscription/devis/{devis.json()['reference']}/engagement", json={"niu": FAUX_NIU}
        )
        assert reponse.status_code == 422, reponse.text

    def test_une_adresse_sans_domaine_est_refusee_au_devis(self, client: TestClient):
        assert _devis(client, niu=NIU, courriel="a@b").status_code == 422

    def test_l_inscription_refuse_un_rccm_faux(self, client: TestClient):
        reference = _souscrire_et_ouvrir(client)
        reponse = _cabinet("b.mballa@cga-brcg.cm").post(
            f"/acquisition/dossiers/{reference}/inscription",
            json={**_inscription(), "rccm": "RC/DLA/2019/3/2019"},
        )
        assert reponse.status_code == 422, reponse.text
        assert "RCCM" in reponse.json()["detail"]


def test_le_prospect_veut_une_vraie_adresse():
    with pytest.raises(ValidationError, match="adresse électronique"):
        Prospect(nom="A", prenom="B", courriel="a@b", telephone="677889911")
    prospect = Prospect(nom="A", prenom="B", courriel=" P.Etoa@Exemple.CM ", telephone="677889911")
    assert prospect.courriel == "p.etoa@exemple.cm"


def test_un_capital_negatif_n_est_pas_lu():
    assert _capital("-5000") is None
    assert _capital("1000000") == 1000000


def test_un_appel_de_fonds_dont_la_periode_finit_avant_de_commencer_est_refuse():
    from app.contextes.souscription.adaptateurs.entrant.routes_fonds_de_tiers import (
        AppelAArreter,
    )

    with pytest.raises(ValidationError, match="avant de commencer"):
        AppelAArreter(
            code_obligation="TVA",
            libelle="TVA d'octobre",
            periode_debut=date(2026, 10, 31),
            periode_fin=date(2026, 10, 1),
            montant=100,
        )
