"""Le répertoire des tenants se tient à jour, sans redémarrage (pas 122).

─────────────────────────────────────────────────────────────────────────────────
LE RESTE DU CHANTIER MULTI-TENANT QUE CE FICHIER SOLDE

Sa dernière section l'écrivait noir sur blanc : « le répertoire se garnit au démarrage ;
il ne se met pas à jour quand un tenant s'ouvre ou change d'état. Un redémarrage suffit
tant qu'il y a une souscription par jour, et ne suffira plus à dix. »

Deux moitiés, et la seconde est la plus grave :

1. un tenant **ouvert** après le démarrage n'est pas résolu. Le client qui vient de payer
   reçoit son lien d'activation, clique, et tombe sur un `404` ;
2. un tenant **suspendu ou résilié** reste résolu. On continue de servir les données d'un
   client qu'on a cessé de servir.

⚠️ **Ces cas s'exécutent sur PostgreSQL réel.** Le rechargement lit la table des tenants,
et un test qui le simulerait ne prouverait rien du chemin que la production prend.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.contextes.tenants.adaptateurs.sortant.depot_tenants_sql import DepotTenantsSql
from app.contextes.tenants.api import NatureTenant
from app.contextes.tenants.application.cycle_de_vie import (
    activer,
    avancer,
    ouvrir,
    resilier,
    suspendre,
)
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from app.contextes.transverse.adaptateurs.entrant import dependances
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    garnir_le_repertoire,
    oublier_le_garnissage,
    repertoire_a_jour,
    repertoire_des_tenants,
)
from app.infrastructure import base_de_donnees, config
from app.partage.horloge import horloge_figee
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LE_JOUR = date(2026, 9, 9)
MIDI = datetime(2026, 9, 9, 12, 0)


def _actif(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


@pytest.fixture
def en_base(moteur_test, monkeypatch):
    """Une configuration en persistance PostgreSQL, et un répertoire vierge.

    ⚠️ Le répertoire et l'instant du dernier garnissage sont des états **de processus** :
    sans les vider, un cas hériterait du répertoire du précédent, et l'ordre des tests
    déciderait du résultat.
    """
    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", str(moteur_test.url).replace("***", "cga"))
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()
    repertoire_des_tenants.cache_clear()
    oublier_le_garnissage()
    yield moteur_test
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()
    repertoire_des_tenants.cache_clear()
    oublier_le_garnissage()


def _ecrire(moteur, *tenants: Tenant) -> None:
    """Écrit ces tenants, et **valide** : le rechargement ouvre sa propre session."""
    from sqlalchemy.orm import Session as SessionSql

    with SessionSql(moteur) as session:
        depot = DepotTenantsSql(session)
        for tenant in tenants:
            depot.enregistrer(tenant)
        session.commit()


def _vider(moteur) -> None:
    """Retire toutes les lignes. Le nom de la table est lu sur la table, pas écrit ici :
    une chaîne en dur se serait tue le jour où la table serait renommée."""
    from app.contextes.tenants.adaptateurs.sortant.tables import TableTenant

    with moteur.begin() as connexion:
        connexion.execute(TableTenant.__table__.delete())


class TestCeQueLaFenetreCorrige:
    def test_un_tenant_ouvert_apres_le_demarrage_finit_par_etre_resolu(self, en_base):
        """Le client qui vient de payer, et qui tombait sur un 404 jusqu'au redémarrage."""
        with horloge_figee(MIDI):
            assert garnir_le_repertoire() == 0
            _ecrire(en_base, _actif("station-bonaberi"))
            # Dans la fenêtre : le répertoire reste celui du garnissage précédent.
            assert repertoire_a_jour().par_slug("station-bonaberi") is None

        with horloge_figee(MIDI + timedelta(seconds=30)):
            assert repertoire_a_jour().par_slug("station-bonaberi") is not None

    def test_un_tenant_suspendu_cesse_d_etre_servi(self, en_base):
        """⚠️ La moitié grave : servir les données d'un client qu'on a cessé de servir."""
        from app.contextes.tenants.domaine.resolution import Verdict, verdict_pour

        with horloge_figee(MIDI):
            _ecrire(en_base, _actif("boulangerie"))
            assert garnir_le_repertoire() == 1, "le nombre versé est rendu, et il compte"
            assert verdict_pour(repertoire_a_jour().par_slug("boulangerie")) is Verdict.SERVIR
            _vider(en_base)
            _ecrire(en_base, suspendre(_actif("boulangerie"), "honoraires échus", LE_JOUR))

        with horloge_figee(MIDI + timedelta(seconds=31)):
            verdict = verdict_pour(repertoire_a_jour().par_slug("boulangerie"))
        assert verdict is not Verdict.SERVIR

    def test_un_tenant_retire_de_la_table_disparait(self, en_base):
        """⚠️ Le rechargement **remplace**. Verser par-dessus le garderait pour toujours."""
        with horloge_figee(MIDI):
            _ecrire(en_base, _actif("ephemere"))
            garnir_le_repertoire()
            assert repertoire_a_jour().par_slug("ephemere") is not None
            _vider(en_base)

        with horloge_figee(MIDI + timedelta(seconds=30)):
            assert repertoire_a_jour().par_slug("ephemere") is None

    def test_un_tenant_resilie_est_recharge_avec_son_statut(self, en_base):
        from app.contextes.tenants.domaine.tenant import StatutTenant

        with horloge_figee(MIDI):
            _ecrire(en_base, resilier(_actif("ancien-client"), "départ", LE_JOUR))

        with horloge_figee(MIDI + timedelta(seconds=60)):
            tenant = repertoire_a_jour().par_slug("ancien-client")
        assert tenant is not None and tenant.statut is StatutTenant.RESILIE


class TestLeRemplacement:
    def test_un_slug_en_capitales_reste_resolu_en_minuscules(self):
        """⚠️ `Tenant` accepte « Station » : seul `valider` refuse la casse, et il n'est
        pas appelé à la lecture.

        Une ligne écrite avant que la règle n'existe, ou par une migration, porterait donc
        une capitale. Le répertoire doit la résoudre quand même : un nom d'hôte est
        insensible à la casse, et rendre `404` à un client dont l'adresse est correcte
        serait une panne qu'il ne pourrait ni comprendre ni contourner.
        """
        from app.contextes.tenants.api import RepertoireEnMemoire
        from app.contextes.tenants.domaine.tenant import Tenant

        repertoire = RepertoireEnMemoire()
        repertoire.remplacer(
            [Tenant(identifiant="tnt-x", slug="Station", nature=NatureTenant.ENTREPRISE)]
        )
        assert repertoire.par_slug("station") is not None

    def test_remplacer_vide_ce_qui_n_est_plus_la(self):
        """La propriété, sans base : ce qui n'est pas dans la nouvelle liste sort."""
        from app.contextes.tenants.api import RepertoireEnMemoire

        repertoire = RepertoireEnMemoire([_actif("un"), _actif("deux")])
        assert repertoire.remplacer([_actif("deux")]) == 1
        assert repertoire.par_slug("un") is None
        assert repertoire.par_slug("deux") is not None


class TestCeQueLaFenetreProtege:
    def test_dans_la_fenetre_la_table_n_est_pas_relue(self, en_base, monkeypatch):
        """⚠️ Le répertoire existe pour que la résolution soit une lecture de dictionnaire.

        Un rechargement par requête le rendrait inutile, et ajouterait un aller-retour en
        base au chemin critique de **tout** le trafic.
        """
        _ecrire(en_base, _actif("station-bonaberi"))
        with horloge_figee(MIDI):
            garnir_le_repertoire()
            lectures = _compter_les_garnissages(monkeypatch)
            for _ in range(50):
                assert repertoire_a_jour().par_slug("station-bonaberi") is not None
        assert lectures() == 0, "cinquante requêtes dans la fenêtre, aucune relecture"

    def test_la_fenetre_passee_une_seule_relecture_sert_la_suite(self, en_base, monkeypatch):
        _ecrire(en_base, _actif("station-bonaberi"))
        with horloge_figee(MIDI):
            garnir_le_repertoire()
        lectures = _compter_les_garnissages(monkeypatch)
        with horloge_figee(MIDI + timedelta(seconds=30)):
            for _ in range(10):
                repertoire_a_jour().par_slug("station-bonaberi")
        assert lectures() == 1

    def test_une_base_injoignable_ne_vide_pas_le_repertoire(self, en_base, monkeypatch):
        """Servir ce qu'on sait vaut mieux que ne rien servir pendant une panne."""
        _ecrire(en_base, _actif("station-bonaberi"))
        with horloge_figee(MIDI):
            garnir_le_repertoire()

        def _injoignable(_locataire):
            raise RuntimeError("base injoignable")

        monkeypatch.setattr(dependances, "session_du_locataire", _injoignable)
        with horloge_figee(MIDI + timedelta(seconds=30)):
            assert repertoire_a_jour().par_slug("station-bonaberi") is not None

    def test_une_base_injoignable_n_est_pas_martelee(self, en_base, monkeypatch):
        """⚠️ L'instant du dernier essai est noté **même quand l'essai échoue**.

        Sans cela, chaque requête rouvrirait une session sur une base déjà en difficulté,
        précisément au moment où elle a besoin qu'on la laisse respirer.
        """
        essais = []

        def _injoignable(_locataire):
            essais.append(1)
            raise RuntimeError("base injoignable")

        monkeypatch.setattr(dependances, "session_du_locataire", _injoignable)
        with horloge_figee(MIDI + timedelta(seconds=30)):
            for _ in range(20):
                repertoire_a_jour()
        assert len(essais) == 1

    def test_en_memoire_rien_n_est_recharge(self, monkeypatch):
        """Le mode de démonstration écrit directement dans le répertoire ; le recharger
        depuis une table qui n'existe pas l'effacerait."""
        monkeypatch.setenv("CGA_PERSISTANCE", "memoire")
        config.configuration.cache_clear()
        repertoire_des_tenants.cache_clear()
        oublier_le_garnissage()
        try:
            repertoire_des_tenants().inscrire(_actif("station-bonaberi"))
            with horloge_figee(MIDI + timedelta(days=7)):
                assert repertoire_a_jour().par_slug("station-bonaberi") is not None
        finally:
            config.configuration.cache_clear()
            repertoire_des_tenants.cache_clear()
            oublier_le_garnissage()


class TestLeReglage:
    def test_une_fenetre_nulle_est_refusee(self, monkeypatch):
        """⚠️ Zéro seconde, c'est un aller-retour en base par requête, c'est-à-dire le
        contraire de ce que le répertoire existe pour éviter."""
        from pydantic import ValidationError

        monkeypatch.setenv("CGA_FENETRE_REPERTOIRE_TENANTS_SECONDES", "0")
        config.configuration.cache_clear()
        try:
            with pytest.raises(ValidationError):
                config.configuration()
        finally:
            config.configuration.cache_clear()

    def test_la_fenetre_reglee_est_celle_qui_s_applique(self, en_base, monkeypatch):
        monkeypatch.setenv("CGA_FENETRE_REPERTOIRE_TENANTS_SECONDES", "300")
        config.configuration.cache_clear()
        with horloge_figee(MIDI):
            garnir_le_repertoire()
            _ecrire(en_base, _actif("station-bonaberi"))
        with horloge_figee(MIDI + timedelta(seconds=299)):
            assert repertoire_a_jour().par_slug("station-bonaberi") is None
        with horloge_figee(MIDI + timedelta(seconds=300)):
            assert repertoire_a_jour().par_slug("station-bonaberi") is not None


def _compter_les_garnissages(monkeypatch):
    """Compte les ouvertures de session du rechargement, sans en empêcher aucune."""
    compte = []
    vraie = dependances.session_du_locataire

    def _comptee(locataire):
        compte.append(1)
        return vraie(locataire)

    monkeypatch.setattr(dependances, "session_du_locataire", _comptee)
    return lambda: len(compte)
