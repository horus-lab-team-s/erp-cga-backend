"""L'évaluateur de prédicats. Aucun `eval`, aucune clé inconnue tolérée."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.contexts.conformite.jsonlogic import ErreurPredicat, evaluer


class TestAccesAuxDonnees:
    def test_chemin_pointe(self):
        donnees = {"emetteur": {"niu": "M053311224455R"}}
        assert evaluer({"var": "emetteur.niu"}, donnees) == "M053311224455R"

    def test_champ_absent_rend_none_sans_lever(self):
        assert evaluer({"var": "emetteur.niu"}, {}) is None

    def test_valeur_par_defaut(self):
        assert evaluer({"var": ["contexte.doublons", 0]}, {}) == 0

    def test_var_vide_rend_la_racine(self):
        assert evaluer({"var": ""}, {"a": 1}) == {"a": 1}


class TestComparaisons:
    def test_comparaison_decimale(self):
        donnees = {"ttc": Decimal("2350000")}
        assert evaluer({">=": [{"var": "ttc"}, Decimal("500000")]}, donnees) is True

    def test_comparaison_contre_donnee_absente_est_fausse(self):
        # Une facture peut légitimement manquer d'un champ : c'est ce que la règle teste.
        # Le prédicat doit rendre faux, jamais lever.
        assert evaluer({">=": [{"var": "absent"}, 100]}, {}) is False

    def test_egalite_souple_entre_types_numeriques(self):
        assert evaluer({"==": [Decimal("450000"), 450000]}, {}) is True

    def test_booleen_nest_pas_un_nombre(self):
        # Confondre True et 1 dans un contrôle fiscal produit des faux positifs.
        assert evaluer({"==": [True, 1]}, {}) is False
        assert evaluer({">=": [True, 1]}, {}) is False


class TestLogique:
    def test_and_court_circuite(self):
        assert evaluer({"and": [False, {"var": "jamais.evalue"}]}, {}) is False

    def test_or(self):
        assert evaluer({"or": [False, True]}, {}) is True

    def test_negation_sur_chaine_vide(self):
        assert evaluer({"!!": [{"var": "niu"}]}, {"niu": ""}) is False
        assert evaluer({"!!": [{"var": "niu"}]}, {"niu": "P019876543210K"}) is True


class TestIteration:
    LIGNES = {"lignes": [{"designation": "Ciment"}, {"designation": "Travaux divers"}]}

    def test_some_avec_portee_sur_l_element(self):
        predicat = {"some": [{"var": "lignes"}, {"==": [{"var": "designation"}, "Ciment"]}]}
        assert evaluer(predicat, self.LIGNES) is True

    def test_none_sur_collection_vide(self):
        assert evaluer({"none": [{"var": "lignes"}, True]}, {"lignes": []}) is True

    def test_all_sur_collection_vide_est_faux(self):
        # Sémantique JSONLogic, contraire à celle de Python.
        assert evaluer({"all": [{"var": "lignes"}, True]}, {"lignes": []}) is False


class TestRegex:
    MOTIF_NIU = r"^[A-Z][0-9]{12}[A-Z]$"

    def test_niu_valide(self):
        assert evaluer({"regex": [{"var": "niu"}, self.MOTIF_NIU]}, {"niu": "M081234567890P"})

    def test_niu_invalide(self):
        assert not evaluer({"regex": [{"var": "niu"}, self.MOTIF_NIU]}, {"niu": "12345"})

    def test_regex_sur_valeur_absente_est_fausse(self):
        assert evaluer({"regex": [{"var": "niu"}, self.MOTIF_NIU]}, {}) is False

    def test_drapeau_insensible_a_la_casse(self):
        assert evaluer({"regex": ["TRAVAUX DIVERS", r"(?i)^travaux divers$"]}, {}) is True


class TestSurete:
    def test_operateur_inconnu_est_refuse(self):
        with pytest.raises(ErreurPredicat, match="non autorisé"):
            evaluer({"exec": ["rm -rf /"]}, {})

    def test_parametre_non_resolu_est_refuse(self):
        # Un paramètre doit être substitué AVANT évaluation. S'il arrive ici, la
        # résolution a échoué : mieux vaut lever que comparer contre un dictionnaire.
        with pytest.raises(ErreurPredicat, match="non résolu"):
            evaluer({">=": [1, {"param": "SEUIL_ESPECES_DEDUCTIBILITE_TVA"}]}, {})

    def test_noeud_a_plusieurs_operateurs_est_refuse(self):
        with pytest.raises(ErreurPredicat, match="exactement un opérateur"):
            evaluer({"==": [1, 1], "!=": [1, 2]}, {})

    def test_regex_invalide_est_signalee(self):
        with pytest.raises(ErreurPredicat, match="expression régulière invalide"):
            evaluer({"regex": ["abc", "([a-z"]}, {})
