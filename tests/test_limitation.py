"""La limitation de débit, et ce qu'elle doit refuser sans gêner personne.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES TESTS DÉFENDENT

Une limitation de débit se trompe de deux façons, et les deux sont graves :

* **trop lâche**, elle laisse saturer le processeur par des vérifications
  Argon2 — cent millisecondes chacune, par conception ;
* **trop serrée**, elle bloque une agence entière qui sort par une seule adresse,
  un mardi matin à neuf heures, et personne ne comprend pourquoi.

Les tests couvrent donc autant ce qui doit **passer** que ce qui doit être
refusé. Le second cas est celui qu'on oublie d'écrire, et c'est celui qui coûte
un appel du cabinet.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    reinitialiser_atelier,
)
from app.contextes.transverse.adaptateurs.entrant.limitation import (
    REGLES_PAR_DEFAUT,
    Limiteur,
    Regle,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 8, 15, 10, 0)

REGLE = Regle(
    chemin="/transverse/session",
    methode="POST",
    maximum=3,
    fenetre=60,
    motif="essai",
)


class TestLimiteur:
    def test_les_premieres_tentatives_passent(self):
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            assert all(limiteur.autorise(REGLE, "10.0.0.1") for _ in range(3))

    def test_la_tentative_de_trop_est_refusee(self):
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            for _ in range(3):
                limiteur.autorise(REGLE, "10.0.0.1")
            assert limiteur.autorise(REGLE, "10.0.0.1") is False

    def test_deux_adresses_sont_comptees_separement(self):
        """Sinon la première agence à saturer bloquerait toutes les autres."""
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            for _ in range(3):
                limiteur.autorise(REGLE, "10.0.0.1")
            assert limiteur.autorise(REGLE, "10.0.0.2") is True

    def test_la_fenetre_glisse(self):
        """Une fois la fenêtre passée, l'adresse repart de zéro."""
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            for _ in range(3):
                limiteur.autorise(REGLE, "10.0.0.1")
            assert limiteur.autorise(REGLE, "10.0.0.1") is False
        with horloge_figee(INSTANT + timedelta(seconds=61)):
            assert limiteur.autorise(REGLE, "10.0.0.1") is True

    def test_une_tentative_refusee_n_allonge_pas_le_blocage(self):
        """Le point le plus subtil de ce module.

        Si les refus se comptaient, un attaquant qui continue de marteler
        maintiendrait la fenêtre pleine indéfiniment — et l'utilisateur
        légitime qui partage cette adresse resterait dehors longtemps après la
        fin de l'attaque. Ici, cinquante refus supplémentaires ne repoussent
        pas la réouverture d'une seconde.
        """
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            for _ in range(3):
                limiteur.autorise(REGLE, "10.0.0.1")
            for _ in range(50):
                assert limiteur.autorise(REGLE, "10.0.0.1") is False
        with horloge_figee(INSTANT + timedelta(seconds=61)):
            assert limiteur.autorise(REGLE, "10.0.0.1") is True

    def test_un_chemin_non_protege_n_est_pas_reconnu(self):
        limiteur = Limiteur(regles=(REGLE,))
        assert limiteur.regle_pour("POST", "/transverse/moi") is None
        assert limiteur.regle_pour("GET", "/transverse/session") is None

    def test_la_purge_oublie_les_adresses_inactives(self):
        """Sans elle, le dictionnaire garde une entrée par adresse **vue depuis
        le démarrage** — une fuite lente, invisible en test, et bien réelle au
        bout de quelques mois."""
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            limiteur.autorise(REGLE, "10.0.0.1")
        with horloge_figee(INSTANT + timedelta(hours=2)):
            assert limiteur.purger() == 1
            # Et l'adresse repart bien de zéro après l'oubli.
            assert limiteur.autorise(REGLE, "10.0.0.1") is True

    def test_la_purge_epargne_une_adresse_active(self):
        limiteur = Limiteur(regles=(REGLE,))
        with horloge_figee(INSTANT):
            limiteur.autorise(REGLE, "10.0.0.1")
            assert limiteur.purger() == 0


class TestReglesParDefaut:
    def test_la_connexion_et_le_second_facteur_sont_proteges(self):
        """Les deux points d'entrée qui décident d'un accès.

        Le second facteur en particulier : six chiffres, un million de
        possibilités, et une fenêtre qui en accepte plusieurs. Sans limite, il
        se devine en quelques heures — et le second facteur ne vaut plus rien.
        """
        chemins = {regle.chemin for regle in REGLES_PAR_DEFAUT}
        assert "/transverse/session" in chemins
        assert "/transverse/session/renforcement" in chemins
        assert "/transverse/mot-de-passe/oubli" in chemins
        assert "/transverse/mot-de-passe/definition" in chemins

    def test_aucune_regle_n_est_assez_serree_pour_gener_une_agence(self):
        """Un cabinet entier peut sortir par une seule adresse publique.

        Une limite en dessous d'une dizaine de tentatives bloquerait un mardi
        matin ordinaire, et l'équipe désactiverait le garde-fou plutôt que de
        le régler.
        """
        for regle in REGLES_PAR_DEFAUT:
            assert regle.maximum >= 10, regle.chemin

    def test_chaque_regle_porte_un_motif_lisible(self):
        """Le journal d'un incident se lit six mois plus tard, par quelqu'un
        d'autre. « Limite atteinte sur /transverse/session » ne dit rien ;
        « (connexion) » situe l'incident."""
        for regle in REGLES_PAR_DEFAUT:
            assert regle.motif and not regle.motif.startswith("/")


# ── Bout en bout, à travers l'application ───────────────────────────────────


@pytest.fixture
def client() -> Iterator[TestClient]:
    reinitialiser_atelier()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())


class TestApi:
    def test_une_lecture_ordinaire_n_est_jamais_limitee(self, client: TestClient):
        """Cinquante lectures du référentiel doivent passer.

        Un écran qui affiche dix paramètres fait dix appels ; limiter la lecture
        rendrait l'application inutilisable avant de gêner qui que ce soit.
        """
        for _ in range(50):
            assert client.get("/sante").status_code == 200

    def test_le_martelage_de_la_connexion_finit_en_429(self, client: TestClient):
        """Le test qui compte : sans lui, Argon2 est un amplificateur de charge."""
        codes = [
            client.post(
                "/transverse/session",
                json={"courriel": "inconnu@exemple.cm", "mot_de_passe": "faux"},
            ).status_code
            for _ in range(40)
        ]
        assert 429 in codes
        # ⚠️ Les trente premières doivent passer : la limite protège contre un
        # automate, pas contre un humain qui se trompe.
        assert codes[:30].count(429) == 0

    def test_le_refus_dit_quand_reessayer(self, client: TestClient):
        for _ in range(40):
            reponse = client.post(
                "/transverse/session",
                json={"courriel": "inconnu@exemple.cm", "mot_de_passe": "faux"},
            )
            if reponse.status_code == 429:
                break
        assert reponse.status_code == 429
        # Sans `Retry-After`, un client bien élevé n'a aucun moyen de savoir
        # quand revenir, et un front un peu insistant s'auto-bloque.
        assert int(reponse.headers["Retry-After"]) == 300
        assert "Réessayez dans 5 minutes" in reponse.json()["detail"]

    def test_le_refus_ne_dit_rien_du_compte_vise(self, client: TestClient):
        """Un `429` ne doit pas devenir un oracle d'existence de compte.

        Le message est le même que l'adresse existe ou non ; c'est la même
        discipline que la route de mot de passe oublié.
        """
        for _ in range(40):
            reponse = client.post(
                "/transverse/session",
                json={"courriel": "inconnu@exemple.cm", "mot_de_passe": "faux"},
            )
        detail = reponse.json()["detail"]
        assert "inconnu@exemple.cm" not in detail
        assert "compte" not in detail.lower()
