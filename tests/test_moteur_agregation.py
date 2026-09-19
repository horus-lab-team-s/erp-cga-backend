"""Les quatre façons de conclure d'une liste de constats.

Le pas 5 de la généralisation. Ces tests s'écrivent avec des constats factices : c'est
la démonstration que l'agrégation ne connaît aucun domaine, puisqu'aucun n'est importé
pour la tester.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.moteur.agregation import (
    ConstatEvalue,
    denombrer,
    enjeu_maximal,
    intervalle_autour,
    niveau_le_plus_eleve,
    somme_des_enjeux,
)
from app.moteur.consequence import Consequence, TypeConsequence

D = Decimal


@dataclass(frozen=True)
class _Constat:
    """Le strict minimum qu'un agrégateur exige. Aucun domaine n'est nécessaire."""

    severite: str
    enjeu: Decimal | None
    consequence: Consequence


def _montant(severite: str, enjeu: Decimal | None) -> _Constat:
    return _Constat(severite, enjeu, Consequence(type=TypeConsequence.MONTANT))


def _ajustement(valeur: Decimal) -> _Constat:
    return _Constat(
        "INFORMATION",
        valeur,
        Consequence(type=TypeConsequence.AJUSTEMENT, valeur_numerique=valeur),
    )


class TestContratDuConstat:
    def test_un_objet_minimal_satisfait_le_protocole(self):
        assert isinstance(_montant("MAJEUR", D(10)), ConstatEvalue)


class TestDenombrement:
    def test_compte_par_severite(self):
        constats = [
            _montant("MAJEUR", None),
            _montant("MAJEUR", None),
            _montant("INFORMATION", None),
        ]
        assert denombrer(constats) == {"MAJEUR": 2, "INFORMATION": 1}

    def test_le_plus_frequent_vient_en_tete(self):
        constats = [_montant("A", None), _montant("B", None), _montant("B", None)]
        assert list(denombrer(constats)) == ["B", "A"]

    def test_sans_constat_le_denombrement_est_vide(self):
        assert denombrer([]) == {}


class TestEnjeuMaximal:
    """L'agrégateur de la conformité documentaire."""

    def test_il_prend_le_plus_grand_et_n_additionne_pas(self):
        """Le cas qui a motivé toute cette séparation.

        Deux règles qui rejettent la même taxe ne la rejettent pas deux fois. Additionner
        produirait un chiffre supérieur au préjudice réel, et c'est celui qu'un adhérent
        conteste en premier, à raison.
        """
        constats = [_montant("MAJEUR", D(19_250)), _montant("BLOQUANT", D(19_250))]
        assert enjeu_maximal()(constats).valeur == D(19_250)

    def test_les_constats_sans_enjeu_ne_comptent_pas(self):
        constats = [_montant("MAJEUR", None), _montant("MAJEUR", D(500))]
        assert enjeu_maximal()(constats).valeur == D(500)

    def test_sans_constat_l_enjeu_vaut_zero(self):
        assert enjeu_maximal()([]).valeur == D(0)

    def test_il_transporte_l_unite_sans_la_convertir(self):
        assert enjeu_maximal(unite="FCFA")([]).unite == "FCFA"


class TestSommeDesEnjeux:
    """L'agrégateur de l'évaluation de charge."""

    def test_il_additionne(self):
        constats = [_montant("INFORMATION", D(18)), _montant("INFORMATION", D(10))]
        assert somme_des_enjeux(unite="points")(constats).valeur == D(28)

    def test_sans_constat_la_somme_vaut_zero(self):
        assert somme_des_enjeux()([]).valeur == D(0)

    def test_les_deux_agregateurs_different_sur_les_memes_constats(self):
        """La preuve que la séparation était nécessaire.

        Aucune règle générale ne dit s'il faut sommer ou prendre le maximum : seul le
        domaine sait. Les mêmes constats donnent donc deux conclusions justes.
        """
        constats = [_montant("MAJEUR", D(10)), _montant("MAJEUR", D(10))]
        assert enjeu_maximal()(constats).valeur == D(10)
        assert somme_des_enjeux()(constats).valeur == D(20)


class TestNiveauLePlusEleve:
    """L'agrégateur du contrôle interne, et de la sévérité d'un rapport."""

    ORDRE = ["FAIBLE", "MODERE", "ELEVE", "CRITIQUE"]

    def test_il_retient_le_plus_haut(self):
        constats = [_montant("FAIBLE", None), _montant("ELEVE", None), _montant("MODERE", None)]
        assert niveau_le_plus_eleve(self.ORDRE)(constats).nominal == "ELEVE"

    def test_l_ordre_declare_prime_sur_l_alphabet(self):
        """« CRITIQUE » précède « ELEVE » alphabétiquement, et le suit en gravité."""
        constats = [_montant("CRITIQUE", None), _montant("ELEVE", None)]
        assert niveau_le_plus_eleve(self.ORDRE)(constats).nominal == "CRITIQUE"

    def test_sans_constat_il_n_y_a_pas_de_niveau(self):
        assert niveau_le_plus_eleve(self.ORDRE)([]).nominal is None

    def test_un_niveau_inconnu_est_ignore_du_classement_mais_reste_compte(self):
        """Mieux vaut un rapport incomplet sur ce point qu'une exception au moment de
        conclure : le constat existe, et le taire serait pire que mal le classer."""
        constats = [_montant("MODERE", None), _montant("INVENTE", None)]
        agregat = niveau_le_plus_eleve(self.ORDRE)(constats)
        assert agregat.nominal == "MODERE"
        assert agregat.denombrement == {"INVENTE": 1, "MODERE": 1}


class TestIntervalleAutour:
    """L'agrégateur de la tarification."""

    def test_la_base_seule_quand_aucun_ajustement(self):
        agregat = intervalle_autour(D(500_000), D("0.20"), D("0.50"))([])
        assert agregat.valeur == D(500_000)
        assert agregat.intervalle == (D(400_000), D(750_000))

    def test_les_ajustements_s_appliquent_a_la_base(self):
        constats = [_ajustement(D(100_000)), _ajustement(D(50_000))]
        agregat = intervalle_autour(D(500_000), D("0.20"), D("0.50"))(constats)
        assert agregat.valeur == D(650_000)

    def test_seuls_les_ajustements_comptent(self):
        """Un constat qui n'est pas un ajustement ne déplace pas le prix. Sans ce filtre,
        un enjeu fiscal glissé dans le même rapport modifierait des honoraires."""
        constats = [_ajustement(D(100_000)), _montant("MAJEUR", D(999_999))]
        agregat = intervalle_autour(D(500_000), D("0.20"), D("0.50"))(constats)
        assert agregat.valeur == D(600_000)

    @pytest.mark.parametrize("base", [D(0), D(250_000), D(1_500_000)])
    def test_les_bornes_encadrent_toujours_la_reference(self, base):
        agregat = intervalle_autour(base, D("0.20"), D("0.50"))([])
        plancher, plafond = agregat.intervalle
        assert plancher <= agregat.valeur <= plafond
