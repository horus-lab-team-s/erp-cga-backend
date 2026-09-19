"""La qualification conservée : ce que la route jetait, et ce qu'elle reprend.

⚠️ **Ce qui manquait n'était pas la table, c'était l'écriture.** La route construisait
la qualification, validait chaque réponse au type de sa question, rendait l'avancement
et les faits, puis **n'en gardait rien**.

Un responsable qui répondait à cinq questions sur douze et revenait le lendemain
recommençait à zéro, sans qu'aucune erreur ne se produise. Quatrième rencontre sur ce
chantier d'un geste qui annonce un résultat qu'il n'a pas produit.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotQualificationsSql,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.domaine.qualification import (
    SourceReponse,
    ouvrir_une_qualification,
)
from app.infrastructure.config import RACINE_DEPOT
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

T0 = datetime(2026, 9, 10, 9, 0)
DOSSIER_QUESTIONNAIRES = RACINE_DEPOT / "Docs" / "referentiel" / "qualification"


@pytest.fixture(scope="module")
def questionnaire():
    """Le questionnaire réel du référentiel, et non un questionnaire d'essai.

    ⚠️ Un questionnaire fabriqué ici vérifierait le mécanisme et tairait une
    incohérence entre le domaine et ce que le centre a réellement rédigé.
    """
    return RepertoireDeQuestionnaires.depuis(DOSSIER_QUESTIONNAIRES).prendre(
        "creation-sarl"
    )


def _avec(qualification, questionnaire, *reponses):
    for code, valeur in reponses:
        qualification = qualification.repondre(
            questionnaire, code, valeur, T0, source=SourceReponse.DECLAREE
        )
    return qualification


class TestElleSurvitEntreDeuxPassages:
    def test_une_qualification_partielle_se_reprend(self, session_sql, questionnaire):
        """⚠️ **Le défaut d'origine, figé.**

        La qualification se complète au fil des échanges avec le client : on
        apprend rarement tout d'un coup. Sans reprise, chaque appel repartait de
        zéro.
        """
        depot = DepotQualificationsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(
            _avec(
                ouvrir_une_qualification("dos-1", questionnaire),
                questionnaire,
                ("forme_juridique", "SARL"),
                ("associes", 2),
            )
        )
        session_sql.flush()

        repris = depot.trouver("dos-1")
        assert repris is not None
        assert repris.avancement(questionnaire)[0] == 2

        depot.enregistrer(
            _avec(repris, questionnaire, ("capital_social", "1000000"))
        )
        session_sql.flush()

        assert depot.trouver("dos-1").avancement(questionnaire)[0] == 3

    def test_les_faits_survivent_avec_leur_type(self, session_sql, questionnaire):
        """Les faits alimentent le chiffrage : un entier relu en chaîne ferait
        échouer une règle sans que rien ne dise pourquoi."""
        depot = DepotQualificationsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(
            _avec(
                ouvrir_une_qualification("dos-1", questionnaire),
                questionnaire,
                ("forme_juridique", "SARL"),
                ("associes", 3),
            )
        )
        session_sql.flush()

        faits = depot.trouver("dos-1").faits()
        assert faits["associes"] == 3
        assert isinstance(faits["associes"], int)

    def test_une_qualification_absente_rend_none(self, session_sql):
        """⚠️ Et ne lève pas. « Pas encore commencée » est l'état de tout dossier
        neuf : ce n'est pas une anomalie, et lever obligerait chaque appelant à
        rattraper une exception pour ouvrir une qualification vide."""
        assert DepotQualificationsSql(session_sql, "CGA-BRCG").trouver("dos-x") is None


class TestLaVersionDuQuestionnaireEstConservee:
    def test_elle_accompagne_les_reponses(self, session_sql, questionnaire):
        """⚠️ C'est ce qui rend une qualification relisible.

        Un questionnaire évolue. Une question ajoutée rendrait « incomplètes »
        toutes les qualifications déjà closes si l'on mesurait l'avancement contre
        la version du jour.
        """
        depot = DepotQualificationsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(ouvrir_une_qualification("dos-1", questionnaire))
        session_sql.flush()

        assert depot.trouver("dos-1").version_questionnaire == questionnaire.version

    def test_elle_est_promue_en_colonne(self, session_sql, questionnaire):
        """Promue pour être interrogeable : « quelles qualifications emploient
        encore la version 1 » se pose avant de retirer un questionnaire."""
        DepotQualificationsSql(session_sql, "CGA-BRCG").enregistrer(
            ouvrir_une_qualification("dos-1", questionnaire)
        )
        session_sql.flush()

        ligne = session_sql.execute(
            text("SELECT service, version_questionnaire FROM qualification")
        ).one()
        assert ligne.service == "creation-sarl"
        assert ligne.version_questionnaire == questionnaire.version


class TestUneLigneParDossier:
    def test_reecrire_ne_cree_pas_une_seconde_ligne(self, session_sql, questionnaire):
        """La qualification est un **état**, pas un journal. Conserver chaque
        passage obligerait à trier pour reconstituer l'état courant, alors que la
        question posée est toujours « où en est-on »."""
        depot = DepotQualificationsSql(session_sql, "CGA-BRCG")
        depot.enregistrer(ouvrir_une_qualification("dos-1", questionnaire))
        session_sql.flush()
        depot.enregistrer(
            _avec(
                depot.trouver("dos-1"), questionnaire, ("forme_juridique", "SARL")
            )
        )
        session_sql.flush()

        assert session_sql.execute(
            text("SELECT count(*) FROM qualification")
        ).scalar_one() == 1

    def test_deux_cabinets_portent_la_meme_reference_de_dossier(
        self, session_sql, questionnaire
    ):
        """⚠️ Quatrième application de la règle du pas 17 : une référence de dossier
        est propre au cabinet qui l'a émise."""
        DepotQualificationsSql(session_sql, "CGA-BRCG").enregistrer(
            ouvrir_une_qualification("dos-1", questionnaire)
        )
        DepotQualificationsSql(session_sql, "CGA-AUTRE").enregistrer(
            ouvrir_une_qualification("dos-1", questionnaire)
        )
        session_sql.flush()

        assert DepotQualificationsSql(session_sql, "CGA-BRCG").trouver("dos-1")
        assert DepotQualificationsSql(session_sql, "CGA-AUTRE").trouver("dos-1")

    def test_le_meme_cabinet_ne_porte_pas_deux_fois_le_meme_dossier(self, session_sql):
        """La contre-épreuve : sans elle, le cas précédent passerait sur une table
        sans contrainte du tout."""
        insertion = text(
            "INSERT INTO qualification (locataire, dossier, service, "
            "version_questionnaire, donnees) VALUES ('CGA-BRCG', 'dos-1', 's', "
            "'1', CAST('{}' AS json))"
        )
        session_sql.execute(insertion)
        session_sql.flush()
        with pytest.raises(IntegrityError):
            session_sql.execute(insertion)
