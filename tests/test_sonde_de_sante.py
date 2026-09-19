"""La sonde de santé : ce qu'elle affirme, et ce qu'elle refuse d'affirmer.

⚠️ **Une sonde a un seul devoir, et ce n'est pas de répondre `200`.** C'est de ne jamais
dire que tout va bien quand ce n'est pas vrai. Un orchestrateur s'en sert pour décider
d'envoyer du trafic ; une sonde complaisante garde un conteneur en panne dans la rotation
et laisse le tableau de bord au vert pendant que les adhérents reçoivent des `500`. C'est
le pire état d'une panne : celui où personne ne cherche.

CE FICHIER COUVRE DEUX PROMESSES

`503` quand la base est injoignable. Elle n'avait aucun test : la route lisait la base
depuis le chantier 2, et rien ne vérifiait qu'elle rendait bien `503` plutôt que `200`.

Le verdict de cloisonnement, publié depuis le pas 12. Une sonde qui répond
« opérationnel » pendant que les politiques ne s'appliquent pas ment sur exactement la
chose qui est fausse.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.config import configuration
from app.infrastructure.roles import DiagnosticDuRole, EtatDuCloisonnement
from app.main import creer_application
from tests.conftest import URL_BASE_TEST, exige_postgresql


@pytest.fixture
def application_sans_cycle_de_vie(monkeypatch):
    """Une application en persistance PostgreSQL, montée sans jouer son cycle de vie.

    ⚠️ **Les deux moitiés de cette phrase comptent.**

    *En persistance PostgreSQL*, parce que la sonde sort avant d'interroger quoi que ce
    soit en mémoire : montée sur la configuration de test par défaut, elle ne parcourrait
    aucune des lignes que ce fichier vérifie. Un test qui s'exécute sur un chemin que la
    production ne prend jamais ne prouve rien de la production.

    *Sans jouer son cycle de vie*, parce que `TestClient(app)` hors gestionnaire de
    contexte ne déclenche pas le démarrage. C'est l'état par défaut voulu ici : chaque
    cas pose ensuite le verdict qu'il veut éprouver, ou n'en pose aucun.
    """
    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", URL_BASE_TEST)
    configuration.cache_clear()
    application = creer_application()
    yield application
    configuration.cache_clear()


@exige_postgresql
class TestLeVerdictDeCloisonnement:
    def test_sans_constat_la_sonde_le_dit_au_lieu_de_le_taire(self, application_sans_cycle_de_vie):
        """`NON_CONSTATE` plutôt que rien, et surtout plutôt qu'`APPLIQUE`.

        Un champ absent se lit comme « pas concerné » ; un champ qui dit « je n'ai pas
        pu constater » se lit comme ce qu'il est. La différence compte le jour où la
        base était injoignable au démarrage et où quelqu'un cherche pourquoi.
        """
        client = TestClient(application_sans_cycle_de_vie)
        corps = client.get("/sante").json()
        assert corps["cloisonnement"] == "NON_CONSTATE"

    def test_un_cloisonnement_applique_est_publie_sans_explication(
        self, application_sans_cycle_de_vie, monkeypatch
    ):
        """Rien à expliquer quand tout va bien : le champ resterait du bruit."""
        _forcer(
            application_sans_cycle_de_vie,
            monkeypatch,
            DiagnosticDuRole(etat=EtatDuCloisonnement.APPLIQUE, role="cga_app"),
        )
        corps = TestClient(application_sans_cycle_de_vie).get("/sante").json()
        assert corps["cloisonnement"] == "APPLIQUE"
        assert "cloisonnement_explication" not in corps

    def test_un_contournement_est_publie_avec_le_geste_qui_le_corrige(
        self, application_sans_cycle_de_vie, monkeypatch
    ):
        """Le verdict seul enverrait lire le code. C'est le geste qui manque, pas le nom.

        Celui qui lit `/sante` à 3 h du matin doit repartir avec le fichier à jouer, pas
        avec un mot à chercher.
        """
        _forcer(
            application_sans_cycle_de_vie,
            monkeypatch,
            DiagnosticDuRole(
                etat=EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE,
                role="cga",
                tables_possedees=("proforma", "dossier_commercial"),
            ),
        )
        corps = TestClient(application_sans_cycle_de_vie).get("/sante").json()
        assert corps["cloisonnement"] == "CONTOURNE_PROPRIETAIRE"
        assert "roles-postgresql.sql" in corps["cloisonnement_explication"]

    def test_un_cloisonnement_contourne_ne_rend_pas_503(
        self, application_sans_cycle_de_vie, monkeypatch
    ):
        """Un choix, et non un oubli.

        Le service reste disponible : le retirer du trafic ne protège aucune donnée et
        prive les adhérents d'un service qui fonctionne. La sanction est ailleurs, et
        plus dure — la production refuse de démarrer, voir `test_le_demarrage`.
        """
        _forcer(
            application_sans_cycle_de_vie,
            monkeypatch,
            DiagnosticDuRole(etat=EtatDuCloisonnement.CONTOURNE_SUPERUTILISATEUR, role="postgres"),
        )
        assert TestClient(application_sans_cycle_de_vie).get("/sante").status_code == 200


@exige_postgresql
class TestLaBaseInjoignable:
    def test_une_base_muette_rend_503_et_non_200(self, application_sans_cycle_de_vie, monkeypatch):
        """`503` et non `200` avec un état dégradé dans le corps.

        Un orchestrateur lit le code de statut, jamais le corps. Un `200` accompagné de
        `"etat": "degrade"` garde le conteneur en rotation.
        """
        _base_muette(monkeypatch)
        reponse = TestClient(application_sans_cycle_de_vie).get("/sante")
        assert reponse.status_code == 503
        assert reponse.json()["etat"] == "degrade"
        assert reponse.json()["cause"] == "base_de_donnees"

    def test_le_detail_de_la_panne_reste_au_journal(
        self, application_sans_cycle_de_vie, monkeypatch
    ):
        """Une sonde est souvent exposée sans authentification.

        Le message d'un pilote de base nomme l'hôte, le port et l'utilisateur. Il n'a
        rien à faire dans un corps de réponse public.
        """
        _base_muette(monkeypatch, message="host=interne port=5432 user=cga_app")
        corps = TestClient(application_sans_cycle_de_vie).get("/sante").json()
        assert "interne" not in str(corps)
        assert "cga_app" not in str(corps)


def _forcer(application, monkeypatch, diagnostic: DiagnosticDuRole) -> None:
    """Pose le verdict que le cycle de vie aurait posé.

    Monkeypatch plutôt qu'une vraie base : ces cas vérifient ce que la **sonde** fait
    d'un verdict, pas comment le verdict s'obtient. Cette seconde question est celle de
    `test_roles.py`, et elle s'y traite sur une vraie base parce qu'elle le doit.
    """
    monkeypatch.setattr(application.state, "cloisonnement", diagnostic, raising=False)


def _base_muette(monkeypatch, message: str = "base injoignable") -> None:
    """Rend le moteur incapable de se connecter, sans toucher à la base réelle."""

    def _echec():
        raise OSError(message)

    monkeypatch.setattr("app.main.moteur", _echec)


@exige_postgresql
class TestLeDemarrage:
    """La seule exception au principe « on démarre quand même ».

    ─────────────────────────────────────────────────────────────────────────────────
    Partout ailleurs dans ce projet, une dépendance défaillante n'empêche pas le
    processus de se lever : un répertoire des tenants vide rend 404, ce qui est
    désagréable mais franc, et refuser de démarrer priverait aussi la vitrine et la
    sonde, qui n'ont besoin d'aucun tenant.

    Le cloisonnement fait exception parce que la comparaison ne tient pas. Un service
    qui démarre sans cloisonnement **fonctionne** : il répond, il sert, et il sert les
    données de deux cabinets sans séparation. La panne franche vaut mieux.
    ─────────────────────────────────────────────────────────────────────────────────
    """

    def test_la_production_refuse_de_demarrer_sur_un_contournement_constate(self, monkeypatch):
        """Pas un avertissement, pas une dégradation : le processus ne se lève pas."""
        _en_production(monkeypatch)
        _constat(monkeypatch, EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE)
        with pytest.raises(RuntimeError, match="Démarrage refusé"), TestClient(creer_application()):
            pass

    def test_le_refus_dit_le_geste_qui_le_leve(self, monkeypatch):
        """Celui qui déploie à 3 h du matin n'ira pas lire le code."""
        _en_production(monkeypatch)
        _constat(monkeypatch, EtatDuCloisonnement.CONTOURNE_PROPRIETAIRE)
        try:
            with TestClient(creer_application()):
                pass
        except RuntimeError as refus:
            assert "roles-postgresql.sql" in str(refus)

    def test_la_production_demarre_quand_le_cloisonnement_s_applique(self, monkeypatch):
        """La contre-épreuve. Sans elle, un refus systématique passerait le cas d'avant."""
        _en_production(monkeypatch)
        _constat(monkeypatch, EtatDuCloisonnement.APPLIQUE)
        with TestClient(creer_application()) as client:
            assert client.get("/sante").json()["cloisonnement"] == "APPLIQUE"

    def test_une_base_injoignable_au_demarrage_ne_refuse_rien(self, monkeypatch):
        """La distinction qui évite d'empêcher un redémarrage au pire moment.

        ⚠️ « J'ai demandé et la réponse est mauvaise » n'est pas « je n'ai pas pu
        demander ». Confondre les deux ferait qu'une coupure réseau de trente secondes
        empêcherait tout redémarrage de production, c'est-à-dire exactement pendant
        l'incident où l'on redémarre.
        """
        _en_production(monkeypatch)
        _base_muette(monkeypatch)
        with TestClient(creer_application()) as client:
            assert client.get("/sante").status_code == 503

    def test_hors_production_un_contournement_ne_refuse_rien(self, monkeypatch):
        """La suite de tests elle-même s'exécute avec un rôle superutilisateur.

        Elle a besoin d'écrire pour deux locataires afin de vérifier qu'ils ne se voient
        pas : lui refuser le contournement lui retirerait son moyen de mesurer. C'est
        `test_isolation.py`, avec son second rôle non propriétaire, qui vérifie le
        cloisonnement pour de bon.
        """
        monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
        monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", URL_BASE_TEST)
        configuration.cache_clear()
        _constat(monkeypatch, EtatDuCloisonnement.CONTOURNE_SUPERUTILISATEUR)
        try:
            with TestClient(creer_application()) as client:
                corps = client.get("/sante").json()
            assert corps["cloisonnement"] == "CONTOURNE_SUPERUTILISATEUR"
        finally:
            configuration.cache_clear()


def _en_production(monkeypatch) -> None:
    """Les réglages minimaux qu'une configuration de production doit satisfaire.

    Ils sont posés en entier parce que `_exigences_production` les vérifie tous : en
    omettre un ferait échouer le cas sur le mauvais motif, et un test qui échoue pour
    une raison étrangère à ce qu'il mesure ne mesure plus rien.
    """
    monkeypatch.setenv("CGA_ENVIRONNEMENT", "production")
    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", URL_BASE_TEST)
    monkeypatch.setenv("CGA_SMTP_HOTE", "smtp.exemple.cm")
    monkeypatch.setenv("CGA_ADRESSE_PUBLIQUE_SITE", "https://www.cga-brcg.cm")
    monkeypatch.setenv("CGA_ORIGINES_CORS", '["https://www.cga-brcg.cm"]')
    monkeypatch.setenv("CGA_CLE_CHIFFREMENT", base64.b64encode(bytes(32)).decode("ascii"))
    configuration.cache_clear()


def _constat(monkeypatch, etat: EtatDuCloisonnement) -> None:
    """Fait répondre le diagnostic sans dépendre de l'état réel des rôles de la base."""
    monkeypatch.setattr(
        "app.main.diagnostiquer",
        lambda _: DiagnosticDuRole(etat=etat, role="essai", tables_possedees=("proforma",)),
    )
