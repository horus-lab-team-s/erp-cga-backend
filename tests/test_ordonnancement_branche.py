"""L'ordonnanceur branché : ce qui se passe réellement quand un tour tourne.

⚠️ **Ce fichier vérifie la couture, pas la décision.** Quels travaux sont dus et à
quel rythme se vérifie sans base, à `test_ordonnanceur.py`. Ici on veut savoir si le
passage survit au redémarrage, si deux instances s'excluent, et si un événement
déposé finit par sortir de la boîte.

CE QUE LA COUTURE A APPRIS

Le premier tour hors requête a échoué sur `LocataireNonEtabli`. L'intergiciel HTTP
fait deux choses que l'on croyait n'en faire qu'une : il ouvre l'unité de travail
**et** il établit le locataire courant. La route ne pouvait pas révéler le manque,
puisqu'elle s'exécute toujours après l'intergiciel. C'est la raison d'être de ce
fichier : le chemin hors requête n'a pas d'autre témoin.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.infrastructure.depots_orchestration import BoiteDEnvoiSql, DepotPassagesSql
from app.orchestration.boite_d_envoi import deposer
from app.orchestration.ordonnanceur import Passage
from app.partage.horloge import maintenant
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LOCATAIRE = "CGA-BRCG"


@pytest.fixture
def en_base(monkeypatch, moteur_test):
    """L'application en persistance PostgreSQL, sur la base de test.

    ⚠️ `moteur_test` est demandé pour son effet de bord : il crée le schéma. Sans
    lui, `tour_hors_requete` ouvrirait sa propre session sur une base sans tables.
    """
    from app.infrastructure import base_de_donnees, config

    monkeypatch.setenv("CGA_PERSISTANCE", "postgresql")
    monkeypatch.setenv("CGA_URL_BASE_DE_DONNEES", str(moteur_test.url).replace("***", "cga"))
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()

    # ⚠️ **La composition est jouée, et il le faut.** Les travaux périodiques sont
    # inscrits par `creer_application()` : un tour appelé sans elle trouve un
    # registre vide et ne fait rien. La première rédaction de ces cas l'a découvert
    # au rouge, ce qui a conduit à ajouter `aucun_travail_inscrit` au rapport pour
    # que ce silence cesse d'être silencieux.
    from app.main import creer_application

    creer_application()

    yield
    config.configuration.cache_clear()
    base_de_donnees.moteur.cache_clear()


def _tour():
    from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
        tour_hors_requete,
    )

    return tour_hors_requete()


class TestLeTourHorsRequete:
    def test_il_etablit_son_locataire_et_ne_leve_pas(self, en_base):
        """Le cas qui a échoué la première fois, et pour la bonne raison.

        Le refus disait qu'une lecture sans locataire servirait des lignes
        arbitraires sans que rien ne le signale. C'est ce refus qui a rendu la
        couture visible, là où une valeur par défaut l'aurait laissée passer.
        """
        rapport = _tour()
        assert rapport.execute
        assert not rapport.aucun_travail_inscrit
        # ⚠️ **L'ordre est celui de la conséquence d'une panne, pas celui du code.**
        # Le relais d'abord : c'est le chemin vital, celui qui ouvre les tenants
        # payés. La relance ensuite : elle écrit à de vrais clients.
        #
        # ⚠️ **Puis la reprise, et la veille en dernier**, et cet ordre-ci porte
        # un sens que le tour rendrait faux s'il était inversé. La reprise remet
        # l'ancienneté du dossier à zéro ; passant après, elle agirait sur des
        # dossiers que la veille vient de signaler dans le même tour, et un
        # responsable de pôle recevrait une alerte sur un dossier déjà repris.
        # ⚠️ **La réconciliation juste après le relais.** C'est le seul travail qui
        # touche à de l'argent déjà encaissé : un règlement dont la notification
        # s'est perdue est un client qui a payé et dont l'espace ne s'ouvre pas.
        # Elle passe donc avant la relance, qui réclamerait sinon son règlement à
        # quelqu'un qui vient de payer.
        assert [r.travail for r in rapport.resultats] == [
            "relais",
            "reconciliation-des-paiements",
            "relance",
            "reprise-des-dossiers",
            "veille-des-dossiers",
            # Pas 115 : les rappels d'échéance des adhérents, en dernier. Un rappel à J-7 n'est pas
            # à la minute, et il ne doit retarder ni l'ouverture d'un tenant payé ni une relance.
            "rappels-d-echeance",
        ]

    def test_le_passage_survit_au_tour_suivant(self, en_base, moteur_test):
        """Ce que la table existe pour faire, et ce que la mémoire ne fait pas."""
        _tour()
        with moteur_test.connect() as connexion:
            lignes = list(
                connexion.execute(
                    text("SELECT * FROM passage_ordonnance ORDER BY travail")
                )
            )
        # ⚠️ Trié par **nom** en SQL, et non par ordre d'exécution : « relais »,
        # « relance », « reprise-des-dossiers », « veille-des-dossiers ». Que les
        # deux ordres coïncident ici est une coïncidence de vocabulaire, pas une
        # propriété ; le cas précédent garde l'ordre d'exécution.
        # ⚠️ Trié par **nom** en SQL, et non par ordre d'exécution : le cas
        # précédent garde l'ordre d'exécution, celui-ci garde la persistance.
        assert [ligne.travail for ligne in lignes] == [
            "rappels-d-echeance",
            "reconciliation-des-paiements",
            "relais",
            "relance",
            "reprise-des-dossiers",
            "veille-des-dossiers",
        ]
        assert all(ligne.termine_le is not None for ligne in lignes)
        assert all(ligne.echecs_consecutifs == 0 for ligne in lignes)

    def test_un_second_tour_immediat_ne_refait_rien(self, en_base):
        """La cadence est tenue **entre deux processus**, pas seulement dans une boucle.

        C'est tout l'intérêt d'un état en base : un ordonnanceur extérieur qui
        appellerait la route dix fois par seconde ne ferait pas dix fois le travail.
        """
        _tour()
        second = _tour()
        assert second.execute
        assert second.resultats == []

    def test_le_debut_et_la_fin_sont_ecrits_ensemble(self, en_base, moteur_test):
        """⚠️ Les écrire séparément demanderait de valider entre les deux, donc de
        rendre le verrou consultatif au milieu du tour : il tombe à la validation."""
        _tour()
        with moteur_test.connect() as connexion:
            lignes = list(connexion.execute(text("SELECT * FROM passage_ordonnance")))
        assert lignes
        assert all(ligne.debute_le is not None for ligne in lignes)
        assert all(ligne.termine_le is not None for ligne in lignes)


class TestLExclusionAuNiveauDuTour:
    def test_une_instance_qui_n_a_pas_le_verrou_ne_fait_rien_et_le_dit(
        self, en_base, moteur_test
    ):
        """`execute=False` n'est pas une erreur : c'est le fonctionnement voulu.

        L'appelant doit pouvoir le distinguer d'un tour qui n'avait rien à faire,
        sans quoi un exploitant conclurait que sa cadence est mal réglée.
        """
        from sqlalchemy.orm import Session

        from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
            VERROU_DU_TOUR,
        )
        from app.infrastructure.verrou import verrou_exclusif

        # Une autre instance tient déjà le verrou du tour.
        autre = Session(moteur_test)
        with verrou_exclusif(autre, VERROU_DU_TOUR) as pris:
            assert pris
            rapport = _tour()
        autre.rollback()
        autre.close()

        assert rapport.execute is False
        assert rapport.resultats == []


class TestUnEvenementTraverse:
    def test_un_evenement_depose_finit_par_etre_publie_par_un_tour(
        self, en_base, moteur_test, monkeypatch
    ):
        """La boucle complète : déposé, dû, publié.

        ⚠️ Sans consommateur abonné, l'événement est publié quand même et compté
        `sans_abonne`. C'est voulu depuis le pas 7 : un événement que personne
        n'écoute n'est pas une erreur, et le retenir indéfiniment ferait grossir la
        boîte au lieu de signaler l'absence d'abonné.
        """
        from sqlalchemy.orm import Session

        session = Session(moteur_test)
        boite = BoiteDEnvoiSql(session, LOCATAIRE)
        deposer(
            boite,
            identifiant="ev-1",
            nom="EssaiDOrdonnancement",
            cle="essai",
            charge={"quoi": "rien"},
            a_l_instant=maintenant(),
        )
        session.commit()

        rapport = _tour()
        assert rapport.execute
        assert "1 publié(s)" in rapport.resultats[0].compte_rendu

        restants = session.execute(
            text("SELECT count(*) FROM boite_d_envoi WHERE publie_le IS NULL")
        ).scalar_one()
        session.close()
        assert restants == 0


class TestUnTravailQuiEchoue:
    def test_l_echec_est_compte_en_base_et_n_arrete_pas_le_tour(
        self, en_base, moteur_test, monkeypatch
    ):
        """Le compteur d'échecs est ce qui allonge le recul, puis conduit à l'abandon.

        S'il ne survivait pas au tour, un travail cassé serait retenté à pleine
        cadence indéfiniment, et le recul ne s'appliquerait jamais.
        """
        import app.contextes.transverse.adaptateurs.entrant.routes_orchestration as routes

        def _casse(*_args, **_kwargs):
            raise RuntimeError("abonné injoignable")

        monkeypatch.setattr(routes, "publier_un_lot", _casse)
        rapport = _tour()

        assert rapport.execute
        relais = next(r for r in rapport.resultats if r.travail == "relais")
        assert relais.reussi is False
        assert "abonné injoignable" in relais.compte_rendu
        # ⚠️ Et le balayage a tourné quand même : un travail qui lève n'arrête pas
        # les suivants. C'est la propriété qui compte du tour.
        assert any(r.travail == "relance" and r.reussi for r in rapport.resultats)

        with moteur_test.connect() as connexion:
            ligne = connexion.execute(
                text("SELECT * FROM passage_ordonnance WHERE travail = 'relais'")
            ).one()
        assert ligne.echecs_consecutifs == 1
        assert "abonné injoignable" in ligne.dernier_echec


class TestLeDepotDesPassages:
    def test_reecrire_un_passage_ne_leve_pas_sur_la_cle_primaire(self, moteur_test):
        """⚠️ Le défaut qui tomberait deux minutes après le démarrage.

        Un travail écrit son passage à chaque tour, et la ligne existe déjà dès le
        second. Un `add` lèverait alors sur la clé primaire, à chaque tour sauf le
        premier — ce qui passe toutes les revues et aucune exploitation.
        """
        from sqlalchemy.orm import Session

        session = Session(moteur_test)
        depot = DepotPassagesSql(session)
        instant = maintenant()
        depot.enregistrer(Passage(travail="essai", termine_le=instant))
        session.flush()
        depot.enregistrer(
            Passage(travail="essai", termine_le=instant + timedelta(seconds=5))
        )
        session.flush()

        assert len(depot.tous()) == 1
        assert depot.tous()["essai"].termine_le == instant + timedelta(seconds=5)
        session.rollback()
        session.close()


class TestUnOrdonnanceurSansTravail:
    """⚠️ Un défaut de composition, et non un état de repos.

    Un ordonnanceur sans travail tourne indéfiniment sans rien faire : la boîte
    d'envoi ne se vide plus, les tenants payés ne s'ouvrent plus, et **aucune requête
    n'échoue pour le signaler**. Rien ne le distingue d'un ordonnanceur dont rien
    n'est dû.

    Le cas s'est présenté ici même : les travaux sont inscrits par la composition de
    l'application, et ces cas appelaient le tour sans elle. Ils sont passés au rouge
    pour la bonne raison, et le rapport porte désormais le constat.
    """

    def test_un_registre_vide_est_signale_dans_le_rapport(self, en_base):
        from app.main import creer_application
        from app.orchestration.inscription import oublier_les_travaux

        oublier_les_travaux()
        try:
            rapport = _tour()
            assert rapport.execute
            assert rapport.aucun_travail_inscrit
            assert rapport.resultats == []
        finally:
            # ⚠️ Rendu à l'état composé : les cas suivants du module partagent le
            # registre, qui est un état de processus.
            creer_application()


class TestUnPaiementOuvreVraimentUnTenant:
    """⚠️ **Le défaut le plus grave du chantier, et son seul témoin possible.**

    ─────────────────────────────────────────────────────────────────────────────
    L'ouverture d'un tenant écrivait dans un registre **en mémoire**, y compris en
    persistance PostgreSQL. La saga rendait `TERMINEE`, l'événement était publié, et
    la table `tenant` contenait zéro ligne.

    Le système **annonçait le succès de ce qu'il n'avait pas fait**. Le tenant
    disparaissait au redémarrage, et le sous-domaine d'un abonnement payé rendait
    404.

    Aucun test de domaine ne pouvait l'attraper : la saga était correcte, le
    provisionneur était correct, le dépôt SQL était correct. **C'est le câblage qui
    était faux**, et seul un parcours complet le montre.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def _encaisser(self, slug: str = "station-bonaberi") -> None:
        from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
            Atelier,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.orchestration.boite_d_envoi import deposer
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG"):
            deposer(
                Atelier().boite,
                "ev-paiement-1",
                "PaiementEncaissé",
                "dos-1",
                {
                    "tenant": f"tnt-{slug}",
                    "slug": slug,
                    "proforma": "PRO-2026-0001",
                    "version_proforma": 1,
                    "reference_externe": "MTN-42",
                },
                maintenant(),
            )

    def test_le_tenant_paye_atterrit_dans_la_table(self, en_base, moteur_test):
        """La ligne qui manquait. Sans elle, l'abonnement n'existe nulle part."""
        self._encaisser()
        _tour()

        with moteur_test.connect() as connexion:
            lignes = list(
                connexion.execute(text("SELECT slug, statut, etape_atteinte FROM tenant"))
            )
        assert [(ligne.slug, ligne.statut) for ligne in lignes] == [
            ("station-bonaberi", "ACTIF")
        ]
        assert lignes[0].etape_atteinte == "PRET"

    def test_la_saga_va_jusqu_au_bout(self, en_base, moteur_test):
        """⚠️ Elle rendait déjà `TERMINEE` **avant** la correction, sur un registre
        en mémoire. Ce cas ne suffit donc pas seul : il accompagne le précédent.

        Il a sa valeur propre après la seconde correction. Le registre durable
        écrivait sans vider la session, et la saga s'arrêtait alors au deuxième pas
        sur « tenant introuvable au répertoire », alors qu'il venait d'être écrit.
        """
        self._encaisser()
        _tour()

        with moteur_test.connect() as connexion:
            ligne = connexion.execute(
                text("SELECT etat, donnees FROM execution_saga")
            ).one()
        assert ligne.etat == "TERMINEE"
        assert len(ligne.donnees["franchies"]) == 7

    def test_le_sous_domaine_repond_sans_attendre_un_redemarrage(self, en_base):
        """⚠️ Écrire en base ne suffit pas.

        La passerelle résout chaque nom d'hôte dans un répertoire en mémoire, garni
        depuis la table **au démarrage**. Sans inscription, le sous-domaine d'un
        client qui vient de payer ne répondrait qu'au prochain redéploiement.
        """
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            repertoire_des_tenants,
        )

        self._encaisser()
        _tour()

        tenant = repertoire_des_tenants().par_slug("station-bonaberi")
        assert tenant is not None
        assert tenant.statut.value == "ACTIF"
