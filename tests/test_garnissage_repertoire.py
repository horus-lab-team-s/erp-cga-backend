"""Le répertoire se garnit au démarrage, sinon la table des tenants ne sert à rien.

Ce qui ferme la boucle du socle : la passerelle consulte un répertoire en mémoire, la
table dit ce qu'il doit contenir, et le cycle de vie de l'application verse l'une dans
l'autre **avant la première requête**.

Le garnir à la première requête ferait échouer celle-ci — et ce serait la requête d'un
vrai client, sur un vrai sous-domaine.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.contextes.tenants.api import DepotTenantsSql, NatureTenant
from app.contextes.tenants.application.cycle_de_vie import (
    activer,
    avancer,
    ouvrir,
    suspendre,
)
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from app.contextes.transverse.adaptateurs.entrant.dependances import repertoire_des_tenants
from app.contextes.transverse.api import garnir_le_repertoire
from tests.conftest import exige_postgresql

LE_JOUR = date(2026, 9, 9)


def _actif(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


@pytest.fixture(autouse=True)
def repertoire_neuf():
    """Un répertoire vierge par test : il est mis en cache par processus."""
    repertoire_des_tenants.cache_clear()
    yield
    repertoire_des_tenants.cache_clear()


class TestSansBase:
    def test_en_memoire_le_garnissage_ne_fait_rien(self, monkeypatch):
        """Le mode démonstration n'a pas de table à lire, et n'a pas à échouer pour
        autant."""
        assert garnir_le_repertoire() == 0
        assert repertoire_des_tenants().par_slug("station") is None

    def test_une_base_injoignable_n_empeche_pas_de_demarrer(self, monkeypatch):
        """Un répertoire vide rend 404 sur les sous-domaines, ce qui est désagréable mais
        franc. Refuser de démarrer priverait aussi la vitrine et la sonde de santé, qui
        n'ont besoin d'aucun tenant. La panne se voit au journal, pas par un processus qui
        ne se lève pas.
        """
        from app.infrastructure import config as module_config

        monkeypatch.setattr(
            module_config.configuration().__class__,
            "persistance",
            "postgresql",
            raising=False,
        )
        monkeypatch.setattr(
            "app.contextes.transverse.adaptateurs.entrant.dependances.session_du_locataire",
            _session_qui_echoue,
        )
        assert garnir_le_repertoire() == 0


def _session_qui_echoue(*args, **kwargs):
    raise OSError("base injoignable")


@exige_postgresql
class TestAvecBase:
    def test_ce_qui_est_en_base_se_retrouve_au_repertoire(self, session_sql, monkeypatch):
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(_actif("station-bonaberi"))
        depot.enregistrer(suspendre(_actif("boulangerie"), "honoraires échus", LE_JOUR))
        session_sql.commit()

        monkeypatch.setattr(
            "app.contextes.transverse.adaptateurs.entrant.dependances.configuration",
            lambda: _config_en_base(),
        )
        monkeypatch.setattr(
            "app.contextes.transverse.adaptateurs.entrant.dependances.session_du_locataire",
            lambda _: _session_prete(session_sql),
        )

        assert garnir_le_repertoire() == 2
        repertoire = repertoire_des_tenants()
        assert repertoire.par_slug("station-bonaberi") is not None
        assert repertoire.par_slug("boulangerie") is not None

    def test_le_statut_survit_au_garnissage(self, session_sql, monkeypatch):
        """La passerelle décide du verdict sur le statut : le perdre en chemin servirait
        un tenant suspendu comme s'il était actif."""
        from app.contextes.tenants.api import StatutTenant, Verdict, verdict_pour

        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(suspendre(_actif("boulangerie"), "honoraires échus", LE_JOUR))
        session_sql.commit()

        monkeypatch.setattr(
            "app.contextes.transverse.adaptateurs.entrant.dependances.configuration",
            lambda: _config_en_base(),
        )
        monkeypatch.setattr(
            "app.contextes.transverse.adaptateurs.entrant.dependances.session_du_locataire",
            lambda _: _session_prete(session_sql),
        )
        garnir_le_repertoire()

        tenant = repertoire_des_tenants().par_slug("boulangerie")
        assert tenant.statut is StatutTenant.SUSPENDU
        assert verdict_pour(tenant) is Verdict.REGULARISER


def _config_en_base():
    """La configuration réelle, avec la persistance forcée sur celle de la production.

    ⚠️ Elle valait `"sql"` — une valeur que **rien n'accepte**. Le garnissage la
    comparait telle quelle, la production exige `"postgresql"` : le répertoire n'était
    donc jamais garni en production, et tout sous-domaine rendait 404. Ces deux tests
    passaient au vert parce qu'ils forçaient eux-mêmes la valeur fautive, c'est-à-dire
    qu'ils vérifiaient le défaut au lieu du comportement.

    C'est pourquoi `persistance` est aujourd'hui un `Literal` fermé à deux valeurs, et
    pourquoi la question se pose par `en_base` plutôt que par une comparaison écrite à
    la main. Voir `test_config.py`.
    """
    from app.infrastructure.config import configuration

    return configuration().model_copy(update={"persistance": "postgresql"})


class _session_prete:
    """Un gestionnaire de contexte qui rend une session déjà ouverte, sans la fermer.

    La session du test est gérée par sa propre fixture : la refermer ici ferait échouer
    les assertions qui suivent, pour une raison sans rapport avec le garnissage.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exception) -> bool:
        return False
