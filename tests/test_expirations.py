"""Ce qui expire au portefeuille, et les alertes (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026, demande 15 : « signaler automatiquement au gestionnaire et au
client les documents ou abonnements expirés ou sur le point de l'être ».

⚠️ CE QUI EST GARDÉ
* la durée vient du catalogue sourcé (l'ACF : 3 mois) ; sans durée établie, l'expiration se
  saisit — elle n'est jamais inventée ;
* un document plus récent remplace l'ancien, qui reste dans l'histoire ;
* le palier le plus proche seulement, une seule fois (le journal sert de mémoire) ;
* une adhésion renouvelée ne se signale plus ;
* le gestionnaire est prévenu dans sa cloche, l'adhérent par courriel.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.contextes.pilotage.domaine.expirations import palier_a_signaler
from app.contextes.portefeuille.adaptateurs.sortant.documents_a_echeance_yaml import (
    charger_le_catalogue_des_documents,
)
from app.contextes.portefeuille.domaine.documents_a_echeance import expiration_calculee
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = NIU_DEMO["BATIMENT"]
COMPTABLE = "l.fotso@cga-brcg.cm"  # porte BÂTIMENT PLUS
ADHERENT = "jp.nkoa@batimentplus.cm"
DIRECTRICE = "b.mballa@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"


class TestLaConfiguration:
    def test_le_catalogue_est_source_et_n_invente_rien(self):
        catalogue = charger_le_catalogue_des_documents(Path("Docs/referentiel"))
        acf = catalogue.sorte("ACF")
        assert acf.validite_mois == 3 and acf.statut == "A_VALIDER" and acf.source
        # Durée non trouvée : pas de durée.
        assert catalogue.sorte("ATTESTATION_CNPS").validite_mois is None
        assert expiration_calculee(acf, date(2026, 11, 30)) == date(2027, 2, 28)

    def test_le_palier_le_plus_proche_une_seule_fois(self):
        paliers = (30, 7, 0)
        assert palier_a_signaler(40, paliers, []) is None
        assert palier_a_signaler(30, paliers, []) == 30
        assert palier_a_signaler(20, paliers, [30]) is None
        # Enregistré tard : le palier de 7, pas celui de 30.
        assert palier_a_signaler(5, paliers, []) == 7
        assert palier_a_signaler(-3, paliers, [30, 7]) == 0
        assert palier_a_signaler(-3, paliers, [30, 7, 0]) is None


def _en(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


@exige_postgresql
class TestParLesRoutes:
    def _acf(self, client, delivre_le="2026-09-20", **autres):
        return client.post(
            f"/portefeuille/entreprises/{BATIMENT}/documents-a-echeance",
            json={"code": "ACF", "delivre_le": delivre_le, **autres},
        )

    def test_enregistrer_puis_remplacer_une_attestation(self, plateforme):
        client = plateforme
        _en(client, COMPTABLE)
        premier = self._acf(client)
        assert premier.status_code == 201, premier.text
        (lu,) = premier.json()["en_vigueur"]
        assert lu["document"]["expire_le"] == "2026-12-20"
        second = self._acf(client, delivre_le="2026-11-05")
        (lu,) = second.json()["en_vigueur"]
        assert lu["document"]["expire_le"] == "2027-02-05"
        assert len(second.json()["historique"]) == 1

    def test_sans_duree_etablie_l_expiration_se_saisit(self, plateforme):
        client = plateforme
        _en(client, COMPTABLE)
        corps = {"code": "ATTESTATION_CNPS", "delivre_le": "2026-10-01"}
        refus = client.post(
            f"/portefeuille/entreprises/{BATIMENT}/documents-a-echeance", json=corps
        )
        assert refus.status_code == 422 and "date d'expiration" in refus.text, refus.text
        saisie = client.post(
            f"/portefeuille/entreprises/{BATIMENT}/documents-a-echeance",
            json={**corps, "expire_le": "2026-12-31"},
        )
        assert saisie.status_code == 201, saisie.text

    def test_l_adherent_l_enregistre_aussi_la_caisse_non(self, plateforme):
        client = plateforme
        _en(client, ADHERENT)
        assert self._acf(client).status_code == 201
        _en(client, CAISSIERE)
        assert self._acf(client).status_code in (403, 404)

    def test_le_palier_se_signale_une_fois_dans_la_cloche_et_par_courriel(self, plateforme):
        from app.contextes.pilotage.adaptateurs.entrant.routes_expirations import (
            alerter_des_expirations,
        )
        from app.contextes.transverse.api import service_de_notification, unite_de_travail
        from app.partage.locataire import etabli

        client = plateforme
        _en(client, COMPTABLE)
        assert self._acf(client).status_code == 201  # expire le 20/12/2026

        def alerter(jour):
            with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
                return alerter_des_expirations(a_la_date=jour)

        # À 25 jours : palier de 30 (et rien pour les autres dossiers de la démonstration
        # qui n'ont rien de daté à ce palier, ou déjà signalés).
        premier = alerter(date(2026, 11, 25))
        assert not premier.startswith("0 "), premier
        assert alerter(date(2026, 11, 25)).startswith("0 ")
        envoyes = [
            m
            for m in service_de_notification().derniers("expiration.adherent")
            if m.destinataire == ADHERENT
        ]
        assert envoyes, "l'adhérent n'a pas été prévenu"

        cloche = client.get("/transverse/notifications").json()["notifications"]
        assert any(
            n["action"] == "expiration.signalee"
            and "Attestation de conformité fiscale" in n["titre"]
            for n in cloche
        ), cloche

    def test_le_panneau_de_la_direction(self, plateforme):
        client = plateforme
        _en(client, COMPTABLE)
        self._acf(client)
        _en(client, DIRECTRICE)
        lus = client.get("/pilotage/expirations", params={"a_la_date": "2026-12-01"})
        assert lus.status_code == 200, lus.text
        acf = [e for e in lus.json()["expirations"] if e["dossier"] == BATIMENT]
        assert acf and acf[0]["jours_restants"] == 19
