"""Le suivi de relance en base : ce qui survit au redémarrage, et pourquoi il le doit.

⚠️ **En mémoire, un redémarrage oublie ce qui a été relancé**, et le balayage suivant
repart au premier palier : le client reçoit une seconde fois le message qu'il a déjà
reçu. C'est la raison d'être de cette table, et c'est pourquoi la persistance mémoire
n'est pas un mode d'exploitation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotSuivisDeRelanceSql,
)
from app.contextes.souscription.domaine.relance import SuiviDeRelance
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

T0 = datetime(2026, 9, 10, 9, 0)


class TestAllerRetour:
    def test_un_suivi_ecrit_se_relit_a_l_identique(self, session_sql):
        depot = DepotSuivisDeRelanceSql(session_sql, "CGA-BRCG")
        depot.enregistrer(SuiviDeRelance(proforma="PRO-2026-0001").avec_les((1, 2), T0))
        session_sql.commit()

        relu = depot.tous()["PRO-2026-0001"]
        assert relu.rangs_envoyes == (1, 2)
        assert relu.derniere_le == T0

    def test_le_balayage_reecrit_le_meme_suivi_a_chaque_palier(self, session_sql):
        """⚠️ Un `add` lèverait sur la clé primaire dès le second palier, c'est-à-dire
        dès la première proforma relancée deux fois."""
        depot = DepotSuivisDeRelanceSql(session_sql, "CGA-BRCG")
        depot.enregistrer(SuiviDeRelance(proforma="PRO-2026-0001").avec(1, T0))
        session_sql.flush()
        depot.enregistrer(
            SuiviDeRelance(proforma="PRO-2026-0001").avec_les((1, 2), T0 + timedelta(days=4))
        )
        session_sql.flush()

        assert len(depot.tous()) == 1
        assert depot.tous()["PRO-2026-0001"].rangs_envoyes == (1, 2)

    def test_un_suivi_absent_rend_l_absence_et_ne_leve_pas(self, session_sql):
        """Il veut dire « jamais relancée », qui est l'état de toute proforma le jour
        de sa transmission. C'est pourquoi le port n'a pas de `lire` qui lève : le
        balayage fabrique alors un suivi vide, et un `lire` obligerait chaque
        appelant à rattraper une exception pour faire exactement cela.
        """
        assert DepotSuivisDeRelanceSql(session_sql, "CGA-BRCG").tous() == {}


class TestLaCleEstComposite:
    def test_deux_cabinets_portent_le_meme_numero_de_proforma(self, session_sql):
        """⚠️ Un numéro de proforma est **séquentiel par cabinet** : `PRO-2026-0001`
        existe chez chacun d'eux.

        Une clé sur le seul numéro ferait que le premier cabinet à relancer
        empêcherait tous les autres d'inscrire leur suivi, et le refus citerait une
        contrainte de clé primaire sans rapport apparent avec le cloisonnement.
        """
        DepotSuivisDeRelanceSql(session_sql, "CGA-BRCG").enregistrer(
            SuiviDeRelance(proforma="PRO-2026-0001").avec(1, T0)
        )
        DepotSuivisDeRelanceSql(session_sql, "CGA-AUTRE").enregistrer(
            SuiviDeRelance(proforma="PRO-2026-0001").avec(1, T0)
        )
        session_sql.flush()

        assert DepotSuivisDeRelanceSql(session_sql, "CGA-BRCG").tous().keys() == {
            "PRO-2026-0001"
        }
        assert DepotSuivisDeRelanceSql(session_sql, "CGA-AUTRE").tous().keys() == {
            "PRO-2026-0001"
        }

    def test_le_meme_cabinet_ne_porte_pas_deux_fois_le_meme_numero(self, session_sql):
        """La contre-épreuve : sans elle, le cas précédent passerait sur une table
        sans contrainte du tout."""
        from sqlalchemy import text

        insertion = text(
            "INSERT INTO suivi_de_relance (locataire, proforma, derniere_le, donnees) "
            "VALUES ('CGA-BRCG', 'PRO-2026-0001', NULL, CAST('{}' AS json))"
        )
        session_sql.execute(insertion)
        session_sql.flush()
        with pytest.raises(IntegrityError):
            session_sql.execute(insertion)


class TestLeSuiviVitAPartDuDocument:
    def test_la_table_ne_porte_aucune_colonne_de_la_proforma(self):
        """⚠️ La proforma est figée et vaut contrat : ce qui a été envoyé à un client
        doit ressortir à l'identique dix ans plus tard.

        Y inscrire un compteur de relances ferait changer un document contractuel
        pour une raison qui n'a rien de contractuel, et chaque balayage réécrirait
        une ligne dont la stabilité est la propriété qu'on lui demande.
        """
        from app.contextes.souscription.adaptateurs.sortant.tables import (
            TableProforma,
            TableSuiviDeRelance,
        )

        assert "rangs_envoyes" not in TableProforma.__table__.c
        assert "derniere_relance_le" not in TableProforma.__table__.c
        assert set(TableSuiviDeRelance.__table__.c) & {"montant", "etat", "empreinte"} == set()
