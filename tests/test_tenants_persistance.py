"""Le répertoire des tenants, adossé à la base.

Ce qui manquait pour que la passerelle résolve autre chose que du vide : la table du plan
de contrôle, et le dépôt qui la lit.

⚠️ **La table du contexte N n'est pas cloisonnée**, contrairement à toutes les autres. Elle
est lue avant qu'on sache de quel locataire il s'agit — c'est elle qui le dit. Ces tests
ouvrent donc des sessions ordinaires, sans locataire établi, et c'est normal.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.contextes.tenants.adaptateurs.sortant.depot_tenants_sql import DepotTenantsSql
from app.contextes.tenants.api import NatureTenant, RepertoireEnMemoire
from app.contextes.tenants.application.cycle_de_vie import (
    activer,
    avancer,
    ouvrir,
    suspendre,
)
from app.contextes.tenants.domaine.tenant import (
    ETAPES_ORDONNEES,
    EtapeOuverture,
    StatutTenant,
    Tenant,
)
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LE_JOUR = date(2026, 9, 9)


def _actif(slug: str, identifiant: str | None = None) -> Tenant:
    tenant = ouvrir(identifiant or f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


class TestAllerRetour:
    def test_un_tenant_ecrit_se_relit_a_l_identique(self, session_sql):
        depot = DepotTenantsSql(session_sql)
        origine = _actif("station-bonaberi")
        depot.enregistrer(origine)
        session_sql.flush()

        assert depot.par_slug("station-bonaberi") == origine

    def test_la_resolution_est_insensible_a_la_casse(self, session_sql):
        """Un nom d'hôte l'est. Résoudre « Station » autrement que « station » servirait
        un 404 à un client dont l'adresse est correcte."""
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(_actif("station"))
        session_sql.flush()

        assert depot.par_slug("STATION") is not None
        assert depot.par_slug("  Station  ") is not None

    def test_un_slug_inconnu_rend_none_sans_lever(self, session_sql):
        """Un slug inconnu est un cas ordinaire — une faute de frappe, un scanner, un
        ancien lien. Lever ferait de chacun une erreur à diagnostiquer."""
        assert DepotTenantsSql(session_sql).par_slug("jamais-vu") is None

    @pytest.mark.parametrize("vide", ["", "   "])
    def test_un_slug_vide_rend_none(self, session_sql, vide: str):
        assert DepotTenantsSql(session_sql).par_slug(vide) is None

    def test_reecrire_un_tenant_met_a_jour_les_colonnes_promues(self, session_sql):
        """Elles sont réécrites depuis le document à chaque fois, jamais saisies à part :
        c'est ce qui les empêche de diverger de la vérité qu'elles résument."""
        depot = DepotTenantsSql(session_sql)
        tenant = _actif("station")
        depot.enregistrer(tenant)
        session_sql.flush()

        depot.enregistrer(suspendre(tenant, "honoraires échus", LE_JOUR))
        session_sql.flush()

        relu = depot.par_slug("station")
        assert relu.statut is StatutTenant.SUSPENDU
        assert relu.motif == "honoraires échus"

    def test_tous_les_rend_tries_par_slug(self, session_sql):
        """Un ordre stable : comparer deux exports d'un mois sur l'autre doit rester
        possible."""
        depot = DepotTenantsSql(session_sql)
        for slug in ("zebre", "alpha", "milieu"):
            depot.enregistrer(_actif(slug))
        session_sql.flush()

        assert [t.slug for t in depot.tous()] == ["alpha", "milieu", "zebre"]


class TestLUniciteEstArbitreeParLaBase:
    """Jamais par une lecture suivie d'une écriture : entre les deux, l'autre a écrit."""

    def test_deux_tenants_ne_partagent_pas_un_slug(self, session_sql):
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(_actif("station", identifiant="tnt-1"))
        session_sql.flush()

        depot.enregistrer(_actif("station", identifiant="tnt-2"))
        with pytest.raises(IntegrityError):
            session_sql.flush()

    def test_l_unicite_ignore_la_casse(self, session_sql):
        """« Station » et « station » désignent le même serveur. Les laisser coexister
        ferait que le premier trouvé gagne, et lequel dépendrait du plan d'exécution."""
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(_actif("station", identifiant="tnt-1"))
        session_sql.flush()

        depot.enregistrer(
            _actif("station", identifiant="tnt-2").model_copy(update={"slug": "STATION"})
        )
        with pytest.raises(IntegrityError):
            session_sql.flush()

    def test_la_base_arbitre_meme_sans_passer_par_le_depot(self, session_sql):
        """Le test qui donne son nom à cette classe, et qui manquait.

        ─────────────────────────────────────────────────────────────────────────
        Les deux cas ci-dessus passent par `DepotTenantsSql`, qui écrit
        `tenant.slug.lower()`. Ils étaient donc verts **grâce au dépôt**, et non
        grâce à la base : en retirant ce `.lower()`, le cas de la casse virait au
        rouge alors que la contrainte de la base est précisément ce qu'il prétend
        vérifier.

        Ce n'était pas une subtilité de test. Le modèle déclarait un `unique=True`
        ordinaire, sensible à la casse, là où la migration posait un index
        d'expression sur `lower(slug)`. Une base montée par `create_all` — celle
        de toute cette suite — n'avait donc pas la contrainte de la production, et
        rien ne pouvait le signaler tant que le dépôt normalisait pour elle.

        ⚠️ Ce cas écrit en SQL direct, sans dépôt et sans ORM. C'est le seul moyen
        de mesurer ce que **la base** refuse, indépendamment de ce que le code qui
        y mène a la bonne idée de faire.
        ─────────────────────────────────────────────────────────────────────────
        """
        from sqlalchemy import text

        insertion = text(
            "INSERT INTO tenant (identifiant, slug, statut, etape_atteinte, donnees) "
            "VALUES (:i, :s, 'ACTIF', 'ACTIVE', CAST('{}' AS json))"
        )
        session_sql.execute(insertion, {"i": "tnt-1", "s": "station"})
        session_sql.flush()

        # ⚠️ L'attente porte sur l'`execute`, pas sur un `flush`. En SQL direct
        # l'ordre part immédiatement ; c'est l'ORM qui diffère ses écritures
        # jusqu'au vidage, et les deux cas précédents en dépendent. Attendre au
        # mauvais endroit ferait échouer ce test **alors que la base refuse bien**,
        # ce qui est arrivé à sa première rédaction.
        with pytest.raises(IntegrityError):
            session_sql.execute(insertion, {"i": "tnt-2", "s": "Station"})


class TestLeModeleEtLaMigrationDisentLaMemeChose:
    """L'écart entre les deux ne se voit pas, et se paie en production.

    Il s'est vu ici en interrogeant `alembic check`, qui compare le schéma déclaré
    au schéma migré. Ce cas-là fige ce qu'il a trouvé, pour qu'une déclaration
    « simplifiée » en `unique=True` ne repasse pas sans bruit.
    """

    def test_l_unicite_du_slug_porte_sur_l_expression_pas_sur_la_colonne(self):
        """Un `UNIQUE` ordinaire sur la colonne serait sensible à la casse.

        Deux tenants dont les slugs ne diffèrent que par elle répondraient au même
        nom d'hôte, et le second servirait les données du premier.
        """
        from app.contextes.tenants.adaptateurs.sortant.tables import TableTenant

        index = {i.name: i for i in TableTenant.__table__.indexes}
        assert "uq_tenant_slug_insensible_casse" in index
        assert index["uq_tenant_slug_insensible_casse"].unique
        # La colonne elle-même ne porte aucune contrainte d'unicité : deux
        # contraintes diraient deux choses différentes, et la plus faible des deux
        # ne servirait qu'à faire diverger le modèle de la migration.
        assert not TableTenant.__table__.c.slug.unique


class TestLeDepotSatisfaitLePort:
    def test_il_a_la_meme_surface_que_le_repertoire_en_memoire(self, session_sql):
        """La passerelle ne doit pas savoir lequel elle emploie."""
        assert hasattr(DepotTenantsSql(session_sql), "par_slug")
        assert hasattr(RepertoireEnMemoire(), "par_slug")

    def test_les_deux_rendent_la_meme_chose_sur_les_memes_donnees(self, session_sql):
        tenant = _actif("station")
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(tenant)
        session_sql.flush()

        memoire = RepertoireEnMemoire([tenant])
        assert depot.par_slug("station") == memoire.par_slug("station")
        assert depot.par_slug("absent") == memoire.par_slug("absent")


class TestLaTableEstHorsCloisonnement:
    def test_elle_ne_porte_pas_la_colonne_de_locataire(self):
        """Et l'exemption est nommée dans `test_tables.py`, avec sa raison. Une exemption
        silencieuse serait indiscernable d'un mixin oublié."""
        from app.tables import METADONNEES

        assert "locataire" not in METADONNEES.tables["tenant"].c

    def test_elle_se_lit_sans_locataire_etabli(self, session_sql):
        """La preuve que la dépendance circulaire est évitée : lire le plan de contrôle
        ne demande pas de savoir de quel locataire il s'agit."""
        from app.partage.locataire import courant_ou_none

        assert courant_ou_none() is None
        DepotTenantsSql(session_sql).enregistrer(_actif("station"))
        session_sql.flush()
        assert DepotTenantsSql(session_sql).par_slug("station") is not None


class TestReprise:
    def test_les_ouvertures_interrompues_se_retrouvent(self, session_sql):
        """Ce que l'index de reprise sert à faire : « ce qui est en échec, et où »."""
        depot = DepotTenantsSql(session_sql)
        depot.enregistrer(_actif("termine"))
        interrompu = avancer(
            ouvrir("tnt-bloque", "bloque", NatureTenant.ENTREPRISE),
            EtapeOuverture.LIGNE_CREEE,
        )
        depot.enregistrer(interrompu)
        session_sql.flush()

        en_cours = [t for t in depot.tous() if t.statut is StatutTenant.EN_OUVERTURE]
        assert [t.slug for t in en_cours] == ["bloque"]
        assert en_cours[0].etape_atteinte is EtapeOuverture.LIGNE_CREEE
