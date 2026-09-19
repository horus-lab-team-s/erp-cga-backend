"""Le noyau d'évaluation : extraction des faits cités et schéma qui les déclare.

Ces tests portent sur `app/moteur/`, qui ne connaît aucun métier. Ils s'écrivent donc
sans facture, sans référentiel et sans base — c'est précisément la propriété qu'on
cherche à préserver.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.moteur.chemins import chemins_cites
from app.moteur.faits import ErreurSchema, Fait, SchemaDeFaits, TypeFait


class TestChemminsCites:
    def test_un_var_simple(self):
        assert chemins_cites({"var": "montant"}) == {"montant"}

    def test_un_chemin_pointe(self):
        assert chemins_cites({"var": "montants.total_ht"}) == {"montants.total_ht"}

    def test_les_deux_membres_d_une_comparaison(self):
        predicat = {"==": [{"var": "montants.somme_lignes_ht"}, {"var": "montants.total_ht"}]}
        assert chemins_cites(predicat) == {"montants.somme_lignes_ht", "montants.total_ht"}

    def test_un_var_avec_valeur_par_defaut(self):
        """`{"var": ["chemin", 0]}` cite bien le chemin : la valeur par défaut n'en est pas un."""
        assert chemins_cites({"var": ["contexte.doublons_potentiels", 0]}) == {
            "contexte.doublons_potentiels"
        }

    def test_missing_cite_ses_arguments(self):
        assert chemins_cites({"missing": ["emetteur.niu", "emetteur.rccm"]}) == {
            "emetteur.niu",
            "emetteur.rccm",
        }

    def test_imbrication_profonde(self):
        predicat = {
            "and": [
                {"!=": [{"var": "reglement.mode"}, "ESPECES"]},
                {"if": [{"var": "emetteur.etranger"}, {"var": "emetteur.niu"}, True]},
            ]
        }
        assert chemins_cites(predicat) == {
            "reglement.mode",
            "emetteur.etranger",
            "emetteur.niu",
        }

    def test_un_var_vide_ne_cite_rien(self):
        """`{"var": ""}` désigne le sujet courant, pas un fait nommé."""
        assert chemins_cites({"var": ""}) == set()

    def test_un_var_calcule_ne_cite_rien(self):
        """Un chemin construit à l'évaluation n'est pas connaissable au chargement.

        On rend l'ensemble vide plutôt qu'un chemin deviné : le garde-fou préfère
        laisser passer un cas qu'il ne comprend pas à refuser une règle valable.
        """
        assert chemins_cites({"var": {"cat": ["montants.", "total_ht"]}}) == set()


class TestPorteeDesIteratifs:
    """La portée change dans `some`, `none`, `all`, `map` et `filter`.

    C'est le piège de l'extraction : un `var` situé dans le corps d'une itération
    désigne un champ de l'élément, pas un fait racine. Un extracteur naïf refuserait
    des règles correctes, et le réflexe serait de désactiver le garde-fou.
    """

    def test_le_corps_est_prefixe_par_la_collection(self):
        predicat = {
            "some": [{"var": "lignes"}, {"==": [{"var": "designation"}, ""]}],
        }
        assert chemins_cites(predicat) == {"lignes", "lignes[].designation"}

    @pytest.mark.parametrize("operateur", ["some", "none", "all", "map", "filter"])
    def test_tous_les_iteratifs_changent_la_portee(self, operateur: str):
        predicat = {operateur: [{"var": "lignes"}, {"var": "montant_ht"}]}
        assert chemins_cites(predicat) == {"lignes", "lignes[].montant_ht"}

    def test_deux_niveaux_d_imbrication(self):
        predicat = {
            "some": [
                {"var": "lignes"},
                {"some": [{"var": "taxes"}, {">": [{"var": "taux"}, 0]}]},
            ]
        }
        assert chemins_cites(predicat) == {
            "lignes",
            "lignes[].taxes",
            "lignes[].taxes[].taux",
        }

    def test_une_collection_non_nommee_n_extrait_pas_son_corps(self):
        """Sans nom de collection, préfixer serait inventer un chemin faux."""
        predicat = {"some": [[1, 2, 3], {">": [{"var": "valeur"}, 0]}]}
        assert chemins_cites(predicat) == set()


class TestSchemaDeFaits:
    @staticmethod
    def _schema() -> SchemaDeFaits:
        return SchemaDeFaits(
            domaine="ESSAI",
            faits=(
                Fait(code="montants.total_ht", type=TypeFait.DECIMAL, libelle="Total", unite="F"),
                Fait(code="montants.total_tva", type=TypeFait.DECIMAL, libelle="Taxe", unite="F"),
                Fait(
                    code="reglement.mode",
                    type=TypeFait.ENUM,
                    libelle="Mode",
                    valeurs=("ESPECES", "VIREMENT"),
                ),
            ),
        )

    def test_un_fait_declare_est_connu(self):
        assert self._schema().inconnus(["montants.total_ht"]) == ()

    def test_un_fait_non_declare_ressort(self):
        assert self._schema().inconnus(["montants.total_htt", "montants.total_ht"]) == (
            "montants.total_htt",
        )

    def test_un_predicat_correct_passe(self):
        self._schema().valider_predicat(["montants.total_ht"], origine="la règle X")

    def test_un_predicat_fautif_est_refuse(self):
        with pytest.raises(ErreurSchema, match="la règle X"):
            self._schema().valider_predicat(["montants.inexistant"], origine="la règle X")

    def test_le_message_suggere_le_fait_voisin(self):
        """Une faute de frappe sur un chemin est l'erreur la plus fréquente. Suggérer
        coûte trois lignes et fait gagner un quart d'heure à chaque occurrence."""
        with pytest.raises(ErreurSchema) as echec:
            self._schema().valider_predicat(["montants.total_htt"], origine="la règle X")
        assert "montants.total_ht" in str(echec.value)

    def test_un_chemin_sans_voisin_ne_suggere_rien(self):
        with pytest.raises(ErreurSchema) as echec:
            self._schema().valider_predicat(["absolument.autre.chose"], origine="la règle X")
        assert "voulez-vous dire" not in str(echec.value)

    def test_un_fait_declare_deux_fois_est_refuse(self):
        # Levée dans un validateur, l'erreur est enveloppée par pydantic : c'est le
        # message qui porte l'information, pas le type. Voir ErreurSchema.
        with pytest.raises(ValidationError, match="deux fois"):
            SchemaDeFaits(
                domaine="ESSAI",
                faits=(
                    Fait(code="a", type=TypeFait.TEXTE, libelle="A"),
                    Fait(code="a", type=TypeFait.ENTIER, libelle="A bis"),
                ),
            )

    def test_un_enum_sans_valeurs_est_refuse(self):
        with pytest.raises(ValidationError, match="sans valeurs"):
            Fait(code="mode", type=TypeFait.ENUM, libelle="Mode")

    def test_des_valeurs_sur_un_type_non_enum_sont_refusees(self):
        with pytest.raises(ValidationError, match="sans être un ENUM"):
            Fait(code="total", type=TypeFait.DECIMAL, libelle="Total", valeurs=("A",))
