"""La qualification : ce qu'on apprend du client, en données typées.

L'étape la plus importante du parcours, et celle qu'on sous-estime. Les tests
portent sur ce qui fait la différence entre un dossier qu'un humain doit relire et
un dossier qu'un moteur peut évaluer :

* **le typage à la saisie**, où le responsable a encore le client au téléphone ;
* **le refus de ce qui est hors schéma**, qui est le piège nommé par le document ;
* **la note libre qui n'entre dans aucun calcul**, garantie que le chiffrage est
  explicable ;
* **le pont vers le moteur**, qui doit tenir sans que rien ne lui soit ajouté.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    QuestionnaireIntrouvable,
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.domaine.qualification import (
    Qualification,
    Question,
    Questionnaire,
    ReponseInvalide,
    SourceReponse,
    ouvrir_une_qualification,
)
from app.infrastructure.config import RACINE_DEPOT
from app.moteur.faits import ErreurSchema, TypeFait

T0 = datetime(2026, 9, 9, 10, 0)
QUALIFICATION = RACINE_DEPOT / "Docs" / "referentiel" / "qualification"


def _q(code: str, type_: TypeFait, **surcharges) -> Question:
    defauts = {"code": code, "libelle": f"Question {code}", "type": type_}
    return Question(**{**defauts, **surcharges})


def _questionnaire(*questions: Question, version: str = "1") -> Questionnaire:
    return Questionnaire(service="essai", version=version, questions=questions)


def _vide(questionnaire: Questionnaire) -> Qualification:
    return ouvrir_une_qualification("dos-1", questionnaire)


# ── Le typage ─────────────────────────────────────────────────────────────────


class TestTypage:
    @pytest.mark.parametrize(
        ("type_", "brut", "attendu"),
        [
            (TypeFait.ENTIER, "12", 12),
            (TypeFait.ENTIER, 12, 12),
            (TypeFait.ENTIER, " 12 ", 12),
            (TypeFait.DECIMAL, "1 000 000", Decimal("1000000")),
            (TypeFait.DECIMAL, 2500, Decimal("2500")),
            (TypeFait.TEXTE, "  commerce  ", "commerce"),
            (TypeFait.DATE, "2026-10-01", date(2026, 10, 1)),
            (TypeFait.DATE, date(2026, 10, 1), date(2026, 10, 1)),
            (TypeFait.LISTE, "statuts, niu", ("statuts", "niu")),
            (TypeFait.LISTE, ["statuts", "niu"], ("statuts", "niu")),
        ],
    )
    def test_la_valeur_est_convertie_au_type_de_la_question(
        self, type_, brut, attendu
    ):
        questionnaire = _questionnaire(_q("champ", type_))
        remplie = _vide(questionnaire).repondre(questionnaire, "champ", brut, T0)
        assert remplie.reponse("champ").valeur == attendu

    @pytest.mark.parametrize("oui", ["oui", "OUI", "vrai", "1", "true", True])
    def test_les_formes_du_oui(self, oui):
        questionnaire = _questionnaire(_q("champ", TypeFait.BOOLEEN))
        remplie = _vide(questionnaire).repondre(questionnaire, "champ", oui, T0)
        assert remplie.reponse("champ").valeur is True

    @pytest.mark.parametrize("non", ["non", "NON", "faux", "0", "false", False])
    def test_les_formes_du_non(self, non):
        questionnaire = _questionnaire(_q("champ", TypeFait.BOOLEEN))
        remplie = _vide(questionnaire).repondre(questionnaire, "champ", non, T0)
        assert remplie.reponse("champ").valeur is False

    def test_un_texte_qui_n_est_ni_oui_ni_non_est_refuse(self):
        """La vérité de Python ferait de la chaîne « non » un vrai, ce qui est
        exactement l'inverse de la réponse donnée."""
        questionnaire = _questionnaire(_q("champ", TypeFait.BOOLEEN))
        with pytest.raises(ReponseInvalide, match="ni un oui ni un non"):
            _vide(questionnaire).repondre(questionnaire, "champ", "peut-être", T0)

    def test_une_case_cochee_n_est_pas_un_entier(self):
        """⚠️ Le piège du langage : `isinstance(True, int)` rend vrai. Sans ce
        refus, cocher une case sur une question qui attend un nombre d'associés
        enregistrerait « 1 associé », et rien ne le signalerait jamais."""
        questionnaire = _questionnaire(_q("associes", TypeFait.ENTIER))
        with pytest.raises(ReponseInvalide, match="case cochée"):
            _vide(questionnaire).repondre(questionnaire, "associes", True, T0)

    def test_une_case_cochee_n_est_pas_un_montant(self):
        questionnaire = _questionnaire(_q("capital", TypeFait.DECIMAL))
        with pytest.raises(ReponseInvalide, match="case cochée"):
            _vide(questionnaire).repondre(questionnaire, "capital", True, T0)

    @pytest.mark.parametrize(
        ("type_", "brut"),
        [
            (TypeFait.ENTIER, "beaucoup"),
            (TypeFait.ENTIER, "12,5"),
            (TypeFait.DECIMAL, "cher"),
            (TypeFait.DATE, "le mois prochain"),
        ],
    )
    def test_une_valeur_du_mauvais_type_est_refusee_a_la_saisie(self, type_, brut):
        """À la saisie, où le responsable a le client au téléphone et peut
        demander. La laisser passer la ferait découvrir au chiffrage, quand il a
        raccroché."""
        questionnaire = _questionnaire(_q("champ", type_))
        with pytest.raises(ReponseInvalide):
            _vide(questionnaire).repondre(questionnaire, "champ", brut, T0)


class TestEnum:
    def test_une_valeur_de_la_liste_passe(self):
        questionnaire = _questionnaire(
            _q("forme", TypeFait.ENUM, valeurs=("SARL", "SA"))
        )
        remplie = _vide(questionnaire).repondre(questionnaire, "forme", "SARL", T0)
        assert remplie.reponse("forme").valeur == "SARL"

    def test_une_valeur_hors_liste_est_refusee(self):
        """Elle ferait échouer chaque règle qui compare à cette liste, sans
        qu'aucune ne le dise."""
        questionnaire = _questionnaire(
            _q("forme", TypeFait.ENUM, valeurs=("SARL", "SA"))
        )
        with pytest.raises(ReponseInvalide, match="n'admet que"):
            _vide(questionnaire).repondre(questionnaire, "forme", "SARLU", T0)

    def test_un_enum_sans_valeurs_est_refuse_a_la_construction(self):
        """La règle est celle du `Fait` du moteur, et c'est lui qui la tient :
        deux endroits où la dire dériveraient.

        ⚠️ Refusé **à la construction du questionnaire**, pas au premier appel de
        `schema()`. C'était l'inverse au premier jet, et un questionnaire
        invalide pouvait exister en mémoire jusqu'au moment de chiffrer.

        L'exception attendue est `ValidationError` et non `ErreurSchema` :
        Pydantic enveloppe ce que lève un validateur de modèle. Le message reste
        lisible, et c'est ce qui compte pour celui qui débogue.
        """
        with pytest.raises(ValidationError, match="ENUM sans valeurs"):
            _questionnaire(_q("forme", TypeFait.ENUM))


# ── Le hors-schéma ────────────────────────────────────────────────────────────


class TestHorsSchema:
    def test_une_question_inconnue_est_refusee(self):
        """Le piège nommé par le document : un objet qui accepte tout est un
        objet sans schéma déguisé, et le jour où le moteur en a besoin il faut
        tout ressaisir."""
        questionnaire = _questionnaire(_q("connue", TypeFait.TEXTE))
        with pytest.raises(ReponseInvalide) as echec:
            _vide(questionnaire).repondre(questionnaire, "inventee", "x", T0)
        assert "Questions connues : connue" in str(echec.value)

    def test_deux_questions_du_meme_code_sont_refusees(self):
        """La seconde écraserait la première, et laquelle dépendrait de l'ordre
        du fichier.

        ⚠️ L'assertion porte sur le message **du questionnaire**, pas seulement
        sur le fait qu'une erreur survienne. Le schéma du moteur refuse déjà les
        doublons, avec son propre message : un test qui se contentait de
        « deux fois » passait aussi bien avec le contrôle du questionnaire
        que sans lui, et ne disait donc rien de son existence.

        Les deux contrôles coexistent parce qu'ils ne s'adressent pas aux mêmes
        gens. Celui du moteur parle de faits à un développeur ; celui-ci nomme le
        service et le fichier à un responsable de pôle qui édite du YAML.
        """
        with pytest.raises(ValueError, match="questionnaire essai"):
            _questionnaire(
                _q("champ", TypeFait.TEXTE), _q("champ", TypeFait.ENTIER)
            )


# ── L'obligatoire et le vide ──────────────────────────────────────────────────


class TestObligatoire:
    def test_une_valeur_vide_sur_une_obligatoire_est_refusee(self):
        """Enregistrer un vide ferait sortir la question de la liste des
        manquantes tout en ne répondant à rien."""
        questionnaire = _questionnaire(
            _q("champ", TypeFait.TEXTE, obligatoire=True)
        )
        for vide in (None, "", "   "):
            with pytest.raises(ReponseInvalide, match="obligatoire"):
                _vide(questionnaire).repondre(questionnaire, "champ", vide, T0)

    def test_un_vide_sur_une_facultative_efface_la_reponse(self):
        """Le client revient sur ce qu'il avait dit sans rien mettre à la
        place."""
        questionnaire = _questionnaire(_q("champ", TypeFait.TEXTE))
        remplie = _vide(questionnaire).repondre(questionnaire, "champ", "x", T0)
        effacee = remplie.repondre(questionnaire, "champ", "", T0)
        assert effacee.reponse("champ") is None

    def test_les_manquantes_sortent_dans_l_ordre_de_saisie(self):
        """C'est une liste que le responsable parcourt à l'écran, pas un
        ensemble."""
        questionnaire = _questionnaire(
            _q("zebre", TypeFait.TEXTE, rang=0, obligatoire=True),
            _q("alpha", TypeFait.TEXTE, rang=1, obligatoire=True),
            _q("facultative", TypeFait.TEXTE, rang=2),
        )
        assert _vide(questionnaire).manquantes(questionnaire) == ("zebre", "alpha")

    def test_la_qualification_est_complete_quand_les_obligatoires_le_sont(self):
        questionnaire = _questionnaire(
            _q("obligatoire", TypeFait.TEXTE, obligatoire=True),
            _q("facultative", TypeFait.TEXTE),
        )
        vide = _vide(questionnaire)
        assert vide.complete(questionnaire) is False

        remplie = vide.repondre(questionnaire, "obligatoire", "x", T0)
        assert remplie.complete(questionnaire) is True

    def test_l_avancement_compte_les_questions_du_questionnaire(self):
        questionnaire = _questionnaire(
            _q("a", TypeFait.TEXTE), _q("b", TypeFait.TEXTE), _q("c", TypeFait.TEXTE)
        )
        remplie = _vide(questionnaire).repondre(questionnaire, "a", "x", T0)
        assert remplie.avancement(questionnaire) == (1, 3)


# ── La saisie ─────────────────────────────────────────────────────────────────


class TestSaisie:
    def test_repondre_deux_fois_remplace(self):
        """Le client se corrige en cours d'entretien, c'est normal. La dernière
        réponse fait foi, avec son horodatage."""
        questionnaire = _questionnaire(_q("ca", TypeFait.DECIMAL))
        remplie = _vide(questionnaire).repondre(questionnaire, "ca", "30000000", T0)
        corrigee = remplie.repondre(
            questionnaire, "ca", "8000000", datetime(2026, 9, 9, 10, 12)
        )
        assert len([r for r in corrigee.reponses if r.code == "ca"]) == 1
        assert corrigee.reponse("ca").valeur == Decimal("8000000")
        assert corrigee.reponse("ca").saisie_le == datetime(2026, 9, 9, 10, 12)

    def test_chaque_reponse_porte_sa_source(self):
        """Une valeur déclarée au téléphone et une valeur lue sur un document
        n'ont pas le même poids. Le régime réel se constate sur pièces."""
        questionnaire = _questionnaire(_q("regime", TypeFait.TEXTE))
        declaree = _vide(questionnaire).repondre(questionnaire, "regime", "REEL", T0)
        assert declaree.reponse("regime").source is SourceReponse.DECLAREE

        constatee = declaree.repondre(
            questionnaire, "regime", "REEL", T0, source=SourceReponse.CONSTATEE
        )
        assert constatee.reponse("regime").source is SourceReponse.CONSTATEE

    def test_chaque_reponse_porte_son_horodatage(self):
        questionnaire = _questionnaire(_q("champ", TypeFait.TEXTE))
        remplie = _vide(questionnaire).repondre(questionnaire, "champ", "x", T0)
        assert remplie.reponse("champ").saisie_le == T0

    def test_la_qualification_est_figee(self):
        questionnaire = _questionnaire(_q("champ", TypeFait.TEXTE))
        with pytest.raises(ValueError):
            _vide(questionnaire).note = "autre"


# ── La note libre ─────────────────────────────────────────────────────────────


class TestNoteLibre:
    def test_elle_n_entre_dans_aucun_calcul(self):
        """⚠️ **La garantie que le chiffrage est explicable.** Un montant qui
        dépendrait d'une phrase écrite à la volée ne se rejouerait pas et ne se
        défendrait pas devant un client."""
        questionnaire = _questionnaire(_q("champ", TypeFait.TEXTE))
        qualification = (
            _vide(questionnaire)
            .repondre(questionnaire, "champ", "commerce", T0)
            .avec_note("le client hésite encore sur le nombre d'associés")
        )
        assert qualification.note
        assert "note" not in qualification.faits()
        assert set(qualification.faits()) == {"champ"}

    def test_elle_est_elaguee_et_bornee(self):
        """⚠️ Ce test a trouvé un défaut réel, et qui dépasse ce module.

        `model_copy` **ne revalide pas**. La borne `max_length` du champ
        protégeait le constructeur et laissait passer la copie, alors que tout le
        domaine repose sur `model_copy` pour ses transitions. La note venant d'un
        formulaire, la borne existe justement pour elle.

        `avec_note` vérifie donc lui-même. Voir sa docstring pour la règle
        générale.
        """
        questionnaire = _questionnaire()
        assert _vide(questionnaire).avec_note("  x  ").note == "x"
        with pytest.raises(ReponseInvalide, match="dépasse"):
            _vide(questionnaire).avec_note("a" * 4_001)


# ── Le pont vers le moteur ────────────────────────────────────────────────────


class TestPontVersLeMoteur:
    def test_le_questionnaire_produit_un_schema_de_faits(self):
        """Le même objet sert à guider une saisie humaine et à valider un
        prédicat de tarification. Deux déclarations séparées dériveraient, et la
        dérive ne se verrait qu'au premier chiffrage faux."""
        questionnaire = _questionnaire(
            _q("associes", TypeFait.ENTIER),
            _q("forme", TypeFait.ENUM, valeurs=("SARL", "SA")),
        )
        schema = questionnaire.schema()
        assert schema.codes == {"associes", "forme"}
        assert schema.fait("forme").valeurs == ("SARL", "SA")

    def test_le_schema_suit_l_ordre_des_questions(self):
        questionnaire = _questionnaire(
            _q("second", TypeFait.TEXTE, rang=1),
            _q("premier", TypeFait.TEXTE, rang=0),
        )
        assert [f.code for f in questionnaire.schema().faits] == ["premier", "second"]

    def test_les_faits_ne_portent_que_ce_qui_a_ete_repondu(self):
        """⚠️ Les questions non répondues sont **absentes** plutôt que nulles.
        Les poser à `None` ferait qu'une comparaison numérique les traiterait
        comme zéro, et un chiffre d'affaires inconnu vaudrait zéro franc."""
        questionnaire = _questionnaire(
            _q("repondue", TypeFait.ENTIER), _q("muette", TypeFait.ENTIER)
        )
        remplie = _vide(questionnaire).repondre(questionnaire, "repondue", 3, T0)
        assert remplie.faits() == {"repondue": 3}
        assert "muette" not in remplie.faits()

    def test_les_faits_satisfont_le_schema(self):
        """Ce que le test d'un domaine de code vérifie par une assertion, celui-ci
        l'obtient par construction : la même configuration produit les deux."""
        questionnaire = _questionnaire(
            _q("a", TypeFait.TEXTE), _q("b", TypeFait.ENTIER)
        )
        remplie = (
            _vide(questionnaire)
            .repondre(questionnaire, "a", "x", T0)
            .repondre(questionnaire, "b", 2, T0)
        )
        assert set(remplie.faits()) <= questionnaire.schema().codes

    def test_un_predicat_qui_cite_un_fait_inconnu_est_refuse(self):
        """Le même contrôle que pour les autres domaines, à ceci près que le
        schéma vient du référentiel."""
        from app.moteur.chemins import chemins_cites

        questionnaire = _questionnaire(_q("associes", TypeFait.ENTIER))
        with pytest.raises(ErreurSchema, match="associe"):
            questionnaire.schema().valider_predicat(
                chemins_cites({">": [{"var": "associe"}, 1]}), origine="essai"
            )


# ── La version ────────────────────────────────────────────────────────────────


class TestVersion:
    def test_la_qualification_conserve_la_version_du_questionnaire(self):
        """Pour la même raison que la version du barème : six mois plus tard,
        personne ne doit se demander sur quelles questions un dossier a été
        qualifié."""
        questionnaire = _questionnaire(_q("champ", TypeFait.TEXTE), version="3")
        assert _vide(questionnaire).version_questionnaire == "3"

    def test_completude_se_juge_contre_le_questionnaire_qu_on_lui_donne(self):
        """Une qualification se relit longtemps après, et le questionnaire aura
        changé. C'est l'appelant qui décide lequel il confronte."""
        ancien = _questionnaire(_q("a", TypeFait.TEXTE, obligatoire=True))
        nouveau = _questionnaire(
            _q("a", TypeFait.TEXTE, obligatoire=True),
            _q("b", TypeFait.TEXTE, obligatoire=True),
            version="2",
        )
        remplie = _vide(ancien).repondre(ancien, "a", "x", T0)
        assert remplie.complete(ancien) is True
        assert remplie.complete(nouveau) is False
        assert remplie.manquantes(nouveau) == ("b",)


# ── Les questionnaires réels ──────────────────────────────────────────────────


class TestQuestionnairesDuReferentiel:
    @pytest.fixture(scope="class")
    def repertoire(self):
        return RepertoireDeQuestionnaires.depuis(QUALIFICATION)

    def test_ils_se_chargent(self, repertoire):
        assert repertoire.services() == ["creation-sarl", "tenue-comptable"]

    def test_chacun_produit_un_schema_valide(self, repertoire):
        """Éprouvé au chargement, répété ici : un ENUM sans valeurs ne doit pas
        se découvrir quand un responsable a le client au téléphone."""
        for service in repertoire.services():
            schema = repertoire.prendre(service).schema()
            assert len(schema.faits) == len(schema.codes)

    def test_chaque_question_obligatoire_est_typee_autrement_qu_en_texte_libre(
        self, repertoire
    ):
        """Une obligatoire en TEXTE reste possible — l'activité en est une — mais
        les grandeurs qui entrent dans un calcul doivent être des nombres ou des
        listes fermées. Ce test le vérifie pour celles qui portent une unité."""
        for service in repertoire.services():
            for question in repertoire.prendre(service).ordonnees:
                if question.unite:
                    assert question.type in (
                        TypeFait.ENTIER,
                        TypeFait.DECIMAL,
                    ), f"{service}/{question.code}"

    def test_le_chiffre_d_affaires_est_une_tranche_et_non_un_montant(
        self, repertoire
    ):
        """En tranche et non en montant : un prévisionnel au franc près est une
        fausse précision, et le régime fiscal se décide par seuils."""
        question = repertoire.prendre("creation-sarl").question(
            "chiffre_affaires_prevu"
        )
        assert question.type is TypeFait.ENUM
        assert question.valeurs

    def test_la_creation_demande_la_forme_juridique_en_premier(self, repertoire):
        """L'ordre compte : demander le capital social avant la forme juridique
        fait interrompre le responsable par le client."""
        ordonnees = repertoire.prendre("creation-sarl").ordonnees
        assert ordonnees[0].code == "forme_juridique"
        codes = [q.code for q in ordonnees]
        assert codes.index("associes") < codes.index("capital_social")

    def test_les_faits_de_la_grille_de_charge_se_retrouvent_dans_la_tenue(
        self, repertoire
    ):
        """La qualification d'une tenue comptable doit produire ce que la grille
        de charge évalue. Sinon le chiffrage tourne sur des faits absents, et
        chaque critère échoue en silence."""
        from app.contextes.portefeuille.domaine.charge import SCHEMA_CHARGE

        pose = {q.code for q in repertoire.prendre("tenue-comptable").questions}
        manquants = SCHEMA_CHARGE.codes - pose
        assert not manquants, (
            f"la grille de charge évalue {sorted(manquants)}, que le "
            "questionnaire de tenue comptable ne demande pas"
        )

    def test_chaque_question_a_un_libelle_lisible(self, repertoire):
        for service in repertoire.services():
            for question in repertoire.prendre(service).questions:
                assert len(question.libelle) > 5, f"{service}/{question.code}"

    def test_un_service_sans_questionnaire_leve(self, repertoire):
        """Aucun repli sur un questionnaire générique : il poserait les mauvaises
        questions, et le chiffrage porterait sur des faits hors sujet."""
        with pytest.raises(QuestionnaireIntrouvable, match="Services qualifiables"):
            repertoire.prendre("formation-fiscale")

    def test_une_qualification_reelle_se_remplit_de_bout_en_bout(self, repertoire):
        questionnaire = repertoire.prendre("creation-sarl")
        qualification = ouvrir_une_qualification("dos-1", questionnaire)
        reponses = {
            "forme_juridique": "SARL",
            "associes": 3,
            "capital_social": "1000000",
            "apports_en_nature": "non",
            "chiffre_affaires_prevu": "DE_10M_A_50M",
            "salaries_prevus": 2,
            "region_siege": "LITTORAL",
            "activite": "Commerce général",
        }
        for code, valeur in reponses.items():
            qualification = qualification.repondre(questionnaire, code, valeur, T0)

        assert qualification.complete(questionnaire) is True
        assert qualification.manquantes(questionnaire) == ()
        assert qualification.faits()["associes"] == 3
        assert qualification.faits()["capital_social"] == Decimal("1000000")
        assert qualification.faits()["apports_en_nature"] is False
