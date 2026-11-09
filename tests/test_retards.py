"""Les retards du cabinet, en rouge, par responsable (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026, demandes 20 et 22 : « signaler les retards par un code couleur,
avec les délais, pour que le responsable repère tout de suite les dossiers en souffrance ».

⚠️ CE QUI EST GARDÉ
* la direction voit tout le cabinet ; un comptable, son seul périmètre ; la caisse, ni l'un
  ni l'autre côté pilotage ;
* chaque retard porte son échéance, ses jours de retard et ses porteurs ;
* côté commerce : un rendez-vous passé non constaté, des honoraires en retard ;
* aucune note des personnes : des retards de portefeuille, rien d'autre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.contextes.transverse.api import NIU_DEMO
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

DIRECTRICE = "b.mballa@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"  # porte BÂTIMENT PLUS
CAISSIERE = "n.essama@cga-brcg.cm"
BATIMENT = NIU_DEMO["BATIMENT"]


def _en(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


class TestLePilotage:
    def test_la_direction_voit_tout_le_cabinet_et_les_porteurs(self, plateforme):
        client = plateforme
        _en(client, DIRECTRICE)
        lus = client.get("/pilotage/retards", params={"a_la_date": "2026-12-31"})
        assert lus.status_code == 200, lus.text
        corps = lus.json()
        assert corps["tout_le_cabinet"] is True
        assert corps["retards"], "la démonstration a des déclarations en retard"
        premier = corps["retards"][0]
        assert premier["jours_de_retard"] >= 1 and premier["echeance"] and premier["lien"]
        # Les plus en retard d'abord.
        jours = [r["jours_de_retard"] for r in corps["retards"]]
        assert jours == sorted(jours, reverse=True)
        assert any(r["responsables"] for r in corps["retards"])

    def test_le_comptable_ne_voit_que_son_perimetre(self, plateforme):
        client = plateforme
        _en(client, DIRECTRICE)
        tout = client.get("/pilotage/retards", params={"a_la_date": "2026-12-31"}).json()
        _en(client, COMPTABLE)
        siens = client.get("/pilotage/retards", params={"a_la_date": "2026-12-31"}).json()
        assert siens["tout_le_cabinet"] is False
        assert {r["dossier"] for r in siens["retards"]} <= {r["dossier"] for r in tout["retards"]}
        assert len(siens["retards"]) < len(tout["retards"])

    def test_la_caisse_n_y_a_pas_acces(self, plateforme):
        client = plateforme
        _en(client, CAISSIERE)
        assert client.get("/pilotage/retards").status_code == 403


class TestLeCommerce:
    def test_un_rendez_vous_passe_non_constate_est_en_retard(self, plateforme):
        client = plateforme
        lundi = datetime(2026, 11, 9, 7, 0)  # 8 h à Douala
        with horloge_figee(lundi):
            client.cookies.clear()
            pris = client.post(
                "/acquisition/demandes",
                json={
                    "nom": "Ngono Couture",
                    "telephone": "699445566",
                    "courriel": "ngono@exemple.cm",
                    "service_souhaite": "CREATION",
                    "canal_prefere": "APPEL",
                    "rendez_vous": {"mode": "PRESENTIEL", "debut": "2026-11-10T09:00:00"},
                },
            )
            assert pris.status_code == 201, pris.text
        with horloge_figee(lundi + timedelta(days=2)):
            _en(client, DIRECTRICE)
            retards = client.get("/acquisition/retards").json()["retards"]
        rdv = [r for r in retards if r["categorie"] == "RENDEZ_VOUS"]
        assert len(rdv) == 1 and rdv[0]["jours_de_retard"] == 1, rdv
        assert rdv[0]["lien"] == "/acquisition/rendez-vous"

    def test_les_honoraires_en_retard_se_voient_de_la_direction(self, plateforme):
        client = plateforme
        _en(client, DIRECTRICE)
        retards = client.get("/acquisition/retards").json()["retards"]
        reglement = [r for r in retards if r["categorie"] == "REGLEMENT"]
        assert any(r["dossier"] == BATIMENT for r in reglement), reglement

    def test_ni_le_commerce_ni_l_argent(self, plateforme):
        client = plateforme
        _en(client, COMPTABLE)
        assert client.get("/acquisition/retards").status_code == 403


class TestLaProformaExpiree:
    """Lot 4 (demande 11–12) : une proforma transmise, ni acceptée ni réémise au-delà de sa
    validité, remonte « expirée sans acceptation : à réémettre »."""

    def test_elle_remonte_apres_sa_validite(self, plateforme):
        from app.infrastructure.config import configuration
        from tests.test_recette_du_parcours import TestLeReglementNotifieParLOperateur

        client = plateforme
        emise = datetime(2026, 11, 9, 8, 0)
        jours = configuration().validite_lien_acceptation_jours
        with horloge_figee(emise):
            reference, numero = TestLeReglementNotifieParLOperateur()._dossier_accepte(
                client, accepter=False
            )

        def expirees(instant):
            with horloge_figee(instant):
                _en(client, DIRECTRICE)
                retards = client.get("/acquisition/retards").json()["retards"]
            return [
                r
                for r in retards
                if r["dossier"] == reference and "expirée sans acceptation" in r["libelle"]
            ]

        assert expirees(emise + timedelta(days=jours - 1)) == []
        (retard,) = expirees(emise + timedelta(days=jours + 3))
        assert numero in retard["libelle"] and retard["jours_de_retard"] == 3
