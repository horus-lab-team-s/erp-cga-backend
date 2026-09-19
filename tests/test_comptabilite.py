"""Contexte E · Comptabilité — les invariants qui font qu'une comptabilité en est une.

Ces tests ne vérifient pas des fonctionnalités : ils empêchent les quatre façons
dont une comptabilité cesse d'être défendable devant l'administration.

* Une écriture déséquilibrée, qui rendrait la balance fausse sans qu'on sache où.
* Une écriture modifiée après validation, qui détruirait la piste d'audit.
* Un trou dans une séquence de numérotation, qui est le premier signal que
  cherche un contrôleur.
* Un sous-compte non rattaché au plan de référence, dont le solde disparaîtrait
  silencieusement de la liasse fiscale.

Le jeu de données du bas de fichier est un exercice minuscule mais **complet et
cohérent** : les chiffres s'additionnent, la balance s'équilibre, et le bilan
boucle par le résultat. C'est volontaire — un jeu de test dont les totaux ne
tombent pas juste ne prouve rien.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.contextes.comptabilite.contrats import (
    AttributFiscal,
    Compte,
    DestinationCompte,
    EcritureComptable,
    EtatEcriture,
    Journal,
    LigneEcriture,
    NatureJournal,
    Sens,
    TypeEcriture,
)
from app.contextes.comptabilite.domaine.projections import (
    balance,
    balance_par_racine,
    controle_balance_equilibree,
    controle_bouclage,
    grand_livre,
    resultat,
    sequences_incompletes,
)

JUILLET = date(2026, 7, 15)
VALIDE_LE = datetime(2026, 8, 1, 9, 0)


# ── Fabriques ────────────────────────────────────────────────────────────────────


def _l(compte: str, sens: Sens, montant: int, **extra) -> LigneEcriture:
    return LigneEcriture(
        compte=compte, libelle=f"Mouvement {compte}", sens=sens, montant=D(montant), **extra
    )


def _ecriture(
    journal: str,
    numero: int,
    lignes: list[LigneEcriture],
    *,
    validee: bool = True,
    date_operation: date = JUILLET,
    **extra,
) -> EcritureComptable:
    base: dict = {
        "journal": journal,
        "exercice": "2026",
        "numero": numero,
        "date_operation": date_operation,
        "libelle": f"Écriture {journal} {numero}",
        "piece_justificative": f"P-{journal}-{numero}",
        "lignes": lignes,
    }
    if validee:
        base |= {
            "etat": EtatEcriture.VALIDEE,
            "validee_par": "Rodrigue BIYA'A",
            "validee_le": VALIDE_LE,
        }
    return EcritureComptable(**(base | extra))


# ── Le plan de comptes ───────────────────────────────────────────────────────────


class TestCompte:
    def test_la_classe_commande_la_destination(self):
        assert Compte(numero="521", intitule="Banque").destination is DestinationCompte.BILAN
        assert Compte(numero="601", intitule="Achats").destination is DestinationCompte.RESULTAT
        assert Compte(numero="89", intitule="Impôt").destination is DestinationCompte.RESULTAT
        assert (
            Compte(numero="90", intitule="Analytique").destination
            is DestinationCompte.HORS_BILAN
        )

    def test_sens_naturel(self):
        assert Compte(numero="601", intitule="Achats").sens_naturel is Sens.DEBIT
        assert Compte(numero="701", intitule="Ventes").sens_naturel is Sens.CREDIT

    def test_la_classe_4_n_a_pas_de_sens_naturel(self):
        # Un fournisseur est créditeur, un client débiteur, et les deux vivent en
        # classe 4 : deviner produirait un contrôle faux.
        assert Compte(numero="401", intitule="Fournisseurs").sens_naturel is None

    def test_un_sous_compte_doit_deriver_de_sa_reference(self):
        # C'est l'invariant qui protège la liasse : sans lui, le solde de 401100
        # n'atteindrait aucun poste de la DSF.
        with pytest.raises(ValidationError, match="doit commencer par"):
            Compte(numero="401100", intitule="Fournisseurs locaux", compte_reference="411")

    def test_un_compte_ne_derive_pas_de_lui_meme(self):
        with pytest.raises(ValidationError, match="ne dérive pas de lui-même"):
            Compte(numero="401", intitule="Fournisseurs", compte_reference="401")

    def test_la_racine_est_le_point_de_mapping(self):
        derive = Compte(numero="401100", intitule="Locaux", compte_reference="401")
        reference = Compte(numero="401", intitule="Fournisseurs")
        assert derive.racine == "401"
        assert reference.racine == "401"


class TestJournal:
    def test_un_journal_de_tresorerie_exige_sa_contrepartie(self):
        with pytest.raises(ValidationError, match="compte de contrepartie"):
            Journal(code="BQ", intitule="Banque", nature=NatureJournal.BANQUE)

    def test_un_journal_d_achats_n_en_porte_pas(self):
        with pytest.raises(ValidationError, match="seul un journal de trésorerie"):
            Journal(
                code="AC",
                intitule="Achats",
                nature=NatureJournal.ACHATS,
                compte_contrepartie="401",
            )


# ── La ligne ─────────────────────────────────────────────────────────────────────


class TestLigneEcriture:
    def test_le_montant_est_strictement_positif(self):
        # Le sens porte la direction, jamais le signe.
        with pytest.raises(ValidationError, match="strictement positif"):
            _l("601", Sens.DEBIT, -1000)

    def test_le_franc_cfa_n_a_pas_de_decimale(self):
        with pytest.raises(ValidationError, match="décimale"):
            LigneEcriture(
                compte="601", libelle="Achat", sens=Sens.DEBIT, montant=D("1000.50")
            )

    def test_l_inversion_change_le_sens_et_efface_le_lettrage(self):
        ligne = _l("401", Sens.CREDIT, 477_000, lettrage="A1")
        inversee = ligne.inversee()
        assert inversee.sens is Sens.DEBIT
        assert inversee.montant == D(477_000)
        assert inversee.lettrage is None


class TestAttributFiscal:
    def test_un_refus_de_deduction_exige_son_motif(self):
        with pytest.raises(ValidationError, match="motif"):
            AttributFiscal(tva_deductible=False, code_regle_origine="FAC-ACH-007")

    def test_un_refus_de_deduction_exige_la_regle_d_origine(self):
        # Sans elle, impossible de remonter de la liasse jusqu'à la facture.
        with pytest.raises(ValidationError, match="règle"):
            AttributFiscal(
                tva_deductible=False, motif_non_deductibilite="Règlement en espèces"
            )

    def test_un_attribut_qui_ne_refuse_rien_est_neutre(self):
        assert AttributFiscal().est_neutre
        assert not AttributFiscal(
            tva_deductible=False,
            motif_non_deductibilite="Règlement en espèces au-delà du seuil",
            code_regle_origine="FAC-ACH-007",
        ).est_neutre


# ── L'écriture ───────────────────────────────────────────────────────────────────


class TestEquilibre:
    def test_une_ecriture_equilibree_est_acceptee(self):
        ecriture = _ecriture(
            "AC",
            1,
            [
                _l("601", Sens.DEBIT, 400_000),
                _l("4451", Sens.DEBIT, 77_000),
                _l("401", Sens.CREDIT, 477_000),
            ],
        )
        assert ecriture.total_debit == ecriture.total_credit == D(477_000)
        assert ecriture.montant == D(477_000)

    def test_une_ecriture_desequilibree_est_refusee(self):
        with pytest.raises(ValidationError, match="déséquilibrée"):
            _ecriture(
                "AC",
                1,
                [_l("601", Sens.DEBIT, 400_000), _l("401", Sens.CREDIT, 399_000)],
            )

    def test_l_equilibre_se_verifie_sur_l_ecriture_pas_sur_la_ligne(self):
        # Trois lignes au débit pour une au crédit : c'est l'ensemble qui compte.
        ecriture = _ecriture(
            "OD",
            1,
            [
                _l("601", Sens.DEBIT, 100_000),
                _l("602", Sens.DEBIT, 200_000),
                _l("604", Sens.DEBIT, 300_000),
                _l("401", Sens.CREDIT, 600_000),
            ],
        )
        assert ecriture.total_debit == D(600_000)

    def test_une_ecriture_d_une_seule_ligne_est_refusee(self):
        with pytest.raises(ValidationError):
            _ecriture("AC", 1, [_l("601", Sens.DEBIT, 400_000)])


class TestValidation:
    LIGNES = [_l("601", Sens.DEBIT, 400_000), _l("401", Sens.CREDIT, 400_000)]

    def test_valider_exige_un_nom_et_une_date(self):
        # Comme pour une version de paramètre au référentiel : la validation
        # engage une personne, pas le logiciel.
        with pytest.raises(ValidationError, match="qui l'a validée"):
            EcritureComptable(
                journal="AC",
                exercice="2026",
                numero=1,
                date_operation=JUILLET,
                libelle="Achat",
                piece_justificative="P-1",
                lignes=self.LIGNES,
                etat=EtatEcriture.VALIDEE,
            )

    def test_valider_exige_la_piece_justificative(self):
        with pytest.raises(ValidationError, match="pièce justificative"):
            EcritureComptable(
                journal="AC",
                exercice="2026",
                numero=1,
                date_operation=JUILLET,
                libelle="Achat",
                lignes=self.LIGNES,
                etat=EtatEcriture.VALIDEE,
                validee_par="Rodrigue BIYA'A",
                validee_le=VALIDE_LE,
            )

    def test_valider_rend_une_copie_et_l_originale_reste_intacte(self):
        brouillon = _ecriture("AC", 1, self.LIGNES, validee=False)
        validee = brouillon.valider("Rodrigue BIYA'A", VALIDE_LE)
        assert brouillon.etat is EtatEcriture.BROUILLON
        assert validee.etat is EtatEcriture.VALIDEE
        assert validee.validee_par == "Rodrigue BIYA'A"
        assert brouillon.modifiable and not validee.modifiable

    def test_on_ne_valide_pas_deux_fois(self):
        with pytest.raises(ValueError, match="déjà validée"):
            _ecriture("AC", 1, self.LIGNES).valider("X", VALIDE_LE)

    def test_la_cle_est_stable_et_triable(self):
        assert _ecriture("AC", 42, self.LIGNES).cle == "2026/AC/000042"


class TestContrepassation:
    ORIGINE = None  # renseignée dans setup_method

    def setup_method(self):
        self.origine = _ecriture(
            "AC",
            7,
            [
                _l("601", Sens.DEBIT, 400_000),
                _l("4451", Sens.DEBIT, 77_000),
                _l("401", Sens.CREDIT, 477_000),
            ],
        )

    def test_elle_inverse_tous_les_sens(self):
        contre = self.origine.contrepasser(8, date(2026, 9, 1), "Compte d'imputation erroné")
        assert [ligne.sens for ligne in contre.lignes] == [
            Sens.CREDIT,
            Sens.CREDIT,
            Sens.DEBIT,
        ]
        assert contre.total_debit == D(477_000)

    def test_elle_designe_l_origine_et_dit_pourquoi(self):
        contre = self.origine.contrepasser(8, date(2026, 9, 1), "Compte d'imputation erroné")
        assert contre.type is TypeEcriture.CONTREPASSATION
        assert contre.ecriture_contrepassee == "2026/AC/000007"
        assert contre.motif_contrepassation == "Compte d'imputation erroné"

    def test_elle_porte_la_date_du_jour_ou_l_on_s_apercoit_de_l_erreur(self):
        # On ne retouche pas le passé : c'est le principe d'intangibilité.
        contre = self.origine.contrepasser(8, date(2026, 9, 1), "Erreur")
        assert contre.date_operation == date(2026, 9, 1)
        assert self.origine.date_operation == JUILLET

    def test_elle_naît_en_brouillon(self):
        # Une annulation se relit avant d'être validée, comme toute écriture.
        assert self.origine.contrepasser(8, date(2026, 9, 1), "Erreur").modifiable

    def test_on_ne_contrepasse_qu_une_ecriture_validee(self):
        brouillon = _ecriture("AC", 7, self.origine.lignes, validee=False)
        with pytest.raises(ValueError, match="écriture validée"):
            brouillon.contrepasser(8, date(2026, 9, 1), "Erreur")

    def test_une_contrepassation_sans_motif_est_refusee(self):
        with pytest.raises(ValueError, match="motif"):
            self.origine.contrepasser(8, date(2026, 9, 1), "   ")

    def test_ensemble_les_deux_ecritures_ne_laissent_aucune_trace_de_solde(self):
        contre = self.origine.contrepasser(8, date(2026, 9, 1), "Erreur").valider(
            "Rodrigue BIYA'A", VALIDE_LE
        )
        soldes = balance([self.origine, contre])
        assert all(s.solde == 0 for s in soldes)
        # …mais les deux écritures demeurent, et c'est tout l'intérêt.
        assert len(soldes) == 3


class TestAttributFiscalSurEcriture:
    LIGNES = [
        _l("601", Sens.DEBIT, 1_970_650),
        _l("601", Sens.DEBIT, 379_350),
        _l("401", Sens.CREDIT, 2_350_000),
    ]

    ATTRIBUT = AttributFiscal(
        tva_deductible=False,
        motif_non_deductibilite="Règlement en espèces au-delà du seuil légal",
        montant_a_reintegrer=D(0),
        poste_reintegration="TAB_PASSAGE.TVA_NON_DEDUCTIBLE",
        code_regle_origine="FAC-ACH-007",
        reference_rapport="RAP-2026-0412",
    )

    def test_on_pose_l_attribut_sur_la_ligne_concernee(self):
        brouillon = _ecriture("AC", 1, self.LIGNES, validee=False)
        avec = brouillon.avec_attribut_fiscal(1, self.ATTRIBUT)
        assert avec.lignes[1].attribut_fiscal is not None
        assert avec.lignes[0].attribut_fiscal is None
        assert avec.porte_une_consequence_fiscale
        # L'originale n'a pas bougé.
        assert not brouillon.porte_une_consequence_fiscale

    def test_on_ne_pose_pas_d_attribut_sur_une_ecriture_validee(self):
        with pytest.raises(ValueError, match="Contre-passer"):
            _ecriture("AC", 1, self.LIGNES).avec_attribut_fiscal(1, self.ATTRIBUT)

    def test_le_montant_a_reintegrer_s_agrege(self):
        attribut = self.ATTRIBUT.model_copy(update={"montant_a_reintegrer": D(418_000)})
        ecriture = _ecriture("AC", 1, self.LIGNES, validee=False).avec_attribut_fiscal(
            1, attribut
        )
        assert ecriture.montant_a_reintegrer == D(418_000)


# ── Un exercice minuscule mais complet ───────────────────────────────────────────
#
#   1 · Vente          D 411 1 192 500  /  C 701 1 000 000  ·  C 4431 192 500
#   2 · Achat          D 601   400 000  ·  D 4451 77 000    /  C 401 477 000
#   3 · Règlement      D 401   477 000  /  C 521 477 000
#   4 · Encaissement   D 521 1 192 500  /  C 411 1 192 500


def _exercice_complet() -> list[EcritureComptable]:
    return [
        _ecriture(
            "VE",
            1,
            [
                _l("411", Sens.DEBIT, 1_192_500, tiers="CLI-001"),
                _l("701", Sens.CREDIT, 1_000_000),
                _l("4431", Sens.CREDIT, 192_500),
            ],
        ),
        _ecriture(
            "AC",
            1,
            [
                _l("601", Sens.DEBIT, 400_000),
                _l("4451", Sens.DEBIT, 77_000),
                _l("401", Sens.CREDIT, 477_000, tiers="FRN-001"),
            ],
        ),
        _ecriture(
            "BQ",
            1,
            [
                _l("401", Sens.DEBIT, 477_000, tiers="FRN-001"),
                _l("521", Sens.CREDIT, 477_000),
            ],
            date_operation=date(2026, 7, 20),
        ),
        _ecriture(
            "BQ",
            2,
            [
                _l("521", Sens.DEBIT, 1_192_500),
                _l("411", Sens.CREDIT, 1_192_500, tiers="CLI-001"),
            ],
            date_operation=date(2026, 7, 25),
        ),
    ]


class TestBalance:
    def test_les_brouillons_sont_exclus_par_defaut(self):
        # Un brouillon n'est pas de la comptabilité : c'est une intention.
        brouillon = _ecriture(
            "OD",
            1,
            [_l("601", Sens.DEBIT, 999_999), _l("401", Sens.CREDIT, 999_999)],
            validee=False,
        )
        assert balance([brouillon]) == []
        assert len(balance([brouillon], brouillons_inclus=True)) == 2

    def test_la_balance_est_equilibree(self):
        soldes = balance(_exercice_complet())
        assert controle_balance_equilibree(soldes)
        assert sum(s.total_debit for s in soldes) == D(3_339_000)

    def test_les_comptes_soldes_restent_visibles(self):
        # Un compte revenu à zéro après mouvements ne dit pas la même chose qu'un
        # compte jamais mouvementé : la balance doit distinguer les deux.
        soldes = {s.compte: s for s in balance(_exercice_complet())}
        assert soldes["401"].solde == 0
        assert soldes["401"].total_debit == D(477_000)
        assert soldes["401"].sens_solde is None

    def test_le_resultat_est_produits_moins_charges(self):
        assert resultat(balance(_exercice_complet())) == D(600_000)

    def test_le_bilan_boucle_par_le_resultat(self):
        soldes = balance(_exercice_complet())
        assert controle_bouclage(soldes)

    def test_la_tva_ne_traverse_pas_le_resultat(self):
        # Elle transite par des comptes de tiers : ni produit, ni charge.
        soldes = {s.compte: s for s in balance(_exercice_complet())}
        assert soldes["4431"].classe == 4
        assert soldes["4451"].classe == 4


class TestBalanceParRacine:
    PLAN = [
        Compte(numero="401", intitule="Fournisseurs"),
        Compte(numero="401100", intitule="Fournisseurs locaux", compte_reference="401"),
        Compte(numero="401200", intitule="Fournisseurs étrangers", compte_reference="401"),
        Compte(numero="601", intitule="Achats"),
    ]

    ECRITURES = [
        _ecriture(
            "AC",
            1,
            [
                _l("601", Sens.DEBIT, 300_000),
                _l("401100", Sens.CREDIT, 300_000),
            ],
        ),
        _ecriture(
            "AC",
            2,
            [
                _l("601", Sens.DEBIT, 700_000),
                _l("401200", Sens.CREDIT, 700_000),
            ],
        ),
    ]

    def test_les_sous_comptes_s_agregent_sur_leur_reference(self):
        # C'est cette projection, et elle seule, qui alimente la liasse.
        racines = {s.compte: s for s in balance_par_racine(balance(self.ECRITURES), self.PLAN)}
        assert set(racines) == {"401", "601"}
        assert racines["401"].total_credit == D(1_000_000)

    def test_un_compte_absent_du_plan_est_refuse(self):
        # Son solde n'atteindrait aucun poste : il disparaîtrait en silence.
        plan_incomplet = [c for c in self.PLAN if c.numero != "401200"]
        with pytest.raises(ValueError, match="absents du plan"):
            balance_par_racine(balance(self.ECRITURES), plan_incomplet)


class TestGrandLivre:
    def test_le_solde_progressif_suit_les_mouvements(self):
        lignes = grand_livre(_exercice_complet(), "521")
        assert [ligne.solde_progressif for ligne in lignes] == [D(-477_000), D(715_500)]

    def test_l_ordre_est_chronologique(self):
        lignes = grand_livre(_exercice_complet(), "411")
        assert [ligne.date_operation for ligne in lignes] == [JUILLET, date(2026, 7, 25)]
        assert lignes[-1].solde_progressif == 0

    def test_le_tiers_et_le_journal_suivent_la_ligne(self):
        lignes = grand_livre(_exercice_complet(), "401")
        assert {ligne.tiers for ligne in lignes} == {"FRN-001"}
        assert {ligne.journal for ligne in lignes} == {"AC", "BQ"}


class TestContinuiteDesSequences:
    LIGNES = [_l("601", Sens.DEBIT, 1_000), _l("401", Sens.CREDIT, 1_000)]

    def test_une_sequence_continue_ne_signale_rien(self):
        ecritures = [_ecriture("AC", n, self.LIGNES) for n in (1, 2, 3)]
        assert sequences_incompletes(ecritures) == []

    def test_un_trou_est_detecte(self):
        # Le premier signal que cherche un contrôleur : une écriture supprimée.
        ecritures = [_ecriture("AC", n, self.LIGNES) for n in (1, 2, 5)]
        anomalies = sequences_incompletes(ecritures)
        assert len(anomalies) == 1
        assert anomalies[0].numeros_manquants == [3, 4]
        assert anomalies[0].journal == "AC"

    def test_un_doublon_est_detecte(self):
        # Aussi grave qu'un trou : deux écritures sous le même numéro.
        ecritures = [_ecriture("AC", n, self.LIGNES) for n in (1, 2, 2)]
        assert sequences_incompletes(ecritures)[0].numeros_en_double == [2]

    def test_chaque_journal_a_sa_propre_sequence(self):
        ecritures = [
            _ecriture("AC", 1, self.LIGNES),
            _ecriture("VE", 1, self.LIGNES),
            _ecriture("BQ", 1, self.LIGNES),
        ]
        assert sequences_incompletes(ecritures) == []
