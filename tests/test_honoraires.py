"""Les honoraires d'un adhérent : son échéancier, son règlement en ligne, la caisse.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Paiement Tara ou manuel des frais, avec changement de statut. » Décidé le même
jour : l'adhérent voit son échéancier complet, règle par mobile money depuis son
espace, ou au guichet où la responsable de caisse le constate.

⚠️ CE QUI EST GARDÉ, ET OÙ UNE ERREUR COÛTERAIT

* une période ne se règle qu'une fois, quel que soit le canal ;
* l'adhérent ne lit que SON échéancier, et ne fixe pas son prix ;
* la caissière constate, elle ne fixe pas le montant ; la direction fixe, elle
  n'a pas à encaisser ;
* la notification de l'opérateur règle la période sans personne.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotReglementsDHonorairesMemoire,
)
from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    ContratDHonoraires,
    EtatEcheanceDHonoraires,
    PeriodeDejaReglee,
    PeriodiciteDHonoraires,
    ReglementDHonoraires,
    echeancier_d_honoraires,
)
from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = NIU_DEMO["BATIMENT"]
COLOMBE = NIU_DEMO["COLOMBE"]
AGRO = NIU_DEMO["AGRO"]
ADHERENT_BATIMENT = "jp.nkoa@batimentplus.cm"
CAISSIERE = "n.essama@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"


def _contrat(periodicite=PeriodiciteDHonoraires.MENSUELLE, debut=date(2026, 7, 1)):
    return ContratDHonoraires(
        niu="M000",
        denomination="ESSAI SARL",
        montant=Decimal("45000"),
        periodicite=periodicite,
        debut=debut,
        fixe_par="C-001",
        fixe_le=datetime(2026, 7, 1, 9),
    )


def _reglement(periode: date, canal=CanalDeReglement.GUICHET, reference="Reçu 1"):
    return ReglementDHonoraires(
        identifiant=f"RH-{periode}-{reference}",
        niu="M000",
        periode=periode,
        montant=Decimal("45000"),
        canal=canal,
        reference_externe=reference,
        constate_le=datetime(2026, 10, 1, 10),
        constate_par="C-009",
    )


class TestLEcheancier:
    def test_les_quatre_etats(self):
        echeances = echeancier_d_honoraires(
            _contrat(),
            [_reglement(date(2026, 7, 1)), _reglement(date(2026, 8, 1))],
            date(2026, 10, 14),
        )
        etats = {e.debut: e.etat for e in echeances}
        assert etats[date(2026, 7, 1)] is EtatEcheanceDHonoraires.REGLEE
        assert etats[date(2026, 9, 1)] is EtatEcheanceDHonoraires.EN_RETARD
        assert etats[date(2026, 10, 1)] is EtatEcheanceDHonoraires.A_REGLER
        assert etats[date(2026, 11, 1)] is EtatEcheanceDHonoraires.A_VENIR
        assert len([e for e in echeances if e.etat is EtatEcheanceDHonoraires.A_VENIR]) == 2

    def test_la_periode_court_jusqu_a_la_veille_de_la_suivante(self):
        (premiere, *_) = echeancier_d_honoraires(
            _contrat(PeriodiciteDHonoraires.TRIMESTRIELLE), [], date(2026, 7, 2)
        )
        assert (premiere.debut, premiere.fin) == (date(2026, 7, 1), date(2026, 9, 30))

    def test_une_periode_ne_se_regle_qu_une_fois(self):
        """⚠️ Mobile money et guichet pour la même période : le second est refusé."""
        reglements = DepotReglementsDHonorairesMemoire()
        reglements.enregistrer(_reglement(date(2026, 10, 1)))
        with pytest.raises(PeriodeDejaReglee):
            reglements.enregistrer(
                _reglement(date(2026, 10, 1), CanalDeReglement.MOBILE_MONEY, "TARA-1")
            )


def _du_mois(vue: dict, etat: str) -> dict:
    return next(e for e in vue["echeances"] if e["etat"] == etat)


@exige_postgresql
class TestLAdherentEtSonEcheancier:
    def test_il_lit_son_echeancier_avec_ce_qui_est_du(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT_BATIMENT)

        vue = client.get(f"/souscription/honoraires/{BATIMENT}")

        assert vue.status_code == 200, vue.text
        corps = vue.json()
        assert corps["contrat"]["periodicite"] == "MENSUELLE"
        etats = [e["etat"] for e in corps["echeances"]]
        assert {"REGLEE", "EN_RETARD", "A_REGLER", "A_VENIR"} <= set(etats)
        assert Decimal(corps["du"]) == Decimal("90000")
        assert corps["en_retard"] == 1

    def test_il_ne_lit_pas_celui_d_un_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT_BATIMENT)

        assert client.get(f"/souscription/honoraires/{COLOMBE}").status_code == 404

    def test_il_ne_fixe_pas_son_prix_et_n_encaisse_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, ADHERENT_BATIMENT)
        periode = _du_mois(client.get(f"/souscription/honoraires/{BATIMENT}").json(), "A_REGLER")

        prix = client.put(
            f"/souscription/honoraires/{BATIMENT}/contrat",
            json={"montant": "1000", "periodicite": "ANNUELLE", "debut": "2026-01-01"},
        )
        constat = client.post(
            f"/souscription/honoraires/{BATIMENT}/encaissement",
            json={"periode": periode["debut"], "reference_externe": "Je dis que j'ai payé"},
        )
        assert prix.status_code == 403, prix.text
        assert constat.status_code == 403, constat.text


@exige_postgresql
class TestLeReglementEnLigne:
    def test_la_notification_de_l_operateur_regle_la_periode_sans_personne(
        self, plateforme, moteur_test
    ):
        client = plateforme
        ouvrir_une_session(client, ADHERENT_BATIMENT)
        periode = _du_mois(client.get(f"/souscription/honoraires/{BATIMENT}").json(), "A_REGLER")

        demande = client.post(
            f"/souscription/honoraires/{BATIMENT}/paiement-en-ligne",
            json={"periode": periode["debut"], "telephone": "+237655102030"},
        )
        assert demande.status_code == 202, demande.text
        assert demande.json()["simule"] is True
        # ⚠️ Un clic n'est pas un paiement : la période est toujours due.
        assert _du_mois(client.get(f"/souscription/honoraires/{BATIMENT}").json(), "A_REGLER")

        with moteur_test.connect() as connexion:
            (cle,) = connexion.execute(
                text(
                    "SELECT donnees->>'cle_idempotence' FROM paiement "
                    "WHERE donnees->>'nature' = 'HONORAIRES'"
                )
            ).one()
        notification = client.post(
            "/souscription/notification/tara",
            json={
                "productId": cle,
                "paymentId": "TARA-HON-0001",
                "status": "SUCCESS",
                "amount": "45000",
                "phoneNumber": "237655102030",
            },
        )
        assert notification.status_code == 200, notification.text

        (reglee,) = [
            e
            for e in client.get(f"/souscription/honoraires/{BATIMENT}").json()["echeances"]
            if e["debut"] == periode["debut"]
        ]
        assert reglee["etat"] == "REGLEE"
        assert reglee["reglement"]["canal"] == "MOBILE_MONEY"
        assert reglee["reglement"]["reference_externe"] == "TARA-HON-0001"

        # Et la caisse le voit, sans l'avoir saisi : le rapprochement est automatique.
        client.cookies.clear()
        ouvrir_une_session(client, CAISSIERE)
        (ligne,) = [
            l_
            for l_ in client.get("/souscription/honoraires/caisse").json()
            if l_["niu"] == BATIMENT and l_["periode"] == periode["debut"]
        ]
        assert ligne["etat"] == "REGLEE" and ligne["canal"] == "MOBILE_MONEY"


@exige_postgresql
class TestLaCaisse:
    def test_elle_voit_le_retard_en_tete_et_le_constate_au_guichet(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CAISSIERE)

        caisse = client.get("/souscription/honoraires/caisse").json()
        assert caisse[0]["etat"] == "EN_RETARD", caisse[:2]
        retard = next(l_ for l_ in caisse if l_["niu"] == BATIMENT and l_["etat"] == "EN_RETARD")

        constat = client.post(
            f"/souscription/honoraires/{BATIMENT}/encaissement",
            json={"periode": retard["periode"], "reference_externe": "Reçu de caisse 2026-0500"},
        )
        assert constat.status_code == 200, constat.text
        (reglee,) = [e for e in constat.json()["echeances"] if e["debut"] == retard["periode"]]
        assert reglee["etat"] == "REGLEE" and reglee["reglement"]["canal"] == "GUICHET"
        assert Decimal(reglee["reglement"]["montant"]) == Decimal("45000")

    def test_une_periode_reglee_ne_se_constate_pas_deux_fois(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CAISSIERE)
        retard = next(
            l_
            for l_ in client.get("/souscription/honoraires/caisse").json()
            if l_["niu"] == BATIMENT and l_["etat"] == "EN_RETARD"
        )
        corps = {"periode": retard["periode"], "reference_externe": "Reçu 1"}

        assert (
            client.post(f"/souscription/honoraires/{BATIMENT}/encaissement", json=corps).status_code
            == 200
        )
        second = client.post(f"/souscription/honoraires/{BATIMENT}/encaissement", json=corps)
        assert second.status_code == 409, second.text

    def test_elle_ne_fixe_pas_le_montant(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CAISSIERE)
        refus = client.put(
            f"/souscription/honoraires/{AGRO}/contrat",
            json={"montant": "1", "periodicite": "MENSUELLE", "debut": "2026-01-01"},
        )
        assert refus.status_code == 403, refus.text


@exige_postgresql
class TestLaDirectionFixeLeContrat:
    def test_un_contrat_fixe_produit_son_echeancier(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, DIRECTION)

        fixe = client.put(
            f"/souscription/honoraires/{AGRO}/contrat",
            json={"montant": "150000", "periodicite": "TRIMESTRIELLE", "debut": "2026-01-01"},
        )

        assert fixe.status_code == 200, fixe.text
        corps = fixe.json()
        assert corps["contrat"]["denomination"] == "AGRO-NKOLO SA"
        assert corps["contrat"]["fixe_par"] == "C-001"
        assert corps["echeances"][0]["debut"] == "2026-01-01"

    def test_pas_de_contrat_pour_une_entreprise_inconnue(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, DIRECTION)
        refus = client.put(
            "/souscription/honoraires/M999999999999X/contrat",
            json={"montant": "1000", "periodicite": "MENSUELLE", "debut": "2026-01-01"},
        )
        assert refus.status_code == 404, refus.text
