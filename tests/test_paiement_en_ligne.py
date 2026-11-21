"""Le client règle lui-même sa proposition, et l'opérateur fait le reste.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROTÈGE

Le cabinet, le 14 octobre 2026 : « un lien de paiement Tara Money pour le montant
mentionné par le responsable, avec auto-validation si le paiement est effectif ».
La cliente acceptait sa proposition, puis attendait qu'on la rappelle pour payer.

Elle clique désormais « Payer par mobile money » sur la page de sa proposition.

⚠️ TROIS CHOSES QUE CE GESTE NE DOIT PAS FAIRE.

Il est public : il ne doit rien déclencher sans le lien personnel. Il demande un
débit : il ne doit rien constater lui-même. Et il s'adresse à un client qui a
accepté : il ne doit ni sonner chez quelqu'un qui ne s'est engagé à rien, ni se
rejouer sur un dossier réglé.

Éprouvé dans les deux sens, trois mutants : sans la vérification du sceau, le
premier cas rougit ; sans le contrôle de l'accord, celui de la proposition non
acceptée ; sans le refus d'un dossier réglé, le dernier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from sqlalchemy import text

from tests import test_document_de_la_proforma as document
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CAISSIERE = "n.essama@cga-brcg.cm"


def _proposition_emise(client) -> dict:
    emise = document._emettre(client)
    assert emise.status_code == 201, emise.text
    return emise.json()


def _lien(proposition: dict, **surcharges) -> dict:
    return {
        "sceau": proposition["lien_acceptation"],
        "expire_le": proposition["expire_le"],
        "version": proposition["version"],
        **surcharges,
    }


def _accepter(client, proposition: dict) -> None:
    accord = client.post(
        f"/acquisition/proformas/{proposition['numero']}/acceptation",
        json=_lien(proposition, identite_declaree="Abena Ndzana, gérante"),
    )
    assert accord.status_code in (200, 201), accord.text


def _payer(client, proposition: dict, **surcharges):
    return client.post(
        f"/acquisition/proformas/{proposition['numero']}/paiement-en-ligne",
        json=_lien(proposition, **surcharges),
    )


def _paiements(moteur_test) -> list:
    with moteur_test.connect() as connexion:
        return list(
            connexion.execute(
                text("SELECT identifiant, donnees->>'cle_idempotence' AS cle FROM paiement")
            )
        )


def _etat_du_dossier(moteur_test) -> str:
    with moteur_test.connect() as connexion:
        return connexion.execute(
            text("SELECT donnees->>'etat' FROM dossier_commercial")
        ).scalar_one()


class TestSansLeLienPersonnelRienNePart:
    def test_un_sceau_faux_ne_fait_sonner_personne(self, plateforme, moteur_test):
        client = plateforme
        proposition = _proposition_emise(client)
        _accepter(client, proposition)

        refus = _payer(client, proposition, sceau="0" * 64)

        assert refus.status_code == 422, refus.text
        assert _paiements(moteur_test) == []


class TestLeClientPaieEtLOperateurConstate:
    def test_le_clic_demande_le_debit_et_ne_constate_rien(self, plateforme, moteur_test):
        client = plateforme
        proposition = _proposition_emise(client)
        _accepter(client, proposition)

        demande = _payer(client, proposition)

        assert demande.status_code == 202, demande.text
        corps = demande.json()
        assert corps["accepte"] is True
        # ⚠️ Sans identifiants marchands, la page doit le DIRE au client.
        assert corps["simule"] is True
        # Les quatre derniers chiffres : la page est ouverte par un simple lien.
        assert corps["telephone"].startswith("…") and len(corps["telephone"]) == 5
        assert len(_paiements(moteur_test)) == 1
        # ⚠️ RIEN N'EST ENCAISSÉ : le client n'a pas encore saisi son code.
        assert _etat_du_dossier(moteur_test) == "ACCEPTEE"

    def test_la_notification_de_l_operateur_regle_le_dossier_toute_seule(
        self, plateforme, moteur_test
    ):
        """⚠️ L'AUTO-VALIDATION, ET LE RAPPROCHEMENT DE LA CAISSE.

        Personne ne confirme. La notification se rapproche du paiement que la
        plateforme a créé ; le dossier passe à « payée », la page du client le
        lit, et la ligne de la caissière a changé d'état sans elle.
        """
        client = plateforme
        proposition = _proposition_emise(client)
        _accepter(client, proposition)
        assert _payer(client, proposition).status_code == 202
        ((_, cle),) = _paiements(moteur_test)

        notification = client.post(
            "/souscription/notification/tara",
            json={
                "productId": cle,
                "paymentId": "TARA-EN-LIGNE-0001",
                "status": "SUCCESS",
                "amount": str(proposition["montant"]),
                "phoneNumber": "237699114499",
            },
        )
        assert notification.status_code == 200, notification.text
        assert notification.json()["rapproche"] is True, notification.text

        assert _etat_du_dossier(moteur_test) == "PAYEE"
        relue = client.get(
            f"/acquisition/proformas/{proposition['numero']}/consultation",
            params={
                "version": proposition["version"],
                "expire_le": proposition["expire_le"],
                "sceau": proposition["lien_acceptation"],
            },
        )
        assert relue.status_code == 200, relue.text
        assert relue.json()["payee"] is True

        ouvrir_une_session(client, CAISSIERE)
        (ligne,) = [
            r for r in client.get("/acquisition/caisse").json()
            if r["proforma"] == proposition["numero"]
        ]
        assert ligne["etat"] == "ENCAISSE"


class TestSeulementApresLAccordEtUneSeuleFois:
    def test_une_proposition_non_acceptee_ne_se_paie_pas(self, plateforme, moteur_test):
        client = plateforme
        proposition = _proposition_emise(client)

        refus = _payer(client, proposition)

        assert refus.status_code == 409, refus.text
        assert _paiements(moteur_test) == []

    def test_un_dossier_regle_ne_se_redemande_pas(self, plateforme, moteur_test):
        client = plateforme
        proposition = _proposition_emise(client)
        _accepter(client, proposition)
        ouvrir_une_session(client, CAISSIERE)
        constat = client.post(
            f"/acquisition/proformas/{proposition['numero']}/encaissement",
            json={"reference_externe": "Reçu de caisse n° 1"},
        )
        assert constat.status_code == 200, constat.text

        refus = _payer(client, proposition)

        assert refus.status_code == 409, refus.text
        assert "déjà réglée" in refus.text
        assert _paiements(moteur_test) == []
