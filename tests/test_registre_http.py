"""La route du registre : ce qu'elle publie, et à qui elle refuse de le publier.

⚠️ **Le graphe des dépendances d'une plateforme est une carte de ses points de
rupture.** Il dit quel service arrêter pour tout arrêter. Ce n'est pas une donnée
publique, et ces cas gardent ce refus autant que le contenu.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import creer_application


@pytest.fixture(scope="module")
def application():
    """Une seule application pour tout le module.

    ⚠️ `reinitialiser_atelier()` vide les caches, dont le magasin de sessions :
    l'appeler par fixture de client révoquerait la session de la précédente.
    """
    from app.contextes.transverse.adaptateurs.entrant.dependances import (
        reinitialiser_atelier,
    )

    reinitialiser_atelier()
    return creer_application()


@pytest.fixture(scope="module")
def client(application) -> TestClient:
    return TestClient(application)


@pytest.fixture(scope="module")
def administrateur(application) -> TestClient:
    """Un client connecté en administration du cabinet."""
    connecte = TestClient(application)
    reponse = connecte.post(
        "/transverse/session",
        json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": MOT_DE_PASSE_DEMO},
    )
    assert reponse.status_code == 200, reponse.text
    return connecte


class TestElleN_estPasPublique:
    def test_sans_session_elle_refuse(self, client):
        """Le graphe dit quel service arrêter pour tout arrêter."""
        assert client.get("/transverse/services").status_code in (401, 403)


class TestCeQuEllePublie:
    def test_les_quatorze_services_avec_leurs_trois_etats(self, administrateur):
        corps = administrateur.get("/transverse/services").json()
        assert len(corps["services"]) == 14
        for fiche in corps["services"]:
            # ⚠️ Trois champs distincts, jamais un seul. Un champ unique mentirait
            # dans les deux sens : un service complet dont la base est tombée
            # passerait pour incomplet, et un service à peine commencé mais dont le
            # processus tourne passerait pour opérationnel.
            assert fiche["construction"]
            assert fiche["execution"]
            assert "entraine" in fiche

    def test_elle_dit_ce_qui_tombe_avec_le_referentiel(self, administrateur):
        """La réponse qui manquait à « faut-il réveiller quelqu'un ? ».

        « Le Référentiel ne répond plus » n'apprend rien. « Le Référentiel ne répond
        plus, et douze services en dépendent » l'apprend.
        """
        corps = administrateur.get("/transverse/services").json()
        referentiel = next(s for s in corps["services"] if s["nom"] == "referentiel")
        assert len(referentiel["entraine"]) == 12

    def test_le_pilotage_n_entraine_personne(self, administrateur):
        """Il lit tout le monde et personne ne le lit : son arrêt n'interrompt
        aucune production."""
        corps = administrateur.get("/transverse/services").json()
        pilotage = next(s for s in corps["services"] if s["nom"] == "pilotage")
        assert pilotage["entraine"] == []

    def test_la_joignabilite_est_constatee_sur_l_application_montee(
        self, administrateur
    ):
        """⚠️ Et non sur la présence d'un fichier de routes.

        Un `routes_http.py` présent mais dont le routeur n'est monté nulle part ne
        rend le service joignable par personne. Le cas réel est celui des Tenants,
        pilotés par le Transverse et par l'ordonnanceur, qui n'exposent aucune route
        de leur propre chef.
        """
        corps = administrateur.get("/transverse/services").json()
        tenants = next(s for s in corps["services"] if s["nom"] == "tenants")
        assert tenants["prefixes"] == []
        assert tenants["construction"] != "EN_SERVICE"
        assert "tenants" in corps["en_construction"]

    def test_le_referentiel_repond_dans_un_processus_ordinaire(self, administrateur):
        """Sa sonde est réelle : elle vérifie que des paramètres sont chargés."""
        corps = administrateur.get("/transverse/services").json()
        referentiel = next(s for s in corps["services"] if s["nom"] == "referentiel")
        assert referentiel["execution"] == "REPOND"

    def test_les_services_sans_sonde_le_disent(self, administrateur):
        """⚠️ Trois sur quatorze depuis le pas 121, et c'est dit plutôt que masqué.

        Un registre qui déclarerait sains les services qu'il n'interroge pas serait
        la sonde complaisante que le pas 12 a supprimée : il affirmerait précisément
        la chose qu'il ne sait pas.

        ⚠️ **La liste est exacte, et non « au moins ceux-là ».** Ce cas disait
        « pilotage et clôture sont sans sonde » ; le Pilotage a pris sept réglages sans
        prendre de sonde, et rien ne l'a signalé pendant quatre pas. Une liste fermée
        oblige à revenir ici le jour où un service change de camp, dans un sens comme
        dans l'autre.
        """
        corps = administrateur.get("/transverse/services").json()
        sans_sonde = sorted(
            s["nom"] for s in corps["services"] if s["execution"] == "SANS_SONDE"
        )
        # ⚠️ La Clôture et la Création d'entreprise n'ont aucune ressource à elles : elles
        # lisent la Comptabilité, le Portefeuille et la base. Le Social lit les barèmes,
        # mais ils appartiennent au Référentiel, dont la sonde les couvre. Leur inventer
        # une sonde qui relit la ressource d'un autre créerait deux vérités sur une même
        # question. Voir `_inscrire_les_sondes` dans `app/main.py`.
        assert sans_sonde == ["cloture", "creation_entreprise", "social"]

    def test_une_installation_neuve_n_affiche_aucun_service_en_difficulte(
        self, administrateur
    ):
        """⚠️ Le cas qui a fait naître le niveau `SUSPECT`.

        La sonde des Tenants signale un répertoire vide, normal sur une installation
        neuve. Rendue `EN_PANNE`, elle marquait **treize services** comme ayant un
        appui tombé. Une sonde qui crie au loup finit ignorée, et le jour où elle a
        raison personne ne regarde.
        """
        corps = administrateur.get("/transverse/services").json()
        assert corps["en_difficulte"] == []


class TestLeControleDeSanteParService:
    """Ce que Consul interroge toutes les dix secondes, et comment il le lit.

    ⚠️ **Consul traduit les codes de statut en trois niveaux, et un seul code
    déclenche l'intermédiaire.** `2xx` vaut *passing*, `429` vaut *warning*, tout le
    reste vaut *critical*. S'écarter de cette convention ferait ranger un service
    suspect parmi les services morts.
    """

    def test_un_service_qui_repond_rend_200(self, administrateur):
        reponse = administrateur.get("/transverse/services/referentiel/sante")
        assert reponse.status_code == 200
        assert reponse.json()["etat"] == "REPOND"

    def test_un_service_suspect_rend_429_et_non_503(self, administrateur, monkeypatch):
        """Le code de l'avertissement chez Consul.

        ─────────────────────────────────────────────────────────────────────────
        Le rendre `503` classerait un signal incertain parmi les pannes, et
        déclencherait le retrait du trafic pour un répertoire de tenants vide sur
        une installation neuve.

        ⚠️ **Ce cas force la suspicion, et il a fallu qu'il la force.**

        Sa première version comptait sur le répertoire des tenants réellement vide.
        Cela tenait tant qu'aucun test n'ouvrait de tenant ; la recette de bout en
        bout en ouvre un, et le répertoire est mémoïsé **par processus**. Le cas
        est alors passé au rouge selon l'ordre d'exécution des fichiers.

        Un cas qui dépend d'un état global qu'il ne pose pas lui-même ne mesure pas
        ce qu'il croit : il mesure ce que les autres ont laissé derrière eux.
        ─────────────────────────────────────────────────────────────────────────
        """
        import app.contextes.transverse.adaptateurs.entrant.routes_registre as routes
        from app.registre.etat import Verdict

        monkeypatch.setattr(
            routes,
            "sondes_du_processus",
            lambda: {"transverse": lambda: Verdict.suspect("répertoire vide")},
        )
        reponse = administrateur.get("/transverse/services/transverse/sante")
        assert reponse.status_code == 429
        assert reponse.json()["etat"] == "SUSPECT"
        assert reponse.json()["motif"] == "répertoire vide"

    def test_un_service_sans_sonde_rend_200(self, administrateur):
        """⚠️ Trois sur quatorze, et les rendre critiques afficherait un tableau
        Consul rouge en permanence. Un tableau rouge en permanence n'est plus lu.

        Le corps porte la nuance : `SANS_SONDE` n'est pas `REPOND`.
        """
        reponse = administrateur.get("/transverse/services/cloture/sante")
        assert reponse.status_code == 200
        assert reponse.json()["etat"] == "SANS_SONDE"

    def test_un_service_en_panne_rend_503(self, administrateur, monkeypatch):
        """Le niveau *critical* de Consul, celui qui retire le service du trafic.

        ⚠️ **Ce cas force la panne, et il le doit.** Aucun des quatorze services
        n'est réellement en panne dans un processus de test : retirer la
        correspondance vers `503` ne changeait donc rien, et la mutation a survécu
        pour cette seule raison.

        Un cas ne peut mesurer un garde que s'il existe une situation où le garde
        change quelque chose. C'est la troisième fois que ce chantier le rappelle.
        """
        import app.contextes.transverse.adaptateurs.entrant.routes_registre as routes
        from app.registre.etat import Verdict

        monkeypatch.setattr(
            routes,
            "sondes_du_processus",
            lambda: {"referentiel": lambda: Verdict.panne("dossier introuvable")},
        )
        reponse = administrateur.get("/transverse/services/referentiel/sante")
        assert reponse.status_code == 503
        assert reponse.json()["etat"] == "EN_PANNE"
        assert reponse.json()["motif"] == "dossier introuvable"

    def test_un_nom_inconnu_rend_404(self, administrateur):
        assert administrateur.get("/transverse/services/inexistant/sante").status_code == 404

    def test_le_controle_ne_rend_ni_le_graphe_ni_les_appuis(self, administrateur):
        """⚠️ Appelé toutes les dix secondes par un agent qui n'a besoin que d'un code.

        Et surtout : la liste de ce qui tombe avec un service est une carte des
        points de rupture de la plateforme. Elle vit sur la route du registre,
        demandée par un humain, pas dans une sonde qui bat en permanence.
        """
        corps = administrateur.get("/transverse/services/referentiel/sante").json()
        assert set(corps) <= {"service", "etat", "motif"}

    def test_il_n_est_pas_public(self, client):
        """Énumérer les services est le premier geste de qui cherche quoi arrêter.

        Consul sait porter un en-tête d'autorisation sur ses contrôles : la
        protection ne lui coûte rien, et son absence coûterait la carte.
        """
        assert client.get("/transverse/services/referentiel/sante").status_code in (401, 403)
