"""Le registre durable des tenants : ce qui était annoncé fait, et ne l'était pas.

⚠️ **Le défaut que ce fichier garde est le plus grave rencontré sur ce chantier.**

L'ouverture d'un tenant écrivait dans un registre **en mémoire**, y compris en
persistance PostgreSQL. Mesuré sur une vraie base : un paiement encaissé, la saga
rendait `TERMINEE`, et la table `tenant` contenait zéro ligne.

Le système **annonçait le succès de ce qu'il n'avait pas fait**. Le client payait,
l'exécution était marquée terminée, l'événement publié, et le tenant disparaissait au
redémarrage suivant. Le sous-domaine rendait alors 404 sur un abonnement payé.

Rien n'échouait. C'est ce qui rend cette forme de défaut particulière, et c'est la
troisième fois que ce chantier la rencontre après les politiques de cloisonnement qui
ne s'appliquaient à personne et le gabarit de courriel qui n'existait pas.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.contextes.tenants.adaptateurs.sortant.depot_tenants_sql import DepotTenantsSql
from app.contextes.tenants.adaptateurs.sortant.registre_durable import RegistreDurable
from app.contextes.tenants.api import NatureTenant, RepertoireEnMemoire
from app.contextes.tenants.application.cycle_de_vie import activer, avancer, ouvrir
from app.contextes.tenants.domaine.tenant import ETAPES_ORDONNEES, Tenant
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LE_JOUR = date(2026, 9, 10)


def _actif(slug: str) -> Tenant:
    tenant = ouvrir(f"tnt-{slug}", slug, NatureTenant.ENTREPRISE)
    for etape in ETAPES_ORDONNEES[1:]:
        tenant = avancer(tenant, etape)
    return activer(tenant, LE_JOUR)


@pytest.fixture
def registre(session_sql):
    repertoire = RepertoireEnMemoire()
    return (
        RegistreDurable(DepotTenantsSql(session_sql), repertoire, session_sql),
        repertoire,
    )


class TestIlEcritVraimentEnBase:
    def test_un_tenant_enregistre_atterrit_dans_la_table(self, registre, session_sql):
        """Le défaut d'origine, figé.

        Avant cette correction, la même séquence laissait la table vide pendant que
        tout annonçait le succès.
        """
        durable, _ = registre
        durable.enregistrer(_actif("station-bonaberi"))

        lignes = list(session_sql.execute(text("SELECT slug, statut FROM tenant")))
        assert [(ligne.slug, ligne.statut) for ligne in lignes] == [
            ("station-bonaberi", "ACTIF")
        ]


class TestIlRendLeSousDomaineJoignableTOutDeSuite:
    def test_le_repertoire_apprend_l_ouverture(self, registre):
        """⚠️ Écrire en base ne suffit pas.

        La passerelle résout chaque nom d'hôte dans un répertoire **en mémoire**,
        garni depuis la table **au démarrage**. Sans inscription, le sous-domaine
        d'un client qui vient de payer ne répondrait qu'au prochain redéploiement.

        Un client qui règle attend que son espace s'ouvre, pas la prochaine
        livraison.
        """
        durable, repertoire = registre
        durable.enregistrer(_actif("station-bonaberi"))

        assert repertoire.par_slug("station-bonaberi") is not None

    def test_la_base_passe_avant_le_repertoire(self, registre, session_sql):
        """⚠️ L'ordre n'est pas indifférent.

        Si la base refuse — un slug déjà pris, une contrainte violée —, l'exception
        remonte et le répertoire n'a rien appris : la passerelle ne servira pas un
        tenant qui n'existe pas.

        L'ordre inverse laisserait un tenant joignable en mémoire et absent de la
        base, donc servi jusqu'au redémarrage puis évanoui : le défaut d'origine
        avec une fenêtre plus courte.
        """
        durable, repertoire = registre
        durable.enregistrer(_actif("station-bonaberi"))
        session_sql.commit()

        # Un second tenant, même slug à la casse près : la base doit refuser.
        # ⚠️ `IntegrityError` nommée plutôt qu'`Exception` : une attente aveugle
        # passerait aussi sur une faute de frappe dans le test lui-même, et ce cas
        # affirmerait alors que la base refuse là où c'est le test qui plante.
        autre = _actif("STATION-BONABERI").model_copy(update={"identifiant": "tnt-2"})
        with pytest.raises(IntegrityError):
            durable.enregistrer(autre)

        # Le répertoire n'a pas appris le second : il ne connaît que le premier.
        assert repertoire.par_slug("station-bonaberi").identifiant == "tnt-station-bonaberi"


class TestIlLitEnBaseEtNonAuRepertoire:
    def test_par_slug_trouve_un_tenant_absent_du_repertoire(self, registre, session_sql):
        """⚠️ Le répertoire peut être incomplet : il est garni au démarrage et
        n'apprend que ce qui passe par ce registre.

        Le provisionnement s'en sert pour savoir si un slug est déjà pris, et cette
        question ne souffre pas d'à-peu-près : deux tenants sur le même sous-domaine
        se serviraient l'un les données de l'autre.
        """
        DepotTenantsSql(session_sql).enregistrer(_actif("boulangerie"))
        session_sql.flush()

        durable, repertoire = registre
        assert repertoire.par_slug("boulangerie") is None
        assert durable.par_slug("boulangerie") is not None


class TestLeVidageRendLaRelectureFiable:
    def test_ce_qui_vient_d_etre_ecrit_se_relit_dans_la_meme_transaction(
        self, registre
    ):
        """⚠️ **Le défaut qui a suivi la première correction.**

        Ce projet coupe `autoflush` délibérément : il enverrait des `INSERT` au
        moindre `SELECT` intercalé et ferait échouer des contraintes loin du code
        fautif.

        Or la saga d'ouverture relit ce qu'elle vient d'écrire, pas après pas. Sans
        vidage explicite, elle s'est arrêtée au deuxième pas sur « tenant
        introuvable au répertoire », alors qu'il venait d'être écrit.

        ⚠️ Vider n'est pas valider : la transaction gouverne toujours l'atomicité.
        """
        durable, _ = registre
        durable.enregistrer(_actif("station-bonaberi"))

        assert durable.par_slug("station-bonaberi") is not None
