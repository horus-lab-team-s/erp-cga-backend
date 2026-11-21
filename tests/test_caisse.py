"""La responsable de caisse constate les règlements, et rien d'autre.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROTÈGE

Le cabinet l'a décidé le 14 octobre 2026 : « c'est une personne, généralement la
responsable de caisse, qui doit encaisser les versements ». Jusque-là le geste
exigeait `GERER_COMPTES`, donc l'administrateur. Celle qui tient la caisse
n'avait pas le droit de dire qu'un client avait payé.

Un rôle nouveau, `CAISSE`, porte une permission nouvelle, `ENCAISSER`, et elle
seule.

⚠️ UN RÔLE NOUVEAU SE JUGE SUR CE QU'IL N'OUVRE PAS.

Les deux premiers cas disent que la caissière travaille. Tous les autres disent
où elle s'arrête, et où s'arrêtent les autres devant sa caisse : c'est là qu'une
erreur coûterait, et c'est là qu'elle ne se verrait pas à l'écran.

Éprouvé dans les deux sens : en remettant `GERER_COMPTES` sur l'encaissement,
les deux premiers rougissent ; en donnant `LIRE_PROSPECT` à la caisse, le
troisième ; en donnant `ENCAISSER` à la chargée de clientèle, le quatrième.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

# ⚠️ Le MODULE, et non la classe : importer une classe `Test…` dans ce fichier la
# ferait collecter une seconde fois par pytest, et ses cas tourneraient en double.
from tests import test_recette_du_parcours as recette
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CAISSIERE = "n.essama@cga-brcg.cm"
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
#: Elle qualifie et arrête le prix. Elle ne doit donc pas encaisser.
CHARGEE_DE_CLIENTELE = "p.moukouri@cga-brcg.cm"


def _vente_acceptee(client) -> tuple[str, str]:
    """Une création d'entreprise acceptée, non réglée : (dossier, proforma)."""
    return recette.TestLeReglementNotifieParLOperateur()._dossier_accepte(client)


class TestLaCaissiereTravaille:
    def test_elle_voit_ce_qui_est_du(self, plateforme):
        client = plateforme
        reference, numero = _vente_acceptee(client)
        ouvrir_une_session(client, CAISSIERE)

        caisse = client.get("/acquisition/caisse")

        assert caisse.status_code == 200, caisse.text
        (ligne,) = [r for r in caisse.json() if r["dossier"] == reference]
        assert ligne["proforma"] == numero
        assert ligne["etat"] == "A_ENCAISSER"
        assert Decimal(ligne["montant"]) == Decimal("250000")
        assert ligne["client"] == "Boulangerie du Wouri"
        # Une création n'ouvre pas d'espace : aucune adresse ne lui sera demandée.
        assert ligne["ouvre_un_espace"] is False

    def test_elle_constate_un_versement_et_la_ligne_change_d_etat(self, plateforme):
        client = plateforme
        reference, numero = _vente_acceptee(client)
        ouvrir_une_session(client, CAISSIERE)

        constat = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"reference_externe": "Reçu de caisse n° 2026-0412"},
        )
        assert constat.status_code == 200, constat.text
        assert constat.json()["etat"] == "PAYEE"

        (ligne,) = [
            r for r in client.get("/acquisition/caisse").json() if r["dossier"] == reference
        ]
        assert ligne["etat"] == "ENCAISSE"
        assert ligne["payee_le"] is not None


class TestEtElleNeVoitRienDAutre:
    def test_ni_les_dossiers_ni_les_documents_ni_les_livres(self, plateforme):
        client = plateforme
        reference, _ = _vente_acceptee(client)
        ouvrir_une_session(client, CAISSIERE)

        for chemin in (
            "/acquisition/dossiers",
            f"/acquisition/dossiers/{reference}",
            f"/acquisition/dossiers/{reference}/qualification",
            "/acquisition/compositions-proforma",
            "/comptabilite/dossiers/M081234567890P/balance?exercice=2026",
        ):
            reponse = client.get(chemin)
            assert reponse.status_code in (403, 404), (
                f"la caisse a ouvert {chemin} : {reponse.status_code}"
            )
            assert reponse.status_code != 200


class TestCelleQuiChiffreNEncaissePas:
    def test_la_chargee_de_clientele_n_a_pas_la_caisse(self, plateforme):
        client = plateforme
        _, numero = _vente_acceptee(client)
        ouvrir_une_session(client, CHARGEE_DE_CLIENTELE)

        assert client.get("/acquisition/caisse").status_code == 403
        refus = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"reference_externe": "essai"},
        )
        assert refus.status_code == 403, refus.text


class TestLAdministrateurGardeLeGeste:
    def test_il_voit_la_caisse_et_peut_constater(self, plateforme):
        """Sans cela, un cabinet qui n'a nommé personne à la caisse n'aurait plus
        aucun moyen de constater un règlement."""
        client = plateforme
        _, numero = _vente_acceptee(client)
        ouvrir_une_session(client, ADMINISTRATEUR)

        assert client.get("/acquisition/caisse").status_code == 200
        constat = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"reference_externe": "Virement UBA 0098"},
        )
        assert constat.status_code == 200, constat.text
