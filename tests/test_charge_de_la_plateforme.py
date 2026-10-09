"""La charge de la PLATEFORME : ce que le serveur consomme et encaisse.

⚠️ NE PAS CONFONDRE AVEC `test_charge.py`, QUI PORTE SUR TOUT AUTRE CHOSE.

« Charge » a deux sens dans ce produit, et c'est un piège réel — j'ai écrasé le
premier fichier en écrivant le second, et vingt-cinq cas ont disparu sans que
la suite ne baisse d'un cran visible :

  · la **charge d'un dossier** — le poids de travail qu'un adhérent représente
    pour le cabinet, évalué sur une grille. C'est `test_charge.py`, contexte
    Portefeuille, et il est plus ancien ;
  · la **charge de la plateforme** — mémoire, processeur, connexions, latences.
    C'est ce fichier-ci.

Le nom long est donc délibéré.
La mesure de charge, éprouvée sur ce qui la rend utile — et sur ce qui la rendrait nuisible.

─────────────────────────────────────────────────────────────────────────────────
⚠️ UN ÉCRAN DE SURVEILLANCE NE DOIT JAMAIS ÊTRE LA CAUSE D'UNE PANNE.

C'est la première exigence, avant l'exactitude : une mesure qui lève, qui fuit
en mémoire, ou qui fait tomber l'application parce que `/proc` est masqué serait
pire que pas de mesure du tout.

⚠️ ET ELLE NE DOIT PAS SE MESURER ELLE-MÊME. La sonde de santé est appelée
toutes les cinq secondes par Docker et par Kubernetes : comptée, elle
représenterait la moitié des mesures et écraserait les latences réelles du
travail du cabinet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.infrastructure.charge import (
    CHEMINS_IGNORES,
    TAILLE_DE_L_ANNEAU,
    MesureDesRequetes,
    Passage,
    charge_de_la_base,
    charge_du_processus,
)
from app.main import app

ADMINISTRATEUR = "s.onana@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def _connecter(client: TestClient, courriel: str) -> TestClient:
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


class TestLAnneau:
    def test_il_est_borne_et_ne_fuit_pas(self):
        """⚠️ LE CAS QUI COMPTE LE PLUS. Une liste qui grandit à chaque requête
        est une fuite de mémoire déguisée en mesure : sur un serveur qui tourne
        trois mois, elle finit par peser plus que l'application."""
        mesures = MesureDesRequetes(taille=10)
        for rang in range(1000):
            mesures.noter(Passage(route="/x", methode="GET", statut=200, millisecondes=rang))

        instantane = mesures.instantane()
        assert instantane["requetes_mesurees"] == 10
        # ⚠️ Le compteur TOTAL, lui, n'est pas borné : il compte depuis le
        # démarrage, et c'est ce qu'on veut lire.
        assert instantane["requetes_depuis_le_demarrage"] == 1000

    def test_il_retient_mille_passages_par_defaut(self):
        assert TAILLE_DE_L_ANNEAU == 1000

    def test_les_centiles_se_lisent_sur_ce_qui_est_retenu(self):
        mesures = MesureDesRequetes(taille=100)
        for rang in range(100):
            mesures.noter(Passage(route="/x", methode="GET", statut=200, millisecondes=rang))

        instantane = mesures.instantane()
        assert instantane["mediane_ms"] == 50.0
        assert instantane["p95_ms"] == 95.0
        assert instantane["max_ms"] == 99.0

    def test_un_anneau_vide_ne_leve_pas(self):
        """Au démarrage, aucune requête n'a encore été servie."""
        instantane = MesureDesRequetes().instantane()
        assert instantane["mediane_ms"] == 0.0
        assert instantane["taux_d_erreur"] == 0.0
        assert instantane["routes_les_plus_lentes"] == []

    def test_le_taux_d_erreur_ne_compte_pas_les_refus(self):
        """⚠️ CE QUE LA PLATEFORME RATE N'EST PAS CE QU'ELLE REFUSE.

        Les deux étaient comptés ensemble. Mesuré le 28 septembre sur la pile de
        démonstration, après une recette complète :

            requêtes 454 · 2xx 322 · 4xx 132 · 5xx 0
            taux d'erreur affiché : 29,1 %   (seuil d'alerte console : 2 %)

        Zéro panne, et un panneau rouge en permanence. Or ce produit **refuse par
        métier** : 403 pour un comptable écarté de l'acquisition, 409 pour un
        dépôt aux pièces incomplètes, 422 pour un prix hors intervalle sans motif.
        Ce sont des succès du produit, qui étaient comptés comme ses échecs.
        """
        mesures = MesureDesRequetes()
        for statut in (200, 200, 404, 500):
            mesures.noter(Passage(route="/x", methode="GET", statut=statut, millisecondes=1))

        instantane = mesures.instantane()
        assert instantane["taux_d_erreur"] == 0.25, "le 404 est compté comme une panne"
        assert instantane["erreurs_depuis_le_demarrage"] == 1
        assert instantane["par_statut"] == {"2xx": 2, "4xx": 1, "5xx": 1}

    def test_les_refus_sont_comptes_a_part_et_non_perdus(self):
        """⚠️ **Séparés, pas supprimés.** Le raisonnement d'origine tenait à ceci :
        « un pic de 404 est un symptôme ». Il l'est, et le nombre reste rendu.

        Ce que ce raisonnement supposait à tort, c'est qu'un taux global détecte un
        pic. Il détecte un NIVEAU. Les fusionner ne donnait donc pas le signal
        qu'on croyait acheter ; cela rendait seulement le seuil inutilisable.
        """
        mesures = MesureDesRequetes()
        for statut in (200, 200, 404, 500):
            mesures.noter(Passage(route="/x", methode="GET", statut=statut, millisecondes=1))

        instantane = mesures.instantane()
        assert instantane["taux_de_refus"] == 0.25
        assert instantane["refus_depuis_le_demarrage"] == 1

    def test_une_recette_complete_ne_declare_aucune_panne(self):
        """Le cas mesuré sur la pile, reproduit au banc.

        Un produit qui refuse beaucoup et ne tombe jamais doit afficher un taux
        d'erreur NUL. C'est la seule façon que le seuil de 2 % veuille dire
        quelque chose le jour où un 500 apparaît.
        """
        mesures = MesureDesRequetes()
        for statut in [200] * 322 + [403] * 60 + [409] * 40 + [422] * 32:
            mesures.noter(Passage(route="/x", methode="GET", statut=statut, millisecondes=1))

        instantane = mesures.instantane()
        assert instantane["taux_d_erreur"] == 0.0
        assert instantane["taux_de_refus"] == round(132 / 454, 4)

    def test_les_routes_lentes_sont_classees_par_MEDIANE(self):
        """Un maximum est souvent un incident isolé ; une médiane est un problème."""
        mesures = MesureDesRequetes()
        for _ in range(10):
            mesures.noter(Passage(route="/lente", methode="GET", statut=200, millisecondes=100))
        mesures.noter(Passage(route="/rapide", methode="GET", statut=200, millisecondes=900))
        for _ in range(9):
            mesures.noter(Passage(route="/rapide", methode="GET", statut=200, millisecondes=1))

        premiere = mesures.instantane()["routes_les_plus_lentes"][0]
        assert premiere["route"] == "GET /lente"


class TestLesLecturesSysteme:
    def test_la_charge_du_processus_ne_leve_jamais(self):
        """⚠️ Sur un système sans `/proc`, ou dans un bac à sable qui le masque,
        la lecture échoue. Un écran de surveillance qui tomberait faute de
        pouvoir se mesurer serait un comble : on rend `null`."""
        charge = charge_du_processus()
        assert set(charge) >= {"memoire_resident_mo", "processeurs", "demarre_depuis_s"}

    def test_sans_base_la_reponse_le_dit_au_lieu_de_lever(self):
        resultat = charge_de_la_base(None)
        assert resultat["joignable"] is False
        assert "mémoire" in resultat["motif"]


class TestLaRoute:
    def test_elle_est_reservee_a_l_administration(self, client):
        """Les routes les plus lentes dessinent la forme de l'application, et le
        nombre de requêtes dit l'activité du cabinet. Ce n'est pas le travail
        d'un comptable."""
        comptable = _connecter(client, COMPTABLE)
        assert comptable.get("/transverse/charge").status_code == 403

    def test_elle_repond_a_l_administrateur(self, client):
        administrateur = _connecter(client, ADMINISTRATEUR)

        reponse = administrateur.get("/transverse/charge")

        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert set(corps) == {
            "portee_de_la_mesure",
            "processus",
            "base_de_donnees",
            "requetes",
        }

    def test_elle_annonce_la_portee_de_ce_qu_elle_mesure(self, client):
        """⚠️ Par instance, sans historique. Un exploitant qui croirait lire
        l'ensemble du parc conclurait de travers — la réponse le dit
        elle-même."""
        administrateur = _connecter(client, ADMINISTRATEUR)

        portee = administrateur.get("/transverse/charge").json()["portee_de_la_mesure"]

        assert "instance" in portee
        assert "historique" in portee

    def test_elle_ne_se_mesure_pas_elle_meme(self):
        """La sonde de santé est appelée toutes les cinq secondes par Docker et
        par Kubernetes : comptée, elle écraserait les latences réelles."""
        assert "/sante" in CHEMINS_IGNORES
        assert "/transverse/charge" in CHEMINS_IGNORES
