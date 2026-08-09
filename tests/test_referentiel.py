"""Le référentiel normatif : lecture datée, refus de deviner."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contexts.referentiel.modeles import (
    Fondement,
    Parametre,
    StatutValidation,
    Unite,
    VersionParametre,
)
from app.contexts.referentiel.service import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
)

FONDEMENT = Fondement(texte="CGI art. test", source="test")


def _version(valeur, du: date, au: date | None = None) -> VersionParametre:
    return VersionParametre(valeur=valeur, applicable_du=du, applicable_au=au, fondement=FONDEMENT)


def _parametre(*versions: VersionParametre, unite: Unite = Unite.FCFA) -> Parametre:
    return Parametre(
        code="SEUIL_TEST", libelle="Seuil de test", categorie="TEST", unite=unite,
        versions=list(versions),
    )


class TestLectureDatee:
    """Une facture de 2024 se contrôle avec les règles de 2024."""

    SERVICE = ServiceParametres(
        [
            _parametre(
                _version(100_000, date(2019, 1, 1), date(2025, 1, 1)),
                _version(500_000, date(2025, 1, 1)),
            )
        ]
    )

    def test_ancienne_version_pour_une_operation_ancienne(self):
        assert self.SERVICE.valeur_numerique("SEUIL_TEST", date(2024, 6, 30)) == Decimal(100_000)

    def test_nouvelle_version_apres_le_changement(self):
        assert self.SERVICE.valeur_numerique("SEUIL_TEST", date(2025, 1, 1)) == Decimal(500_000)

    def test_borne_haute_exclue(self):
        # [du, au[ : le 31/12/2024 relève encore de l'ancienne version.
        assert self.SERVICE.valeur_numerique("SEUIL_TEST", date(2024, 12, 31)) == Decimal(100_000)

    def test_avant_toute_version_leve_une_erreur(self):
        # Jamais de valeur par défaut : une valeur légale silencieusement absente est un
        # bug fiscal, pas un cas limite.
        with pytest.raises(AucuneVersionApplicable, match="aucune version applicable"):
            self.SERVICE.resoudre("SEUIL_TEST", date(2018, 12, 31))

    def test_code_inconnu_leve_une_erreur(self):
        with pytest.raises(ParametreInconnu, match="absent du référentiel"):
            self.SERVICE.resoudre("CODE_QUI_NEXISTE_PAS", date(2026, 1, 1))


class TestInvariantsDuModele:
    def test_versions_chevauchantes_refusees(self):
        with pytest.raises(ValueError, match="se chevauchent"):
            _parametre(
                _version(1, date(2019, 1, 1)),  # jamais fermée
                _version(2, date(2025, 1, 1)),
            )

    def test_bornes_inversees_refusees(self):
        with pytest.raises(ValueError, match="incohérente"):
            _version(1, date(2025, 1, 1), date(2024, 1, 1))

    def test_statut_valide_exige_une_personne_nommee(self):
        # La validation engage une personne, pas l'éditeur.
        with pytest.raises(ValueError, match="valide_par"):
            VersionParametre(
                valeur=1,
                applicable_du=date(2019, 1, 1),
                statut=StatutValidation.VALIDE,
                fondement=FONDEMENT,
            )

    def test_fondement_obligatoire(self):
        with pytest.raises(ValueError):
            VersionParametre(valeur=1, applicable_du=date(2019, 1, 1))  # type: ignore[call-arg]

    def test_codes_en_double_refuses(self):
        with pytest.raises(ValueError, match="en double"):
            ServiceParametres([_parametre(_version(1, date(2019, 1, 1))),
                               _parametre(_version(2, date(2019, 1, 1)))])


class TestReferentielReel:
    """Contrôles sur le fichier livré, Docs/referentiel/parametres.yaml."""

    DATE = date(2026, 7, 15)

    def test_le_referentiel_se_charge(self, parametres: ServiceParametres):
        assert len(parametres.codes) >= 15

    def test_seuil_especes_resoluble(self, parametres: ServiceParametres):
        resolu = parametres.resoudre("SEUIL_ESPECES_DEDUCTIBILITE_TVA", self.DATE)
        assert resolu.valeur_decimale == Decimal(500_000)
        assert resolu.unite is Unite.FCFA

    def test_divergence_de_sources_documentee(self, parametres: ServiceParametres):
        # Q1 : le cadrage énonce 100 000, les maquettes 500 000. Tant que le fiscaliste
        # n'a pas tranché, la note doit porter la trace du désaccord.
        resolu = parametres.resoudre("SEUIL_ESPECES_DEDUCTIBILITE_TVA", self.DATE)
        assert resolu.note and "DIVERGENCE" in resolu.note

    def test_taux_de_tva_resoluble(self, parametres: ServiceParametres):
        assert parametres.valeur_numerique("TVA_TAUX_GENERAL", self.DATE) == Decimal("19.25")

    def test_format_niu_valide_les_exemples_du_dossier_de_design(
        self, parametres: ServiceParametres
    ):
        import re

        motif = re.compile(parametres.valeur_texte("FORMAT_NIU", self.DATE))
        for niu in ("M081234567890P", "P019876543210K", "M065544332211L"):
            assert motif.match(niu), niu

    def test_rien_nest_encore_opposable(self, parametres: ServiceParametres):
        # Tant que ce test passe, aucun chiffre produit par la plateforme n'est
        # opposable. Il devra être inversé quand le fiscaliste aura validé — c'est
        # volontairement un rappel qui échouera au bon moment.
        assert parametres.codes_non_valides(self.DATE), (
            "Des paramètres sont passés au statut VALIDE : mettre à jour ce test et "
            "Docs/architecture/09-questions-ouvertes.md."
        )
