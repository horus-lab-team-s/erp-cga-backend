"""Les délais de la création et leurs dépassements (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026, demandes 12 et 13 : un délai pour monter les statuts, un délai
pour que le client les valide, un délai de retrait des originaux.

⚠️ CE QUI EST GARDÉ
* aucun délai par défaut : sans règle de la direction, rien ne se signale ;
* rédaction (cabinet), validation (client), retrait (client), chacun depuis son jalon ;
* les jalons se datent du jour, une seule fois, à la bonne étape ;
* la règle se fixe par le super administrateur, avec un motif ; la caisse ne la lit pas ;
* les dépassements remontent dans la vue « Retards » du pilotage.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.creation_entreprise.api import (
    REGLE_SANS_DELAIS,
    NatureDuDelai,
    creations_en_memoire,
    delais_depasses,
)
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import SUPER_ADMIN, _renforcer

FORMALITES = "p.moukouri@cga-brcg.cm"
DIRECTRICE = "b.mballa@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"
MOTIF = "Délais validés en réunion de direction"

REGLE = REGLE_SANS_DELAIS.model_copy(
    update={
        "delai_redaction_statuts_jours": 10,
        "delai_validation_statuts_jours": 15,
        "delai_retrait_originaux_jours": 30,
    }
)


def _dossier(reference):
    return next(d for d in creations_en_memoire().lister() if d.reference == reference)


class TestLeDomaine:
    def test_sans_regle_rien_ne_se_signale(self):
        constitution = _dossier("CR-2026-0012")
        assert delais_depasses(constitution, REGLE_SANS_DELAIS, date(2027, 6, 1)) == []

    def test_la_redaction_court_depuis_la_constitution(self):
        constitution = _dossier("CR-2026-0012")  # en constitution depuis le 3 août 2026
        assert delais_depasses(constitution, REGLE, date(2026, 8, 13)) == []
        (retard,) = delais_depasses(constitution, REGLE, date(2026, 8, 20))
        assert retard.nature is NatureDuDelai.REDACTION_STATUTS
        assert not retard.du_fait_du_client
        assert (retard.echeance, retard.jours_de_retard) == (date(2026, 8, 13), 7)

    def test_la_validation_court_depuis_la_transmission(self):
        dossier = _dossier("CR-2026-0012")
        sans_statuts = tuple(
            p.model_copy(update={"fournie_le": None}) if p.code == "STATUTS" else p
            for p in dossier.pieces
        )
        transmis = dossier.model_copy(
            update={"statuts_transmis_le": date(2026, 9, 1), "pieces": sans_statuts}
        )
        (retard,) = delais_depasses(transmis, REGLE, date(2026, 9, 20))
        assert retard.nature is NatureDuDelai.VALIDATION_STATUTS and retard.du_fait_du_client
        assert retard.jours_de_retard == 4
        # Les statuts signés reçus, la validation est faite.
        assert (
            delais_depasses(
                transmis.model_copy(update={"pieces": dossier.pieces}), REGLE, date(2026, 9, 20)
            )
            == []
        )

    def test_le_retrait_court_depuis_la_remise_et_cesse_au_retrait(self):
        livraison = _dossier("CR-2026-0015")  # en livraison depuis le 25 juin 2026
        (retard,) = delais_depasses(livraison, REGLE, date(2026, 8, 1))
        assert retard.nature is NatureDuDelai.RETRAIT_ORIGINAUX
        retire = livraison.model_copy(update={"originaux_remis_le": date(2026, 7, 30)})
        assert delais_depasses(retire, REGLE, date(2026, 8, 1)) == []

    def test_un_dossier_clos_n_est_jamais_en_retard(self):
        assert delais_depasses(_dossier("CR-2026-0009"), REGLE, date(2027, 1, 1)) == []


def _en(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


@exige_postgresql
class TestParLesRoutes:
    def test_la_regle_se_fixe_avec_un_motif_et_les_retards_la_suivent(self, plateforme):
        client = plateforme
        with horloge_figee(datetime(2026, 11, 9, 8, 0)):
            _en(client, FORMALITES)
            lue = client.get("/creations/regle-des-delais")
            assert lue.status_code == 200, lue.text
            assert lue.json()["par_defaut"] is True
            _en(client, CAISSIERE)
            assert client.get("/creations/regle-des-delais").status_code == 403

            _en(client, DIRECTRICE)
            avant = client.get("/pilotage/retards").json()["retards"]
            assert not [r for r in avant if "Statuts à rédiger" in r["libelle"]]

            _en(client, SUPER_ADMIN)
            _renforcer(client)
            sans_motif = client.put(
                "/creations/regle-des-delais", json={"delai_redaction_statuts_jours": 10}
            )
            assert sans_motif.status_code == 422
            fixee = client.put(
                "/creations/regle-des-delais",
                json={
                    "delai_redaction_statuts_jours": 10,
                    "delai_retrait_originaux_jours": 30,
                    "motif": MOTIF,
                },
            )
            assert fixee.status_code == 200, fixee.text
            assert fixee.json()["par_defaut"] is False

            _en(client, DIRECTRICE)
            apres = client.get("/pilotage/retards").json()["retards"]
            redaction = [r for r in apres if r["dossier"] == "CR-2026-0012"]
            assert redaction and "délai du cabinet" in redaction[0]["libelle"]
            retrait = [r for r in apres if r["dossier"] == "CR-2026-0015"]
            assert retrait and "délai du client" in retrait[0]["libelle"]

    def test_les_jalons_se_datent_une_fois_a_la_bonne_etape(self, plateforme):
        client = plateforme
        with horloge_figee(datetime(2026, 11, 9, 8, 0)):
            _en(client, FORMALITES)
            # Les originaux ne se remettent pas d'un dossier en constitution.
            assert client.post("/creations/CR-2026-0012/originaux-remis").status_code == 409
            transmis = client.post("/creations/CR-2026-0012/statuts-transmis")
            assert transmis.status_code == 200, transmis.text
            assert transmis.json()["statuts_transmis_le"] == "2026-11-09"
        with horloge_figee(datetime(2026, 11, 12, 8, 0)):
            _en(client, FORMALITES)  # trois jours plus tard, une nouvelle session
            encore = client.post("/creations/CR-2026-0012/statuts-transmis")
            assert encore.json()["statuts_transmis_le"] == "2026-11-09", "pas de seconde date"
            remis = client.post("/creations/CR-2026-0015/originaux-remis")
            assert remis.status_code == 200, remis.text
            assert remis.json()["originaux_remis_le"] == "2026-11-12"
            _en(client, CAISSIERE)
            assert client.post("/creations/CR-2026-0013/statuts-transmis").status_code == 403
