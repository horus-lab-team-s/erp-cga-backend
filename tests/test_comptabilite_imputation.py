"""L'imputation : d'une facture contrôlée à l'écriture proposée.

Ces tests ferment le parcours E03 → E02 → E10 des maquettes : la boîte de
réception mène au rapport de conformité, et le rapport mène à l'écriture.

Le cas le plus instructif est le dernier : **la même facture, deux régimes, deux
écritures différentes**. C'est la démonstration que le régime du destinataire
n'est pas un attribut décoratif mais le paramètre qui commande la forme même de
la comptabilité.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.contextes.comptabilite.api import (
    ComptabilisationInterdite,
    ImputationImpossible,
    PlanImputation,
    RegleImputation,
    proposer_ecriture_achat,
    resoudre_compte,
)
from app.contextes.comptabilite.contrats import EtatEcriture, Sens
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.api import (
    Document,
    FactureAControler,
    LigneFacture,
    ModeReglement,
    Montants,
    Partie,
    RegimeEmetteur,
    Reglement,
)
from app.contextes.conformite.application.moteur_conformite import MoteurConformite

PLAN = PlanImputation(
    compte_charge_par_defaut="604",
    regles=[
        RegleImputation(
            motif=r"(?i)ciment|fers? à béton|sable",
            compte="604",
            libelle="Achats de matériaux",
            priorite=10,
        ),
        RegleImputation(
            motif=r"(?i)transport",
            compte="612",
            libelle="Transports",
            priorite=5,
        ),
    ],
)


def _sans_regle(**extra) -> PlanImputation:
    return PlanImputation(compte_charge_par_defaut="604", **extra)


# ── Les règles d'imputation ──────────────────────────────────────────────────────


class TestRegleImputation:
    def test_un_motif_invalide_est_refuse_au_chargement(self):
        # Mieux vaut un démarrage refusé qu'une règle qui ne se déclenche jamais.
        with pytest.raises(ValidationError):
            RegleImputation(motif="([a-z", compte="604", libelle="Cassé")

    def test_le_motif_peut_ignorer_la_casse(self):
        regle = RegleImputation(motif="(?i)ciment", compte="604", libelle="Matériaux")
        assert regle.correspond("CIMENT CPJ 42,5")
        assert regle.correspond("Ciment en sacs")
        assert not regle.correspond("Gasoil")


class TestPlanImputation:
    def test_le_compte_par_defaut_est_un_compte_de_charge(self):
        with pytest.raises(ValidationError, match="classe 6"):
            PlanImputation(compte_charge_par_defaut="401")

    def test_la_priorite_ordonne_les_regles(self):
        # Sans priorité, une règle générale mangerait toutes les spécifiques.
        plan = PlanImputation(
            compte_charge_par_defaut="604",
            regles=[
                RegleImputation(motif=".", compte="605", libelle="Tout", priorite=0),
                RegleImputation(motif="(?i)gasoil", compte="6021", libelle="Carburant", priorite=9),
            ],
        )
        assert resoudre_compte("Gasoil — 240 litres", plan) == "6021"
        assert resoudre_compte("Autre chose", plan) == "605"

    def test_sans_regle_correspondante_on_retombe_sur_le_defaut(self):
        # Jamais une erreur : une facture doit pouvoir être saisie même quand le
        # cabinet n'a pas encore écrit la règle qui la concerne.
        assert resoudre_compte("Prestation inconnue", PLAN) == "604"


# ── La proposition d'écriture ────────────────────────────────────────────────────


class TestPropositionAuRegimeDuReel:
    def test_l_ecriture_est_equilibree(self, moteur: MoteurConformite):
        facture = FACTURES_DEMO["F-2026-0412"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=1,
        )
        assert ecriture.total_debit == ecriture.total_credit == D(2_350_000)

    def test_elle_naît_en_brouillon(self, moteur: MoteurConformite):
        # Le comptable garde la main : le système impute, il n'enregistre pas.
        facture = FACTURES_DEMO["F-2026-0412"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=1,
        )
        assert ecriture.etat is EtatEcriture.BROUILLON
        assert ecriture.modifiable

    def test_une_ligne_de_charge_par_ligne_de_facture(self, moteur: MoteurConformite):
        # F-2026-0412 : ciment et fers à béton, les deux imputés en 604.
        facture = FACTURES_DEMO["F-2026-0412"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=1,
        )
        charges = [ligne for ligne in ecriture.lignes if ligne.compte == "604"]
        assert len(charges) == 2
        assert sum(ligne.montant for ligne in charges) == D(1_970_650)

    def test_les_regles_imputent_par_nature(self, moteur: MoteurConformite):
        # F-2026-0413 : « Transport de matériaux Douala — Kribi » → compte 612.
        facture = FACTURES_DEMO["F-2026-0413"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=2,
        )
        assert [ligne.compte for ligne in ecriture.lignes] == ["612", "4451", "401"]

    def test_la_tva_va_en_classe_4(self, moteur: MoteurConformite):
        # Elle est une créance sur l'État, pas une charge.
        facture = FACTURES_DEMO["F-2026-0413"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=2,
        )
        tva = next(ligne for ligne in ecriture.lignes if ligne.compte == "4451")
        assert tva.sens is Sens.DEBIT
        assert tva.montant == D(231_000)

    def test_le_fournisseur_est_credite_du_ttc_et_porte_son_niu(
        self, moteur: MoteurConformite
    ):
        facture = FACTURES_DEMO["F-2026-0413"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=2,
        )
        fournisseur = next(ligne for ligne in ecriture.lignes if ligne.compte == "401")
        assert fournisseur.sens is Sens.CREDIT
        assert fournisseur.montant == D(1_431_000)
        assert fournisseur.tiers == "M042233445566T"

    def test_l_attribut_fiscal_est_deja_pose(self, moteur: MoteurConformite):
        # C'est tout l'intérêt : l'écriture arrive au comptable déjà qualifiée.
        facture = FACTURES_DEMO["F-2026-0412"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=1,
        )
        tva = next(ligne for ligne in ecriture.lignes if ligne.compte == "4451")
        assert tva.attribut_fiscal is not None
        assert tva.attribut_fiscal.rejette_tva
        assert tva.attribut_fiscal.code_regle_origine == "FAC-ACH-007"

    def test_la_piece_et_la_reference_suivent(self, moteur: MoteurConformite):
        facture = FACTURES_DEMO["F-2026-0412"]
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), PLAN,
            journal="AC", exercice="2026", numero=1,
        )
        assert ecriture.piece_justificative == "F-2026-0412"
        assert ecriture.date_operation == date(2026, 7, 12)


class TestRefusDImputer:
    def test_une_piece_bloquante_n_est_pas_imputee(self, moteur: MoteurConformite):
        # Et le refus a lieu AVANT toute construction : aucun numéro consommé,
        # donc aucun trou dans la séquence.
        facture = FACTURES_DEMO["F-2026-0414"]
        with pytest.raises(ComptabilisationInterdite):
            proposer_ecriture_achat(
                facture, moteur.controler(facture), PLAN,
                journal="AC", exercice="2026", numero=1,
            )

    def test_un_ecart_de_lignes_empeche_l_imputation(self, moteur: MoteurConformite):
        # F-2026-0430 : les lignes totalisent 12 400 de plus que le total annoncé.
        # L'imputation refuse de deviner — c'est le pendant comptable de FAC-CAL-002.
        facture = FACTURES_DEMO["F-2026-0430"]
        with pytest.raises(ImputationImpossible, match="12400|12 400"):
            proposer_ecriture_achat(
                facture, moteur.controler(facture), PLAN,
                journal="AC", exercice="2026", numero=1,
            )


# ── La même facture, deux régimes ────────────────────────────────────────────────


def _facture_igs(regime: RegimeEmetteur) -> FactureAControler:
    """Une facture identique, dont seul le régime du destinataire change."""
    return FactureAControler(
        document=Document(reference="F-TEST-IGS", date_emission=date(2026, 7, 15)),
        emetteur=Partie(
            denomination="GROSSISTE MARCHÉ CENTRAL",
            niu="M090011223344L",
            niu_actif=True,
            rccm="RC/DLA/2015/B/0881",
            regime=RegimeEmetteur.REEL,
        ),
        destinataire=Partie(
            denomination="ETS TCHOUMBA & FILS",
            niu="P019876543210K",
            niu_actif=True,
            regime=regime,
        ),
        montants=Montants(
            total_ht=D(1_000_000), total_tva=D(192_500), total_ttc=D(1_192_500)
        ),
        reglement=Reglement(mode=ModeReglement.VIREMENT),
        lignes=[LigneFacture(designation="Riz parfumé — 200 sacs", montant_ht=D(1_000_000))],
    )


class TestLeRegimeCommandeLaFormeDeLEcriture:
    def test_au_reel_trois_lignes_dont_la_tva(self, moteur: MoteurConformite):
        facture = _facture_igs(RegimeEmetteur.REEL)
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), _sans_regle(),
            journal="AC", exercice="2026", numero=1,
        )
        assert [ligne.compte for ligne in ecriture.lignes] == ["604", "4451", "401"]
        assert ecriture.lignes[0].montant == D(1_000_000)

    def test_au_synthetique_deux_lignes_et_la_tva_devient_un_cout(
        self, moteur: MoteurConformite
    ):
        # Le point le plus important de tout le contexte E : une entreprise non
        # assujettie ne récupère jamais la TVA. Elle s'incorpore au prix d'achat.
        facture = _facture_igs(RegimeEmetteur.IGS)
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), _sans_regle(),
            journal="AC", exercice="2026", numero=1,
        )
        assert [ligne.compte for ligne in ecriture.lignes] == ["604", "401"]
        assert ecriture.lignes[0].montant == D(1_192_500)  # HT + TVA
        assert ecriture.total_debit == ecriture.total_credit == D(1_192_500)

    def test_la_tva_non_recuperable_peut_rester_tracable(self, moteur: MoteurConformite):
        # Point de variation : le cabinet peut choisir un compte dédié plutôt que
        # de noyer la TVA dans l'achat. C'est le traitement à préférer, parce
        # qu'il permet de la retrouver au tableau de passage.
        facture = _facture_igs(RegimeEmetteur.IGS)
        plan = _sans_regle(compte_tva_non_recuperable="6459")
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), plan,
            journal="AC", exercice="2026", numero=1,
        )
        assert [ligne.compte for ligne in ecriture.lignes] == ["604", "6459", "401"]
        dediee = ecriture.lignes[1]
        assert dediee.montant == D(192_500)
        assert dediee.libelle == "TVA non récupérable"

    def test_aucune_ligne_de_classe_4_pour_un_non_assujetti(
        self, moteur: MoteurConformite
    ):
        facture = _facture_igs(RegimeEmetteur.IGS)
        ecriture = proposer_ecriture_achat(
            facture, moteur.controler(facture), _sans_regle(),
            journal="AC", exercice="2026", numero=1,
        )
        comptes_tva = [ligne for ligne in ecriture.lignes if ligne.compte.startswith("445")]
        assert comptes_tva == []
