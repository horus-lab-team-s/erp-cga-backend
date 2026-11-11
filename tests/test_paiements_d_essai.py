"""La période d'essai du serveur de la cliente : des paiements qui se valident seuls.

─────────────────────────────────────────────────────────────────────────────────
« Restons sur le serveur de la cliente : la solution n'est pas encore utilisée, nous avons
deux semaines. Pour les tests, des paiements qui se valident de façon automatique. »

⚠️ CE QUI EST GARDÉ
* admise en production (c'est là qu'on l'emploie), contrairement au mode démonstration ;
* les clés Tara sont alors IGNORÉES : aucun débit réel ne peut partir ;
* un paiement initié se valide seul, marqué « ESSAI- » pour être retrouvé et purgé ;
* `/sante` l'annonce ; sans le réglage, rien de tout cela.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from sqlalchemy import text

from app.infrastructure.config import Configuration
from tests import test_mode_demonstration as demonstration
from tests.conftest import exige_postgresql
from tests.test_versements_du_parcours import _fixer_la_regle

# Par le module : importer la classe de test la ferait collecter une seconde fois ici.
_production = demonstration.TestVerrous()._production


class TestLeReglage:
    def test_admis_en_production(self):
        config = Configuration(**_production(paiements_d_essai=True))
        assert config.paiements_d_essai and config.paiements_valides_d_office

    def test_absent_par_defaut(self):
        config = Configuration(**_production())
        assert not config.paiements_d_essai and not config.paiements_valides_d_office

    def test_les_cles_tara_sont_ignorees(self, monkeypatch):
        from app.contextes.souscription.adaptateurs.entrant.routes_http import Comptoir
        from app.infrastructure.config import configuration

        monkeypatch.setenv("CGA_TARA_CLE_API", "cle-reelle")
        monkeypatch.setenv("CGA_TARA_IDENTIFIANT_MARCHAND", "marchand-reel")
        monkeypatch.setenv("CGA_PAIEMENTS_D_ESSAI", "true")
        configuration.cache_clear()
        try:
            assert Comptoir().fournisseur.simule is True
            monkeypatch.delenv("CGA_PAIEMENTS_D_ESSAI")
            configuration.cache_clear()
            assert Comptoir().fournisseur.simule is False
        finally:
            configuration.cache_clear()


@exige_postgresql
def test_en_essai_les_frais_se_valident_seuls_et_se_retrouvent(
    plateforme, moteur_test, monkeypatch
):
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_PAIEMENTS_D_ESSAI", "true")
    configuration.cache_clear()
    try:
        client = plateforme
        _fixer_la_regle(client)
        client.cookies.clear()
        accuse = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Couture Ngono",
                "telephone": "699445566",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        ).json()
        paiement = client.post(
            "/acquisition/demandes/frais-de-traitement/paiement-en-ligne",
            json={"jeton": accuse["jeton_de_frais"]},
        )
        assert paiement.status_code == 202, paiement.text
        assert paiement.json()["valide_d_office"] is True
        etat = client.post(
            "/acquisition/demandes/frais-de-traitement/etat",
            json={"jeton": accuse["jeton_de_frais"]},
        ).json()
        assert etat["verses"] is True
        with moteur_test.connect() as connexion:
            reference = connexion.execute(
                text("SELECT donnees->>'reference_externe' FROM paiement")
            ).scalar_one()
        assert reference.startswith("ESSAI-"), reference
    finally:
        monkeypatch.delenv("CGA_PAIEMENTS_D_ESSAI")
        configuration.cache_clear()


def test_sante_l_annonce_et_seulement_alors(monkeypatch):
    """`/sante` lit la configuration à la création de l'application : une application neuve."""
    from fastapi.testclient import TestClient

    from app.infrastructure.config import configuration
    from app.main import creer_application

    configuration.cache_clear()
    try:
        assert "paiements_d_essai" not in TestClient(creer_application()).get("/sante").json()
        monkeypatch.setenv("CGA_PAIEMENTS_D_ESSAI", "true")
        configuration.cache_clear()
        assert TestClient(creer_application()).get("/sante").json()["paiements_d_essai"] is True
    finally:
        configuration.cache_clear()
