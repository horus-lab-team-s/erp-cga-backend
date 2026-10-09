"""La composition des proformas, transcrite des documents du cabinet.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le cabinet envoie deux proformas de création de SARL, et le système n'en
produisait aucune : `empreinte_document` hachait un substitut, et le code le
disait — « le contenu du document PDF n'est pas encore produit ».

Les rubriques, leurs lignes et leurs montants ont été **transcrits** des deux
PDF. Ces cas vérifient la transcription contre les seuls chiffres que les
documents impriment en clair : les sous-totaux et le total général. Si une ligne
a été recopiée de travers, l'un des deux ne tombe plus.

⚠️ **LES SOUS-TOTAUX NE SONT PAS AU RÉFÉRENTIEL**, et c'est ce qui donne sa
valeur à ce contrôle : ils se calculent depuis les lignes. Les y recopier aurait
fait comparer un nombre à lui-même.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.souscription.adaptateurs.sortant.compositions_proforma import (
    RepertoireDesCompositions,
)
from app.contextes.souscription.domaine.composition_proforma import (
    CompositionIntrouvable,
    StatutComposition,
)
from app.infrastructure.config import RACINE_DEPOT

JOUR = date(2026, 9, 30)


@pytest.fixture(scope="module")
def repertoire() -> RepertoireDesCompositions:
    return RepertoireDesCompositions.depuis(
        RACINE_DEPOT / "Docs" / "referentiel" / "tarification"
    )


class TestLaTranscriptionEstFidele:
    def test_la_proforma_avec_suivi_totalise_505_000(self, repertoire):
        """Le chiffre encadré du document : TOTAL GENERAL 505 000 FCFA."""
        avec = repertoire.prendre("SARL_SOUS_SEING_AVEC_SUIVI", JOUR)
        assert avec.total == Decimal(505_000)

    def test_la_proforma_sans_suivi_totalise_300_000(self, repertoire):
        sans = repertoire.prendre("SARL_SOUS_SEING_SANS_SUIVI", JOUR)
        assert sans.total == Decimal(300_000)

    @pytest.mark.parametrize(
        ("numero", "sous_total"),
        [("I", 177_000), ("II", 53_000), ("III", 25_000)],
    )
    def test_les_sous_totaux_imprimes_tombent_juste(self, repertoire, numero, sous_total):
        """⚠️ Les trois premières rubriques sont IDENTIQUES sur les deux documents.

        Les vérifier sur les deux, c'est éprouver deux transcriptions distinctes
        du même texte : une faute de frappe dans l'une seulement se verrait.
        """
        for code in ("SARL_SOUS_SEING_AVEC_SUIVI", "SARL_SOUS_SEING_SANS_SUIVI"):
            composition = repertoire.prendre(code, JOUR)
            rubrique = next(r for r in composition.rubriques if r.numero == numero)
            assert rubrique.sous_total == Decimal(sous_total), (code, numero)

    def test_la_difference_entre_les_deux_n_est_pas_additive(self):
        """⚠️ LE POINT QUI INTERDIT D'EN FAIRE UNE RÈGLE.

        Prendre le suivi ajoute deux rubriques (150 000 + 100 000) ET fait
        disparaître les frais de procédure (45 000). 300 000 + 250 000 ne fait
        pas 505 000 : il fait 550 000. C'est une décision commerciale du cabinet,
        et c'est pourquoi le référentiel porte deux compositions nommées plutôt
        qu'une règle.
        """
        assert Decimal(300_000) + Decimal(150_000) + Decimal(100_000) != Decimal(505_000)
        assert Decimal(505_000) == Decimal(300_000) + Decimal(250_000) - Decimal(45_000)


class TestCeQueLeReferentielGarANTIT:
    def test_chaque_composition_dit_d_ou_elle_vient(self, repertoire):
        """Sans la source, personne ne peut vérifier la transcription."""
        for composition in repertoire.toutes():
            assert composition.source.endswith(".pdf"), composition.code

    def test_aucune_n_est_declaree_arretee(self, repertoire):
        """⚠️ `TRANSCRIT` n'est pas `VALIDE`, et la nuance est le sujet.

        Ces montants figurent sur des documents que le cabinet envoie déjà : ils
        valent bien plus qu'une valeur de travail. Mais personne ne les a
        confirmés **dans le système**, et les déclarer arrêtés ferait passer pour
        relu ce qui ne l'est pas.
        """
        for composition in repertoire.toutes():
            assert composition.statut == StatutComposition.TRANSCRIT
            assert composition.arretee is False

    def test_un_code_inconnu_nomme_les_compositions_connues(self, repertoire):
        with pytest.raises(CompositionIntrouvable) as echec:
            repertoire.prendre("SARL_INVENTEE", JOUR)
        assert "SARL_SOUS_SEING_AVEC_SUIVI" in str(echec.value)

    def test_une_date_hors_periode_est_refusee(self, repertoire):
        """Un document daté d'avant l'entrée en vigueur n'a pas de composition."""
        with pytest.raises(CompositionIntrouvable):
            repertoire.prendre("SARL_SOUS_SEING_AVEC_SUIVI", date(2025, 12, 31))
