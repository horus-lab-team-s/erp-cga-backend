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
    en_toutes_lettres,
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


class TestLeMontantEnToutesLettres:
    """La ligne qui protège un document commercial d'un chiffre altéré.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI ELLE EXISTE

    Les deux proformas que le cabinet emploie la portent : « Arrête la présente
    pro-forma à la somme de : Cinq cent cinq mille francs FCFA. » Ce n'est pas un
    ornement. Un « 5 » devenu « 8 » après signature ne se voit pas ; « cinq cent »
    en face de « 850 000 » se voit.

    ⚠️ **LES PIÈGES SONT TOUS ICI, ET AUCUN NE SE VOIT EN RELISANT LE CODE.**

    Le premier jet rendait « trois-cents-mille » et « cent-quatre-vingts-mille ».
    C'est faux : *cent* et *vingt* ne prennent l's que s'ils **terminent** le
    nombre, et « mille » les suit. Trouvé en déroulant la table, pas en relisant.
    ─────────────────────────────────────────────────────────────────────────────
    """

    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [
            (0, "zéro"),
            (1, "un"),
            (16, "seize"),
            (17, "dix-sept"),
            # Les dizaines qui prennent « et un ».
            (21, "vingt-et-un"),
            (31, "trente-et-un"),
            (61, "soixante-et-un"),
            # 70 et 90 n'ont pas de nom propre : ils se disent sur la dizaine du
            # dessous.
            (70, "soixante-dix"),
            (71, "soixante-onze"),
            (90, "quatre-vingt-dix"),
            (91, "quatre-vingt-onze"),
            # ⚠️ « quatre-vingts » prend un s, « quatre-vingt-un » non.
            (80, "quatre-vingts"),
            (81, "quatre-vingt-un"),
            (180, "cent-quatre-vingts"),
            # ⚠️ Et il le perd dès que « mille » suit.
            (80_000, "quatre-vingt-mille"),
            (180_000, "cent-quatre-vingt-mille"),
            # ⚠️ Même règle pour « cent ».
            (100, "cent"),
            (200, "deux-cents"),
            (203, "deux-cent-trois"),
            (300_000, "trois-cent-mille"),
            # « mille » est invariable, et « un mille » ne se dit pas.
            (1_000, "mille"),
            (2_000, "deux-mille"),
            # Le montant du document réel du cabinet.
            (505_000, "cinq-cent-cinq-mille"),
            # « million » est un nom : il prend un s.
            (1_000_000, "un million"),
            (2_000_000, "deux millions"),
            (2_500_000, "deux millions cinq-cent-mille"),
        ],
    )
    def test_les_accords_du_francais(self, valeur, attendu):
        assert en_toutes_lettres(valeur) == attendu

    def test_un_montant_a_decimales_est_refuse(self):
        """⚠️ Le franc CFA n'a pas de subdivision en circulation.

        Un montant non entier vient donc d'un calcul qui a dérapé ailleurs, et
        l'arrondir ici masquerait ce dérapage.
        """
        with pytest.raises(ValueError, match="pas de subdivision"):
            en_toutes_lettres(Decimal("1000.50"))

    def test_un_montant_negatif_se_dit(self):
        """Il n'en circule pas sur une proforma, mais un avoir en porte un."""
        assert en_toutes_lettres(-1500) == "moins mille-cinq-cents"
