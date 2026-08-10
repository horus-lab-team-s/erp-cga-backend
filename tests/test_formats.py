"""Formats de restitution — § 4 du dossier de design.

Les exemples testés ici sont exactement ceux du tableau du § 4 : s'ils changent, c'est le
dossier de design qui a changé, pas le code.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.partage.formats import (
    ESPACE_FINE,
    date_courte,
    date_longue,
    montant,
    montant_fcfa,
    taux,
)


def normal(texte: str) -> str:
    """Ramène l'espace insécable étroite à une espace ordinaire, pour comparaison."""
    return texte.replace(ESPACE_FINE, " ")


class TestMontants:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (2_350_000, "2 350 000"),
            (536_625, "536 625"),
            (0, "0"),
            (999, "999"),
            (1_000, "1 000"),
            (1_240_000_000, "1 240 000 000"),
        ],
    )
    def test_separateur_de_milliers(self, valeur, attendu):
        assert normal(montant(valeur)) == attendu

    def test_negatif_entre_parentheses_jamais_signe(self):
        assert normal(montant(-450_000)) == "(450 000)"
        assert "-" not in montant(-450_000)

    def test_aucune_decimale(self):
        # Le FCFA n'a pas de subdivision : arrondi au plus proche, demi vers le haut.
        assert normal(montant(Decimal("2350000.4"))) == "2 350 000"
        assert normal(montant(Decimal("2350000.5"))) == "2 350 001"

    def test_suffixe_fcfa(self):
        assert normal(montant_fcfa(2_350_000)) == "2 350 000 FCFA"

    def test_espace_insecable_pour_ne_pas_couper_un_montant(self):
        assert ESPACE_FINE in montant(2_350_000)
        assert " " not in montant(2_350_000)


class TestTaux:
    def test_virgule_decimale(self):
        assert normal(taux(Decimal("19.25"))) == "19,25 %"

    def test_zeros_superflus_supprimes(self):
        assert normal(taux(Decimal("10"))) == "10 %"
        assert normal(taux(Decimal("1.50"))) == "1,5 %"


class TestDates:
    def test_forme_longue(self):
        assert date_longue(date(2026, 8, 15)) == "15 août 2026"
        assert date_longue(date(2026, 3, 1)) == "1 mars 2026"

    def test_forme_courte_pour_les_tableaux(self):
        assert date_courte(date(2026, 8, 15)) == "15/08/2026"
        assert date_courte(date(2026, 3, 1)) == "01/03/2026"
