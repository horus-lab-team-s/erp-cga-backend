"""Les pénalités de retard, décidées par un responsable (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
Réunion du 4 novembre 2026, demandes 12 et 13 : « des délais clairs et, au besoin, des
pénalités » ; « une proforma expirée : appliquer une pénalité ou refacturer ».

⚠️ CE QUI EST GARDÉ
* une pénalité s'inflige à la main, avec une explication, par la création ou la direction ;
  ni la caisse ni le commerce ne l'infligent ;
* sans montant saisi, le montant par défaut du motif ; sans montant par défaut, refus ;
* le client en est prévenu par courriel ;
* la caisse la voit à encaisser, l'encaisse, et ne la voit plus ;
* le super administrateur la lève avec un motif, mais pas une pénalité déjà versée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.contextes.souscription.domaine.penalites import (
    MotifDePenalite,
    PenaliteInfligee,
    PenaliteRefusee,
    lever,
    total_des_penalites_actives,
)
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import SUPER_ADMIN, _renforcer

DIRECTRICE = "b.mballa@cga-brcg.cm"
FORMALITES = "p.moukouri@cga-brcg.cm"
CAISSIERE = "n.essama@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
INSTANT = datetime(2026, 11, 9, 8, 0)
EXPLICATION = "Originaux prêts depuis le 1er octobre ; délai de retrait de 30 jours dépassé."


def _penalite(numero, montant="10000", **autres):
    return PenaliteInfligee(
        numero=numero,
        motif=MotifDePenalite.RETARD_RETRAIT_ORIGINAUX,
        montant=Decimal(montant),
        explication=EXPLICATION,
        infligee_par="C-001",
        infligee_le=INSTANT,
        **autres,
    )


class TestLeDomaine:
    def test_une_pénalité_levée_ne_compte_plus(self):
        deux = (_penalite(1), _penalite(2, "5000"))
        assert total_des_penalites_actives(deux) == Decimal(15000)
        levees = lever(
            deux, 2, motif="erreur du cabinet", par="C-099", le=INSTANT, deja_versees=Decimal(0)
        )
        assert total_des_penalites_actives(levees) == Decimal(10000)
        assert levees[1].motif_de_levee == "erreur du cabinet"

    def test_on_ne_leve_ni_deux_fois_ni_ce_qui_est_verse(self):
        deux = (_penalite(1), _penalite(2, "5000"))
        with pytest.raises(PenaliteRefusee, match="déjà réglée"):
            lever(deux, 1, motif="geste", par="C-099", le=INSTANT, deja_versees=Decimal(15000))
        levees = lever(deux, 2, motif="geste", par="C-099", le=INSTANT, deja_versees=Decimal(0))
        with pytest.raises(PenaliteRefusee, match="déjà levée"):
            lever(levees, 2, motif="geste", par="C-099", le=INSTANT, deja_versees=Decimal(0))
        with pytest.raises(PenaliteRefusee, match="aucune pénalité"):
            lever(deux, 9, motif="geste", par="C-099", le=INSTANT, deja_versees=Decimal(0))


def _en(client, courriel):
    client.cookies.clear()
    ouvrir_une_session(client, courriel)


#: Le second facteur du super administrateur s'enrôle une fois par cas ; on le réutilise.
_SECRETS: dict[int, str] = {}


@pytest.fixture(autouse=True)
def _oublier_les_secrets():
    _SECRETS.clear()
    yield
    _SECRETS.clear()


def _en_super_administrateur(client):
    _en(client, SUPER_ADMIN)
    secret = _SECRETS.get(id(client))
    _SECRETS[id(client)] = _renforcer(client, secret, decalage=30 if secret else 0)


def _un_dossier(client) -> str:
    _en(client, DIRECTRICE)
    saisie = client.post(
        "/acquisition/demandes/au-cabinet",
        json={
            "nom": "Ngono Couture",
            "telephone": "699445566",
            "courriel": "ngono@exemple.cm",
            "service_souhaite": "CREATION",
            "canal_prefere": "APPEL",
        },
    )
    assert saisie.status_code == 201, saisie.text
    return saisie.json()["reference"]


@exige_postgresql
class TestParLesRoutes:
    def test_du_constat_a_l_encaissement(self, plateforme):
        from app.contextes.transverse.api import service_de_notification

        client = plateforme
        with horloge_figee(INSTANT):
            dossier = _un_dossier(client)
            url = f"/acquisition/dossiers/{dossier}/penalites"

            # Ni la caisse, ni la comptabilité n'infligent.
            for qui in (CAISSIERE, COMPTABLE):
                _en(client, qui)
                refus = client.post(url, json={"motif": "AUTRE", "explication": EXPLICATION})
                assert refus.status_code == 403, (qui, refus.text)

            # Sans montant, ni par défaut : refus lisible.
            _en(client, FORMALITES)
            sans = client.post(
                url, json={"motif": "RETARD_RETRAIT_ORIGINAUX", "explication": EXPLICATION}
            )
            assert sans.status_code == 422 and "montant" in sans.json()["detail"]

            infligee = client.post(
                url,
                json={
                    "motif": "RETARD_RETRAIT_ORIGINAUX",
                    "montant": "10000",
                    "explication": EXPLICATION,
                },
            )
            assert infligee.status_code == 201, infligee.text
            corps = infligee.json()
            assert corps["reste_du"] == "10000" and corps["client_prevenu"] is True
            (message,) = service_de_notification().derniers("penalite.infligee")
            assert message.destinataire == "ngono@exemple.cm"

            _en(client, CAISSIERE)
            caisse = client.get("/acquisition/caisse").json()
            a_encaisser = [
                ligne
                for ligne in caisse
                if ligne["dossier"] == dossier and ligne["objet"] == "PENALITE_DE_RETARD"
            ]
            assert [ligne["montant"] for ligne in a_encaisser] == ["10000"], caisse
            recue = client.post(
                f"/acquisition/dossiers/{dossier}/penalite-de-retard/encaissement",
                json={"reference_externe": "REC-0042"},
            )
            assert recue.status_code == 201, recue.text
            assert recue.json()["penalites_de_retard_versees"] == "10000"
            caisse = client.get("/acquisition/caisse").json()
            assert not [
                ligne
                for ligne in caisse
                if ligne["dossier"] == dossier and ligne["objet"] == "PENALITE_DE_RETARD"
            ]
            # Plus rien de dû : pas de second encaissement.
            assert (
                client.post(
                    f"/acquisition/dossiers/{dossier}/penalite-de-retard/encaissement",
                    json={"reference_externe": "REC-0043"},
                ).status_code
                == 409
            )

            # Versée, elle ne se lève plus.
            _en_super_administrateur(client)
            levee = client.post(
                f"{url}/1/levee", json={"motif": "Le client était hospitalisé ce mois-là"}
            )
            assert levee.status_code == 409 and "déjà réglée" in levee.json()["detail"]

    def test_le_montant_par_defaut_et_la_levee(self, plateforme):
        client = plateforme
        with horloge_figee(INSTANT):
            dossier = _un_dossier(client)
            _en_super_administrateur(client)
            regle = client.put(
                "/souscription/versements/regle",
                json={
                    "seuil_demarrage_pourcent": 100,
                    "penalites_de_retard": {"PROFORMA_EXPIREE": "15000"},
                    "motif": "Pénalités validées en réunion de direction",
                },
            )
            assert regle.status_code == 200, regle.text
            inconnu = client.put(
                "/souscription/versements/regle",
                json={
                    "seuil_demarrage_pourcent": 100,
                    "penalites_de_retard": {"INVENTE": "15000"},
                    "motif": "Pénalités validées en réunion de direction",
                },
            )
            assert inconnu.status_code == 422

            _en(client, DIRECTRICE)
            infligee = client.post(
                f"/acquisition/dossiers/{dossier}/penalites",
                json={
                    "motif": "PROFORMA_EXPIREE",
                    "explication": "Proforma du 1er octobre expirée.",
                },
            )
            assert infligee.status_code == 201, infligee.text
            assert infligee.json()["penalites"][0]["montant"] == "15000"

            # La direction n'en lève pas : le super administrateur, avec un motif.
            levee_url = f"/acquisition/dossiers/{dossier}/penalites/1/levee"
            motif = {"motif": "Erreur du cabinet sur la date d'envoi"}
            assert client.post(levee_url, json=motif).status_code == 403
            _en_super_administrateur(client)
            levee = client.post(levee_url, json=motif)
            assert levee.status_code == 200, levee.text
            assert levee.json()["reste_du"] == "0"
            _en(client, DIRECTRICE)
            fiche = client.get(f"/acquisition/dossiers/{dossier}").json()
            assert fiche["penalites"][0]["levee_par"] is not None
