"""L'exclusion entre instances : deux processus, un seul qui travaille.

⚠️ **Rien de ce fichier ne peut être vérifié avec une doublure.** Ce qui est en jeu
est le comportement de PostgreSQL sur ses verrous consultatifs : qui l'obtient, qui
ne l'obtient pas, et quand il tombe. Une doublure ne répondrait que ce qu'on lui
aurait appris, c'est-à-dire ce qu'on croit déjà.

CE QUE CES CAS PROTÈGENT

Le déploiement prévoit deux conteneurs derrière un répartiteur, ne serait-ce que le
temps d'une mise à jour sans coupure. Pendant ces quelques secondes, deux
ordonnanceurs tournent. Sans verrou, les deux balaient les mêmes proformas, et le
client reçoit deux relances identiques à la même seconde.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.verrou import cle_de_verrou, verrou_exclusif
from tests.conftest import URL_BASE_TEST, exige_postgresql

pytestmark = exige_postgresql

TRAVAIL = "essai-ordonnanceur"


@pytest.fixture
def deux_instances():
    """Deux sessions distinctes : c'est ce que sont deux conteneurs, vu de la base."""
    moteur = create_engine(URL_BASE_TEST, pool_pre_ping=True)
    a, b = Session(moteur), Session(moteur)
    yield a, b
    a.rollback()
    b.rollback()
    a.close()
    b.close()
    moteur.dispose()


class TestLExclusion:
    def test_une_seule_instance_obtient_le_verrou(self, deux_instances):
        a, b = deux_instances
        with verrou_exclusif(a, TRAVAIL) as premier, verrou_exclusif(b, TRAVAIL) as second:
            assert premier is True
            assert second is False

    def test_celle_qui_ne_l_obtient_pas_ne_leve_pas(self, deux_instances):
        """Passer son tour est le fonctionnement normal, pas une panne.

        Lever obligerait chaque appelant à rattraper une exception pour n'en rien
        faire, et un `except` qui ne fait rien finit toujours par en avaler une
        autre.
        """
        a, b = deux_instances
        with verrou_exclusif(a, TRAVAIL), verrou_exclusif(b, TRAVAIL) as second:
            assert second is False  # aucune exception n'a été levée pour arriver ici

    def test_deux_travaux_differents_ne_se_bloquent_pas(self, deux_instances):
        """Le balayage de relance ne doit pas attendre que le relais ait fini."""
        a, b = deux_instances
        with verrou_exclusif(a, TRAVAIL) as premier, verrou_exclusif(b, "un-autre") as autre:
            assert premier is True
            assert autre is True

    def test_le_verrou_tombe_a_la_fin_de_la_transaction(self, deux_instances):
        """La propriété qui dispense de tout nettoyage.

        ⚠️ C'est ce qui distingue ce mécanisme d'une table de verrous : rien ne
        reste à dire « quelqu'un travaille » quand personne ne travaille plus, donc
        rien à expirer, donc aucune horloge à arbitrer entre deux machines.
        """
        a, b = deux_instances
        with verrou_exclusif(a, TRAVAIL) as premier:
            assert premier is True
        a.commit()

        with verrou_exclusif(b, TRAVAIL) as apres:
            assert apres is True

    def test_il_tombe_aussi_sur_une_annulation(self, deux_instances):
        """Le cas du travail qui a échoué. Un verrou rendu seulement au succès
        resterait pris précisément quand quelque chose ne va pas."""
        a, b = deux_instances
        with verrou_exclusif(a, TRAVAIL):
            pass
        a.rollback()

        with verrou_exclusif(b, TRAVAIL) as apres:
            assert apres is True


class TestLaCle:
    def test_le_meme_nom_donne_toujours_la_meme_cle(self):
        """⚠️ La propriété sans laquelle l'exclusion n'existe pas.

        `hash()` de Python est **salé par processus** depuis la 3.3 : deux
        instances calculeraient deux clés pour le même nom, chacune prendrait son
        verrou, et le défaut serait invisible sur une machine de développement à un
        seul processus.
        """
        assert cle_de_verrou("relais") == cle_de_verrou("relais")

    def test_deux_noms_donnent_deux_cles(self):
        assert cle_de_verrou("relais") != cle_de_verrou("relance")

    def test_la_cle_est_une_valeur_figee_et_non_recalculable_au_hasard(self):
        """Le cas qui attrape un changement d'algorithme d'empreinte.

        Changer l'empreinte ferait qu'une instance déployée et une instance encore
        à l'ancienne version ne s'excluraient plus — c'est-à-dire exactement
        pendant une mise à jour sans coupure, le moment où deux versions tournent
        ensemble et où le verrou sert le plus.
        """
        assert cle_de_verrou("relais") == 3895035964816193817

    def test_la_cle_tient_dans_un_bigint_signe(self):
        """`pg_try_advisory_xact_lock` prend un `bigint`. Au delà, PostgreSQL refuse."""
        for nom in ("relais", "relance", "reprise-des-sagas", "un-nom-très-long" * 4):
            assert -(2**63) <= cle_de_verrou(nom) < 2**63

    def test_une_cle_hors_intervalle_serait_refusee_par_la_base(self, deux_instances):
        """La contre-épreuve du cas précédent : sans elle, il n'affirme rien.

        Vérifier qu'un nombre tient dans un intervalle ne prouve pas que
        l'intervalle est le bon. Ce cas demande à PostgreSQL ce qu'il fait d'un
        dépassement, et constate qu'il refuse plutôt que de tronquer en silence.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        a, _ = deux_instances
        with pytest.raises(DBAPIError):
            a.execute(text(f"SELECT pg_try_advisory_xact_lock({2**63})"))
