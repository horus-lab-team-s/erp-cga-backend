"""Les rappels d'échéance de l'adhérent : son réglage, et le travail qui l'honore (pas 115).

Maquette « Espace adhérent CGA », vue F, « Rappels avant les échéances · 7 jours et 2 jours avant ».

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Le jalon dû** : un seuil, pas une date ; le plus proche franchi ; rien pour un retard.
2. **Les réglages** qui mentiraient : un jalon par défaut hors des choix, des rappels actifs sans
   jalon ; la préférence qui choisit un moment non proposé.
3. **Les routes** : le défaut du référentiel dit comme tel, le réglage au compte et daté au journal,
   les refus (collaborateur, autre dossier, moment non proposé, actifs sans moment).
4. **Le travail** : le rappel dû part une fois (courriel et espace), le suivant part à son jalon, un
   rattrapage n'envoie que le plus proche, un compte qui a coupé les rappels n'en reçoit pas, une
   échéance dont la preuve est envoyée n'est pas rappelée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.obligations.adaptateurs.entrant.travail_des_rappels import (
    envoyer_les_rappels,
    travail_des_rappels,
)
from app.contextes.obligations.adaptateurs.sortant.rappels_adherent_yaml import (
    charger_les_rappels_de_l_adherent,
)
from app.contextes.obligations.domaine.rappels_adherent import (
    PreferenceDeRappels,
    ReglagesDesRappels,
    jalon_du,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


class TestLeJalonDu:
    def test_un_seuil_le_plus_proche_et_rien_en_retard(self):
        jalons = (7, 2)
        assert [jalon_du(j, jalons) for j in (9, 8, 7, 6, 3, 2, 1, 0, -1)] == [
            None, None, 7, 7, 7, 2, 2, 2, None,
        ]
        assert jalon_du(5, ()) is None


class TestLesReglages:
    def test_ce_qui_mentirait(self):
        with pytest.raises(ValidationError, match="parmi les jalons possibles"):
            ReglagesDesRappels(jalons_possibles=(7, 3), jalons_par_defaut=(7, 2))
        with pytest.raises(ValidationError, match="ne partiraient"):
            ReglagesDesRappels(jalons_par_defaut=())
        with pytest.raises(ValidationError, match="écrit deux fois"):
            ReglagesDesRappels(jalons_possibles=(7, 7, 2))
        with pytest.raises(ValidationError, match="entre 0 et 60"):
            ReglagesDesRappels(jalons_possibles=(90, 7, 2))
        assert ReglagesDesRappels(actifs_par_defaut=False, jalons_par_defaut=()).actifs_par_defaut is False

    def test_la_preference(self):
        with pytest.raises(ValidationError, match="au moins un moment"):
            PreferenceDeRappels(actifs=True, jalons=())
        assert PreferenceDeRappels(actifs=False, jalons=()).actifs is False
        with pytest.raises(ValueError, match="ne sont pas proposés"):
            PreferenceDeRappels(actifs=True, jalons=(5,)).verifier(ReglagesDesRappels())

    def test_le_fichier_livre(self):
        reglages = charger_les_rappels_de_l_adherent(REFERENTIEL)
        assert reglages.source == "obligations/rappels_adherent.yaml"
        assert reglages.jalons_par_defaut == (7, 2) and reglages.actifs_par_defaut is True
        assert charger_les_rappels_de_l_adherent(Path("/nulle/part")).source == "valeurs par défaut"
        assert travail_des_rappels().cadence.total_seconds() == reglages.cadence_minutes * 60


# ── Les routes et le travail ──────────────────────────────────────────────────

BATIMENT = "M081234567890P"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
RAPPELS = f"/obligations/dossiers/{BATIMENT}/mes-rappels"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    yield


def _client(courriel):
    client = TestClient(creer_application())
    reponse = client.post("/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO})
    assert reponse.status_code == 200, reponse.text
    return client


def _rappels_cnps_aout():
    """Les courriels de rappel reçus par Jean-Pierre NKOA pour la CNPS d'août (échéance 15/09)."""
    from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

    return [
        m
        for m in service_de_notification().derniers("echeance.rappel")
        if m.destinataire == ADHERENT and m.contexte["titre"] == "Cotisations sociales CNPS"
        and m.contexte["echeance"] == "15/09/2026"
    ]


class TestLesRoutes:
    def test_le_defaut_puis_le_reglage_au_compte(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            adherent = _client(ADHERENT)
            vue = adherent.get(RAPPELS).json()
            assert vue["preference"] == {"actifs": True, "jalons": [7, 2], "defini": False}
            assert vue["jalons_possibles"] == [7, 3, 2, 1]
            assert vue["whatsapp_disponible"] is False and "WhatsApp" in vue["motif_whatsapp"]

            regle = adherent.post(RAPPELS, json={"actifs": True, "jalons": [1, 3]})
            assert regle.status_code == 200, regle.text
            assert regle.json()["preference"] == {"actifs": True, "jalons": [3, 1], "defini": True}
            assert adherent.get(RAPPELS).json()["preference"]["jalons"] == [3, 1]

    def test_les_refus(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            adherent = _client(ADHERENT)
            hors = adherent.post(RAPPELS, json={"actifs": True, "jalons": [5]})
            assert hors.status_code == 422 and "ne sont pas proposés" in hors.json()["detail"]
            vide = adherent.post(RAPPELS, json={"actifs": True, "jalons": []})
            assert vide.status_code == 422 and "au moins un moment" in vide.json()["detail"]
            assert adherent.post(RAPPELS, json={"actifs": False, "jalons": []}).status_code == 200
            assert _client("l.fotso@cga-brcg.cm").get(RAPPELS).status_code == 403
            assert _client(AUTRE_ADHERENT).get(RAPPELS).status_code == 404


class TestLeTravail:
    def test_le_rappel_part_une_fois_puis_au_jalon_suivant(self):
        # La CNPS d'août est due le 15/09. Le 8/09, elle est à 7 jours.
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            assert "rappel(s)" in envoyer_les_rappels()
            (premier,) = _rappels_cnps_aout()
            assert premier.contexte["jours"] == "7" and premier.contexte["periode"] == "août 2026"
            envoyer_les_rappels()
            assert len(_rappels_cnps_aout()) == 1, "un second passage le même jour a renvoyé"
            adherent = _client(ADHERENT)
            avis = adherent.get("/transverse/notifications").json()["notifications"]
            assert any(
                n["titre"] == "Cotisations sociales CNPS : à régler avant le 15/09/2026"
                and n["lien"] == "/mon-espace/echeances"
                for n in avis
            )
        with horloge_figee(datetime(2026, 9, 10, 8, 0)):
            envoyer_les_rappels()
            assert len(_rappels_cnps_aout()) == 1, "le jalon J-7 déjà envoyé est reparti à J-5"
        with horloge_figee(datetime(2026, 9, 13, 8, 0)):
            envoyer_les_rappels()
            deux = _rappels_cnps_aout()
            assert len(deux) == 2 and {m.contexte["jours"] for m in deux} == {"7", "2"}

    def test_un_rattrapage_n_envoie_que_le_plus_proche(self):
        with horloge_figee(datetime(2026, 9, 14, 8, 0)):
            envoyer_les_rappels()
            (seul,) = _rappels_cnps_aout()
            assert seul.contexte["jours"] == "1"

    def test_un_compte_qui_a_coupe_les_rappels_n_en_recoit_pas(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            assert _client(ADHERENT).post(RAPPELS, json={"actifs": False, "jalons": []}).status_code == 200
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoyer_les_rappels()
            assert _rappels_cnps_aout() == []

    def test_les_jalons_choisis_sont_honores(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            assert _client(ADHERENT).post(RAPPELS, json={"actifs": True, "jalons": [3]}).status_code == 200
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoyer_les_rappels()
            assert _rappels_cnps_aout() == [], "le jalon 7, non choisi, est parti"
        with horloge_figee(datetime(2026, 9, 12, 8, 0)):
            envoyer_les_rappels()
            assert [m.contexte["jours"] for m in _rappels_cnps_aout()] == ["3"]

    def test_une_echeance_dont_la_preuve_est_envoyee_n_est_pas_rappelee(self):
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoi = _client(ADHERENT).post(
                f"/obligations/dossiers/{BATIMENT}/preuves-de-paiement",
                json={
                    "code_obligation": "CNPS",
                    "periode_debut": "2026-08-01",
                    "periode_fin": "2026-08-31",
                    "piece": "PJ-2026-0013",
                },
            )
            assert envoi.status_code == 201, envoi.text
            envoyer_les_rappels()
            assert _rappels_cnps_aout() == []


class TestCeQueLaBatterieAMontre:
    """Les cas ajoutés après la batterie de mutations du pas 115."""

    def test_le_dernier_reglage_fait_foi(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            adherent = _client(ADHERENT)
            assert adherent.post(RAPPELS, json={"actifs": True, "jalons": [3]}).status_code == 200
            assert adherent.post(RAPPELS, json={"actifs": True, "jalons": [1]}).status_code == 200
            assert adherent.get(RAPPELS).json()["preference"]["jalons"] == [1]

    def test_des_rappels_coupes_ne_partent_pas_meme_avec_des_jalons(self):
        with horloge_figee(datetime(2026, 9, 1, 8, 0)):
            assert _client(ADHERENT).post(RAPPELS, json={"actifs": False, "jalons": [7, 2]}).status_code == 200
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoyer_les_rappels()
            assert _rappels_cnps_aout() == []

    def test_seuls_les_adherents_sont_rappeles(self):
        from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoyer_les_rappels()
            destinataires = {m.destinataire for m in service_de_notification().derniers("echeance.rappel")}
            assert ADHERENT in destinataires
            assert not any(d.endswith("@cga-brcg.cm") for d in destinataires)

    def test_une_echeance_deja_deposee_n_est_pas_rappelee(self):
        from tests.test_teledeclaration import _renforcer

        with horloge_figee(datetime(2026, 9, 5, 8, 0)):
            reviseur = _client("a.bouba@cga-brcg.cm")
            _renforcer(reviseur)
            depot = reviseur.post(
                f"/obligations/dossiers/{BATIMENT}/depots",
                json={
                    "code_obligation": "CNPS",
                    "periode_debut": "2026-08-01",
                    "periode_fin": "2026-08-31",
                    "numero": "CNPS-AOUT-EN-AVANCE",
                    "depose_le": "2026-09-04T10:00:00",
                },
            )
            assert depot.status_code in (200, 201), depot.text
        with horloge_figee(datetime(2026, 9, 8, 8, 0)):
            envoyer_les_rappels()
            assert _rappels_cnps_aout() == []

    def test_la_cle_porte_le_dossier(self):
        from datetime import date

        from app.contextes.obligations.adaptateurs.entrant.travail_des_rappels import cle_du_rappel

        assert cle_du_rappel("N1", "CNPS", date(2026, 8, 1), 7) != cle_du_rappel("N2", "CNPS", date(2026, 8, 1), 7)
