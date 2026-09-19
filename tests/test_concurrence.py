"""Ce qui ne casse qu'à plusieurs.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Un défaut de concurrence ne se voit ni en relecture, ni en test séquentiel. Celui
qui a motivé ce fichier a survécu à 993 tests et à une vérification complète : il
n'est apparu qu'en lançant **huit souscriptions simultanées** contre l'API réelle
— cinq sur huit en `500`.

La cause était un commentaire qui affirmait le contraire de la vérité. La lecture
de la tête de chaîne employait `SELECT … FOR UPDATE` en expliquant que « deux
transactions concurrentes ne peuvent pas lire le même rang maximal ». `FOR UPDATE`
verrouille les lignes **lues**, et ne dit rien de celles qui n'existent pas
encore : la seconde transaction relisait la même tête et recalculait le même rang.

Voir l'en-tête de `JournalAuditSql` pour le déroulé complet.

⚠️ CE TEST DOIT VRAIMENT ÊTRE CONCURRENT

Des fils, des sessions distinctes, une barrière pour qu'ils partent ensemble.
Un test qui simulerait la concurrence en séquence ne reproduirait rien : c'est
précisément le chevauchement qui révèle le défaut.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sqlalchemy.orm import Session as SessionSql

from app.contextes.transverse.adaptateurs.sortant.depots_sql import JournalAuditSql
from app.contextes.transverse.api import verifier_chaine
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

INSTANT = datetime(2026, 8, 15, 10, 0)
CABINET = "CGA-BRCG"
AUTRE = "CGA-CONCURRENT"


def _ecrire(moteur, locataire: str, indice: int, depart: threading.Barrier) -> str | None:
    """Ajoute une entrée d'audit dans sa propre transaction. Rend le motif d'échec."""
    with SessionSql(moteur) as session:
        journal = JournalAuditSql(session, locataire)
        # La barrière fait partir tous les fils au même instant : sans elle, le
        # premier a le temps de valider avant que le second ne lise.
        depart.wait(timeout=30)
        try:
            journal.ajouter(
                horodatage=INSTANT,
                acteur=f"C-{indice:03d}",
                action="essai.concurrent",
                objet_type="Essai",
                objet_id=str(indice),
            )
            session.commit()
            return None
        except Exception as echec:  # noqa: BLE001 — on veut le motif, quel qu'il soit
            session.rollback()
            return f"{type(echec).__name__}: {str(echec).splitlines()[0][:90]}"


class TestJournalAuditConcurrent:
    def test_douze_ajouts_simultanes_aboutissent_tous(self, moteur_test):
        """Le test de non-régression du défaut trouvé en charge réelle.

        Sans le verrou consultatif, plusieurs de ces écritures échouent sur
        `duplicate key … (locataire, rang)`.
        """
        n = 12
        depart = threading.Barrier(n)
        with ThreadPoolExecutor(max_workers=n) as pool:
            echecs = [
                e
                for e in pool.map(
                    lambda i: _ecrire(moteur_test, CABINET, i, depart), range(n)
                )
                if e is not None
            ]
        assert not echecs, (
            f"{len(echecs)} écriture(s) d'audit sur {n} ont échoué en concurrence :\n  "
            + "\n  ".join(echecs[:4])
        )

    def test_les_rangs_restent_uniques_et_contigus(self, moteur_test):
        """Une chaîne à trous ne se vérifie plus, et une chaîne à doublons non plus."""
        n = 10
        depart = threading.Barrier(n)
        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(lambda i: _ecrire(moteur_test, CABINET, i, depart), range(n)))

        with SessionSql(moteur_test) as session:
            entrees = JournalAuditSql(session, CABINET).lister()
        rangs = sorted(e.rang for e in entrees)
        assert len(rangs) == n
        assert rangs == list(range(1, n + 1)), f"rangs obtenus : {rangs}"

    def test_la_chaine_se_verifie_apres_ecriture_concurrente(self, moteur_test):
        """Le contrôle qui compte vraiment.

        Des rangs uniques ne suffisent pas : chaque entrée porte l'empreinte de
        la précédente. Si deux écritures s'étaient chaînées sur la même tête, la
        vérification le dirait — même avec des rangs corrects.
        """
        n = 10
        depart = threading.Barrier(n)
        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(lambda i: _ecrire(moteur_test, CABINET, i, depart), range(n)))

        with SessionSql(moteur_test) as session:
            entrees = JournalAuditSql(session, CABINET).lister()
        # ⚠️ `verifier_chaine` **lève** au premier défaut plutôt que de rendre un
        # verdict. C'est le bon choix pour une chaîne d'audit : un appelant qui
        # oublie de regarder un booléen ne remarque rien, alors qu'une exception
        # ne se rate pas.
        verifier_chaine(sorted(entrees, key=lambda e: e.rang))

    def test_deux_locataires_ne_s_attendent_pas(self, moteur_test):
        """Le verrou est **par locataire**, et c'est ce qui rend son coût tenable.

        Chacun repart de son propre rang 1 : la sérialisation ne les mélange pas.
        """
        n = 6
        depart = threading.Barrier(2 * n)

        def travail(i: int) -> str | None:
            locataire = CABINET if i % 2 == 0 else AUTRE
            return _ecrire(moteur_test, locataire, i, depart)

        with ThreadPoolExecutor(max_workers=2 * n) as pool:
            echecs = [e for e in pool.map(travail, range(2 * n)) if e is not None]
        assert not echecs, echecs[:3]

        with SessionSql(moteur_test) as session:
            a = JournalAuditSql(session, CABINET).lister()
            b = JournalAuditSql(session, AUTRE).lister()
        assert sorted(e.rang for e in a) == list(range(1, n + 1))
        assert sorted(e.rang for e in b) == list(range(1, n + 1))


@pytest.mark.parametrize("tentative", range(3))
def test_le_defaut_ne_revient_pas(moteur_test, tentative: int):
    """Répété : un défaut de concurrence est intermittent par nature.

    Un seul passage vert ne prouve pas grand-chose — l'entrelacement fautif peut
    ne pas s'être produit. Trois passages rendent le silence plus crédible.
    """
    n = 8
    depart = threading.Barrier(n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        echecs = [
            e
            for e in pool.map(lambda i: _ecrire(moteur_test, CABINET, i, depart), range(n))
            if e is not None
        ]
    assert not echecs, echecs[:3]
