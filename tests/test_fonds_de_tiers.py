"""Le cabinet paie pour l'adhérent qui l'a mandaté : fonds reçus, puis reversés.

─────────────────────────────────────────────────────────────────────────────────
DÉCISION DU CABINET, 14 OCTOBRE 2026

Sous mandat de paiement, l'adhérent remet au cabinet les fonds de sa TVA ou de sa
CNPS, par mobile money ou au guichet ; le cabinet reverse et porte la quittance de
l'administration.

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* sans mandat de paiement, aucun appel de fonds ;
* « fonds reçus » n'est pas « payée » : l'échéance de l'adhérent le dit, et ne
  passe à PAYÉE qu'à la quittance de l'administration ;
* on ne reverse que des fonds reçus, et une fois ; des fonds ne se reçoivent
  qu'une fois ;
* chacun son geste : le comptable arrête et reverse, la caisse encaisse,
  l'adhérent remet ses fonds ;
* les faits traversent vers les Obligations par la boîte d'envoi.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text

from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

BATIMENT = NIU_DEMO["BATIMENT"]
COLOMBE = NIU_DEMO["COLOMBE"]
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
FONDS = f"/souscription/fonds-de-tiers/{BATIMENT}"
ECHEANCES = f"/obligations/dossiers/{BATIMENT}/mes-echeances"


def _en_tant_que(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


def _cnps_due(client) -> dict:
    """La période CNPS à régler du dossier, lue chez l'adhérent : pas de date en dur."""
    _en_tant_que(client, ADHERENT)
    return next(
        e
        for e in client.get(ECHEANCES).json()["echeances"]
        if e["code_obligation"] == "CNPS" and e["etat"] in ("EN_RETARD", "A_VENIR")
    )


def _carte(client, echeance) -> dict:
    _en_tant_que(client, ADHERENT)
    return next(
        e
        for e in client.get(ECHEANCES).json()["echeances"]
        if e["code_obligation"] == "CNPS" and e["periode_debut"] == echeance["periode_debut"]
    )


def _appeler(client, echeance, code="CNPS"):
    _en_tant_que(client, REVISEUR)
    return client.post(
        f"{FONDS}/appels",
        json={
            "code_obligation": code,
            "libelle": "Cotisations sociales CNPS",
            "periode_debut": echeance["periode_debut"],
            "periode_fin": echeance["periode_fin"],
            "montant": "184500",
        },
    )


def _publier(client):
    _en_tant_que(client, ADMINISTRATEUR)
    passage = client.post("/orchestration/publication")
    assert passage.status_code == 200, passage.text


def _au_guichet(client, appel, reference="Reçu de caisse 2026-0600"):
    _en_tant_que(client, CAISSIERE)
    return client.post(
        f"{FONDS}/appels/{appel['identifiant']}/encaissement",
        json={"reference_externe": reference},
    )


def _quittance(client, dossier=BATIMENT, par=REVISEUR, canal="DEPOT_CABINET") -> str:
    """Une pièce déposée au dossier : la quittance de l'administration, contenu unique."""
    _en_tant_que(client, par)
    contenu = b"%PDF-1.4\n% quittance " + uuid.uuid4().hex.encode() + b"\n%%EOF\n"
    fichier = client.post(
        "/collecte/fichiers",
        params={"entreprise": dossier},
        files={"fichier": ("quittance.pdf", contenu, "application/pdf")},
    )
    assert fichier.status_code in (200, 201), fichier.text
    depot = client.post(
        "/collecte/pieces",
        json={
            "entreprise": dossier,
            "canal": canal,
            "empreinte": fichier.json()["empreinte"],
        },
    )
    assert depot.status_code in (200, 201), depot.text
    return depot.json()["piece"]["identifiant"]


def _reverser(client, appel, quittance=None):
    quittance = quittance or _quittance(client)
    _en_tant_que(client, REVISEUR)
    return client.post(
        f"{FONDS}/appels/{appel['identifiant']}/reversement", json={"quittance": quittance}
    )


class TestLeMandat:
    def test_sans_mandat_de_paiement_aucun_appel(self, plateforme):
        """⚠️ Bâtiment Plus a mandaté la CNPS, pas la TVA : il règle sa TVA lui-même."""
        client = plateforme
        echeance = _cnps_due(client)

        refus = _appeler(client, echeance, code="TVA")

        assert refus.status_code == 409, refus.text
        assert "n'a pas mandaté" in refus.json()["detail"]

    def test_l_adherent_lit_ce_que_le_cabinet_paie_pour_lui_et_l_appel(self, plateforme):
        client = plateforme
        echeance = _cnps_due(client)
        assert _appeler(client, echeance).status_code == 201

        _en_tant_que(client, ADHERENT)
        vue = client.get(FONDS).json()

        assert "CNPS" in vue["obligations_mandatees"]
        assert "TVA" not in vue["obligations_mandatees"]
        ((appel,),) = [vue["appels"]]
        assert appel["etat"] == "A_REGLER"
        assert Decimal(appel["montant"]) == Decimal("184500")

    def test_un_autre_adherent_ne_le_lit_pas(self, plateforme):
        client = plateforme
        _en_tant_que(client, AUTRE_ADHERENT)
        assert client.get(FONDS).status_code == 404


class TestDeLaRemiseAuReversement:
    def test_fonds_recus_n_est_pas_payee_puis_la_quittance_la_rend_payee(self, plateforme):
        """⚠️ Le parcours entier, et les deux traversées par la boîte d'envoi."""
        client = plateforme
        echeance = _cnps_due(client)
        appel = _appeler(client, echeance).json()

        recu = _au_guichet(client, appel)
        assert recu.status_code == 200, recu.text
        assert recu.json()["etat"] == "FONDS_RECUS"
        _publier(client)
        carte = _carte(client, echeance)
        assert carte["etat"] == "FONDS_RECUS", carte
        assert carte["fonds"]["canal"] == "GUICHET"
        # 15 octobre 2026 : l'adhérent reçoit l'avis, une fois.
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            service_de_notification,
        )

        (avis,) = service_de_notification().derniers("adherent.fonds_recus")
        assert avis.destinataire == ADHERENT and avis.contexte["montant"] == "184 500"

        quittance = _quittance(client)
        reverse = _reverser(client, appel, quittance)
        assert reverse.status_code == 200, reverse.text
        _publier(client)
        carte = _carte(client, echeance)
        assert carte["etat"] == "PAYEE", carte
        assert carte["preuve"]["piece"] == quittance

    def test_la_quittance_est_une_piece_de_ce_dossier(self, plateforme):
        """⚠️ La quittance d'un autre adhérent, ou une pièce inventée, ne rend rien « payé »."""
        client = plateforme
        appel = _appeler(client, _cnps_due(client)).json()
        assert _au_guichet(client, appel).status_code == 200
        d_ailleurs = _quittance(client, COLOMBE, par=AUTRE_ADHERENT, canal="PORTAIL")

        assert _reverser(client, appel, d_ailleurs).status_code == 404
        assert _reverser(client, appel, "PJ-INVENTEE").status_code == 404
        _en_tant_que(client, REVISEUR)
        (vu,) = [
            a
            for a in client.get(FONDS).json()["appels"]
            if a["identifiant"] == appel["identifiant"]
        ]
        assert vu["etat"] == "FONDS_RECUS"

    def test_on_ne_reverse_que_des_fonds_recus(self, plateforme):
        client = plateforme
        appel = _appeler(client, _cnps_due(client)).json()

        assert _reverser(client, appel).status_code == 409

    def test_des_fonds_ne_se_recoivent_qu_une_fois(self, plateforme):
        client = plateforme
        appel = _appeler(client, _cnps_due(client)).json()
        assert _au_guichet(client, appel).status_code == 200

        assert _au_guichet(client, appel, "Second reçu").status_code == 409

    def test_par_mobile_money_la_notification_suffit(self, plateforme, moteur_test):
        client = plateforme
        echeance = _cnps_due(client)
        appel = _appeler(client, echeance).json()
        _en_tant_que(client, ADHERENT)
        demande = client.post(
            f"{FONDS}/appels/{appel['identifiant']}/paiement-en-ligne",
            json={"telephone": "+237655102030"},
        )
        assert demande.status_code == 202, demande.text
        # 15 octobre 2026 : un second clic pendant que le premier débit attend ne débite pas.
        second = client.post(
            f"{FONDS}/appels/{appel['identifiant']}/paiement-en-ligne",
            json={"telephone": "+237655102030"},
        )
        assert second.status_code == 409, second.text
        with moteur_test.connect() as connexion:
            (cle,) = connexion.execute(
                text(
                    "SELECT donnees->>'cle_idempotence' FROM paiement "
                    "WHERE donnees->>'nature' = 'FONDS_DE_TIERS'"
                )
            ).one()

        notification = client.post(
            "/souscription/notification/tara",
            json={
                "productId": cle,
                "paymentId": "TARA-FONDS-0001",
                "status": "SUCCESS",
                "amount": "184500",
                "phoneNumber": "237655102030",
            },
        )

        assert notification.status_code == 200, notification.text
        _publier(client)
        carte = _carte(client, echeance)
        assert carte["etat"] == "FONDS_RECUS" and carte["fonds"]["canal"] == "MOBILE_MONEY"


class TestChacunSonGeste:
    def test_l_adherent_n_arrete_ni_n_encaisse_ni_ne_reverse(self, plateforme):
        client = plateforme
        echeance = _cnps_due(client)
        appel = _appeler(client, echeance).json()
        _en_tant_que(client, ADHERENT)

        arrete = client.post(
            f"{FONDS}/appels",
            json={
                "code_obligation": "IRPP_SALAIRES",
                "libelle": "x",
                "periode_debut": echeance["periode_debut"],
                "periode_fin": echeance["periode_fin"],
                "montant": "1",
            },
        )
        encaisse = client.post(
            f"{FONDS}/appels/{appel['identifiant']}/encaissement",
            json={"reference_externe": "J'ai payé"},
        )
        reverse = client.post(
            f"{FONDS}/appels/{appel['identifiant']}/reversement", json={"quittance": "x"}
        )
        assert (arrete.status_code, encaisse.status_code, reverse.status_code) == (403, 403, 403)

    def test_la_caisse_n_arrete_pas_le_montant(self, plateforme):
        client = plateforme
        echeance = _cnps_due(client)
        _en_tant_que(client, CAISSIERE)

        refus = client.post(
            f"{FONDS}/appels",
            json={
                "code_obligation": "CNPS",
                "libelle": "x",
                "periode_debut": echeance["periode_debut"],
                "periode_fin": echeance["periode_fin"],
                "montant": "1",
            },
        )
        assert refus.status_code == 403, refus.text
