"""L'évaluation de charge, et la preuve que le moteur est général.

Le pas 6 de la généralisation. C'est le seul pas qui démontre quelque chose : les cinq
précédents produisent une abstraction supposée, celui-ci la confronte à un second usage.

Ce domaine diffère de la conformité sur quatre points — le sujet, l'unité, l'agrégation,
et l'absence de paramètres datés. S'il tourne sans qu'on ait rien ajouté au noyau, alors
le noyau est général. Sinon, il ne l'était pas.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.contextes.portefeuille.adaptateurs.sortant.grille_de_charge import (
    charger_la_grille,
    charger_les_tranches,
)
from app.contextes.portefeuille.application.evaluation_charge import evaluer_la_charge
from app.contextes.portefeuille.domaine.charge import (
    DossierAEvaluer,
    RegleDeCharge,
    Tranche,
    tranche_de,
)
from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.moteur.faits import ErreurSchema, Sujet

D = Decimal
AUJOURD_HUI = date(2026, 9, 9)


RACINE_CHARGE = Path(__file__).resolve().parents[2] / "Docs" / "referentiel" / "charge"


@pytest.fixture(scope="session")
def grille() -> list[RegleDeCharge]:
    return charger_la_grille(RACINE_CHARGE)


@pytest.fixture(scope="session")
def tranches() -> tuple[Tranche, ...]:
    return charger_les_tranches(RACINE_CHARGE)


def _fondement() -> Fondement:
    return Fondement(texte="Sonde de test", source="tests")


def _station_service() -> DossierAEvaluer:
    """Le dossier de la section 14 de la conception, chiffré à 59 points."""
    return DossierAEvaluer(
        reference="DOS-STATION-BONABERI",
        pieces_par_mois=420,
        comptes_bancaires=3,
        salaries=12,
        regime_fiscal="REEL_NORMAL",
        etablissements=2,
        qualite_pieces="MOYENNE",
        exercices_en_retard=0,
    )


def _dossier_ordinaire() -> DossierAEvaluer:
    """Un dossier qui n'atteint aucun critère : le cas où la grille doit se taire."""
    return DossierAEvaluer(
        reference="DOS-BOUTIQUE",
        pieces_par_mois=40,
        comptes_bancaires=1,
        salaries=2,
        regime_fiscal="SIMPLIFIE",
        etablissements=1,
        qualite_pieces="BONNE",
        exercices_en_retard=0,
    )


class TestLaGrille:
    def test_elle_se_charge_depuis_le_referentiel(self, grille):
        assert len(grille) == 7
        assert [r.code for r in grille] == sorted(r.code for r in grille), (
            "l'ordre doit être reproductible d'une évaluation à l'autre"
        )

    def test_chaque_critere_ne_cite_que_des_faits_declares(self, grille, tranches):
        """Le même garde-fou que pour la conformité : un critère qui interroge un fait
        inexistant ajouterait ses points à tous les dossiers, et fausserait des
        honoraires facturés."""
        evaluer_la_charge(_dossier_ordinaire(), grille, tranches, AUJOURD_HUI)

    def test_un_critere_fautif_est_refuse_au_montage(self, grille, tranches):
        faux = RegleDeCharge(
            code="CHG-SONDE-999",
            libelle="Sonde",
            applicable_du=date(2026, 1, 1),
            predicat={"<": [{"var": "pieces_par_moi"}, 200]},
            points=5,
            fondement=_fondement(),
        )
        with pytest.raises(ErreurSchema) as echec:
            evaluer_la_charge(_dossier_ordinaire(), [*grille, faux], tranches, AUJOURD_HUI)
        assert "CHG-SONDE-999" in str(echec.value)
        assert "pieces_par_mois" in str(echec.value), "le message doit suggérer le fait voisin"


class TestScoreDeLaStationService:
    """Le calcul complet de la section 14, ligne par ligne."""

    def test_le_score_vaut_cinquante_neuf(self, grille, tranches):
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert evaluation.score == D(59)

    def test_six_criteres_sur_sept_sont_atteints(self, grille, tranches):
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert {c.code for c in evaluation.criteres_retenus} == {
            "CHG-VOL-002",
            "CHG-BAN-001",
            "CHG-SAL-001",
            "CHG-REG-001",
            "CHG-ETA-001",
            "CHG-QUA-001",
        }
        assert "CHG-RET-001" not in {c.code for c in evaluation.criteres_retenus}, (
            "aucun exercice en retard : le critère ne doit pas se déclencher"
        )

    def test_les_sept_criteres_ont_ete_evalues(self, grille, tranches):
        """Distinct des six retenus : évaluer n'est pas déclencher. Le dire évite de
        croire qu'un dossier a passé six contrôles quand il en a passé sept."""
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert evaluation.criteres_evalues == 7

    def test_le_detail_explique_le_total(self, grille, tranches):
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert sum(c.points for c in evaluation.criteres_retenus) == evaluation.score

    def test_la_tranche_est_soutenue(self, grille, tranches):
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert evaluation.tranche.code == "SOUTENU"
        assert evaluation.jours_par_mois == D("2.5")


class TestDossierOrdinaire:
    def test_aucun_critere_atteint_donne_un_score_nul(self, grille, tranches):
        evaluation = evaluer_la_charge(_dossier_ordinaire(), grille, tranches, AUJOURD_HUI)
        assert evaluation.criteres_retenus == []
        assert evaluation.score == D(0)

    def test_un_score_nul_tombe_dans_la_tranche_legere(self, grille, tranches):
        evaluation = evaluer_la_charge(_dossier_ordinaire(), grille, tranches, AUJOURD_HUI)
        assert evaluation.tranche.code == "LEGER"


class TestTranches:
    @pytest.mark.parametrize(
        ("score", "attendue"),
        [(0, "LEGER"), (20, "LEGER"), (21, "STANDARD"), (45, "STANDARD"),
         (46, "SOUTENU"), (75, "SOUTENU"), (76, "LOURD"), (200, "LOURD")],
    )
    def test_les_bornes_sont_tenues(self, tranches, score: int, attendue: str):
        """Les bornes se testent aux valeurs exactes : c'est là que les grilles se
        trompent, et un dossier mal classé se facture mal."""
        assert tranche_de(tranches, D(score)).code == attendue

    def test_les_tranches_couvrent_tout_sans_trou(self, tranches):
        for basse, haute in zip(tranches, tranches[1:], strict=False):
            assert basse.maximum is not None
            assert haute.minimum == basse.maximum + 1, (
                f"trou ou recouvrement entre {basse.code} et {haute.code}"
            )
        assert tranches[-1].maximum is None, (
            "la dernière tranche n'a pas de plafond, pour qu'aucun dossier ne reste sans "
            "classement"
        )


class TestVigueurDatee:
    def test_un_critere_pas_encore_applicable_est_ignore(self, grille, tranches):
        futur = RegleDeCharge(
            code="CHG-FUTUR-001",
            libelle="Critère à venir",
            applicable_du=date(2027, 1, 1),
            predicat={"==": [1, 2]},
            points=99,
            fondement=_fondement(),
        )
        evaluation = evaluer_la_charge(
            _dossier_ordinaire(), [*grille, futur], tranches, AUJOURD_HUI
        )
        assert evaluation.score == D(0)
        assert evaluation.criteres_evalues == 7, "le critère futur ne doit pas être compté"

    def test_un_critere_abroge_est_ignore(self, grille, tranches):
        ancien = RegleDeCharge(
            code="CHG-ANCIEN-001",
            libelle="Critère abrogé",
            applicable_du=date(2020, 1, 1),
            applicable_au=date(2026, 1, 1),
            predicat={"==": [1, 2]},
            points=99,
            fondement=_fondement(),
        )
        evaluation = evaluer_la_charge(
            _dossier_ordinaire(), [*grille, ancien], tranches, AUJOURD_HUI
        )
        assert evaluation.score == D(0)


class TestLeMoteurEstGeneral:
    """La conclusion du chantier, énoncée comme un test."""

    def test_le_dossier_satisfait_le_meme_protocole_que_la_facture(self):
        assert issubclass(DossierAEvaluer, Sujet)

    def test_le_meme_moteur_sert_deux_domaines(self, grille, tranches, regles, parametres):
        """Deux sujets, deux grilles, deux unités, deux agrégations. Un seul moteur.

        Si ce test passe sans qu'on ait rien ajouté à `app/moteur/` pour l'écrire, alors
        la généralisation des pas 1 à 5 tient. C'est la seule preuve qui compte.
        """
        from app.contextes.conformite.application.moteur_conformite import MoteurConformite
        from app.contextes.conformite.domaine.entites import (
            Document,
            FactureAControler,
            LigneFacture,
            Montants,
            Partie,
            RegimeEmetteur,
        )

        facture = FactureAControler(
            document=Document(reference="F-2026-0001", date_emission=date(2026, 7, 15)),
            emetteur=Partie(niu="M053311224455R", niu_actif=True, regime=RegimeEmetteur.REEL),
            destinataire=Partie(niu="M081234567890P", regime=RegimeEmetteur.REEL),
            montants=Montants(total_ht=D(100_000), total_tva=D(19_250), total_ttc=D(119_250)),
            lignes=[LigneFacture(designation="Ciment", montant_ht=D(100_000))],
        )

        conformite = MoteurConformite(regles, parametres).controler(facture)
        charge = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)

        # Le même mécanisme, deux natures de résultat.
        assert conformite.enjeu_total >= D(0)
        assert charge.score == D(59)
        assert conformite.regles_appliquees > 0
        assert charge.criteres_evalues == 7


class TestDisciplineDeJustification:
    """Le recadrage : un critère de charge obéit aux mêmes principes qu'une règle fiscale.

    Le fondement n'est pas légal ici mais interne. La raison d'être du principe ne change
    pas pour autant : un responsable qui annonce un score sans pouvoir dire d'où viennent
    ses points ne peut pas le défendre devant un client qui trouve ses honoraires élevés.
    """

    def test_chaque_critere_porte_un_fondement(self, grille):
        for critere in grille:
            assert critere.fondement.texte.strip(), critere.code
            assert critere.fondement.source.strip(), critere.code

    def test_un_critere_sans_fondement_est_refuse(self):
        with pytest.raises(ValidationError):
            RegleDeCharge(
                code="CHG-SANS-FOND",
                libelle="Sans fondement",
                applicable_du=date(2026, 1, 1),
                predicat={"==": [1, 1]},
                points=5,
            )

    def test_un_critere_valide_sans_signataire_est_refuse(self):
        """Un critère VALIDE sans signataire vaudrait moins qu'un critère A_VALIDER : il
        affirmerait sans engager personne."""
        with pytest.raises(ValidationError, match="valide_par"):
            RegleDeCharge(
                code="CHG-NON-SIGNE",
                libelle="Validé sans signataire",
                applicable_du=date(2026, 1, 1),
                predicat={"==": [1, 1]},
                points=5,
                fondement=_fondement(),
                statut=StatutValidation.VALIDE,
            )

    def test_un_critere_qui_ne_pese_rien_est_refuse(self):
        with pytest.raises(ValidationError, match="pèse rien"):
            RegleDeCharge(
                code="CHG-ZERO",
                libelle="Zéro point",
                applicable_du=date(2026, 1, 1),
                predicat={"==": [1, 1]},
                points=0,
                fondement=_fondement(),
            )

    def test_la_grille_actuelle_n_est_pas_encore_opposable(self, grille, tranches):
        """Les sept critères viennent de la conception, pas d'une grille transmise par le
        centre. Tant qu'ils portent A_VALIDER, le score sert à répartir la charge en
        interne, jamais à justifier un honoraire."""
        evaluation = evaluer_la_charge(_station_service(), grille, tranches, AUJOURD_HUI)
        assert evaluation.repose_sur_des_criteres_non_valides

    def test_sans_critere_retenu_rien_n_est_marque_douteux(self, grille, tranches):
        """Aucun critère atteint, donc aucune valeur non validée employée. Marquer ce cas
        comme douteux ferait douter d'un score qui ne repose sur rien."""
        evaluation = evaluer_la_charge(_dossier_ordinaire(), grille, tranches, AUJOURD_HUI)
        assert not evaluation.repose_sur_des_criteres_non_valides


class TestLesTranchesSontDesDonnees:
    """Principe n° 1 appliqué à une valeur qui n'est pas légale mais qui varie."""

    def test_elles_viennent_du_referentiel_et_non_du_code(self, tranches):
        assert len(tranches) == 4
        assert [t.code for t in tranches] == ["LEGER", "STANDARD", "SOUTENU", "LOURD"]

    def test_le_fichier_porte_son_fondement(self):
        donnees = yaml.safe_load((RACINE_CHARGE / "tranches.yaml").read_text(encoding="utf-8"))
        assert donnees["fondement"]["texte"].strip()
        assert donnees["fondement"]["source"].strip()
        assert donnees["statut"] == "A_VALIDER"

    def test_aucune_valeur_de_tranche_n_est_ecrite_dans_le_code(self):
        """Le contrôle qui empêche la régression : si quelqu'un remet une constante dans
        le domaine, ce test le dit."""
        source = (
            Path(__file__).resolve().parents[1]
            / "app" / "contextes" / "portefeuille" / "domaine" / "charge.py"
        ).read_text(encoding="utf-8")
        for interdite in ("minimum=0", "minimum=21", "minimum=46", "minimum=76"):
            assert interdite not in source, (
                f"« {interdite} » est revenu dans le code. Les paliers vivent dans "
                "Docs/referentiel/charge/tranches.yaml."
            )
