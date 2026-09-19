"""La soudure D → E, éprouvée de bout en bout sur le vrai moteur.

Ces tests ne montent aucun objet factice : ils prennent une facture du jeu de
démonstration, la font passer par le **véritable moteur de conformité** — lequel
résout les paramètres du référentiel daté —, puis appliquent le rapport obtenu à
une écriture comptable réelle.

C'est la chaîne complète du produit, sur un cas que le cabinet a vu sur ses
maquettes :

    facture reçue → règles évaluées → constat chiffré
        → attribut fiscal sur la ligne d'écriture

Si l'un de ces tests casse, c'est que la chaîne de valeur est rompue quelque part.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest

from app.contextes.comptabilite.application.consequences_fiscales import (
    ComptabilisationInterdite,
    appliquer_rapport,
    montant_tva_rejetee,
    reperer_lignes,
    synthetiser,
    verifier_comptabilisation_autorisee,
)
from app.contextes.comptabilite.contrats import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
)
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.application.moteur_conformite import MoteurConformite

JUILLET = date(2026, 7, 12)


def _achat(reference: str, charge: int, tva: int) -> EcritureComptable:
    """L'écriture d'achat telle qu'un comptable la passerait, avant tout contrôle.

    Trois lignes : la charge, la TVA récupérable, la dette fournisseur. C'est la
    forme canonique d'un achat au régime du réel.
    """
    return EcritureComptable(
        journal="AC",
        exercice="2026",
        numero=1,
        date_operation=JUILLET,
        libelle=f"Achat {reference}",
        piece_justificative=reference,
        reference_externe=reference,
        lignes=[
            LigneEcriture(
                compte="604", libelle="Achats de matériaux", sens=Sens.DEBIT, montant=D(charge)
            ),
            LigneEcriture(
                compte="4451",
                libelle="TVA récupérable sur achats",
                sens=Sens.DEBIT,
                montant=D(tva),
            ),
            LigneEcriture(
                compte="401",
                libelle="Fournisseur",
                sens=Sens.CREDIT,
                montant=D(charge + tva),
            ),
        ],
    )


class TestSynthese:
    def test_une_facture_conforme_n_impose_rien(self, moteur: MoteurConformite):
        # F-2026-0413 : transport réglé par virement, fournisseur en règle.
        synthese = synthetiser(moteur.controler(FACTURES_DEMO["F-2026-0413"]))
        assert synthese.sans_consequence
        assert synthese.codes_regles == []

    def test_un_reglement_en_especes_rejette_la_tva_seule(self, moteur: MoteurConformite):
        # F-2026-0412 : 2 350 000 réglés en espèces, au-delà du seuil.
        synthese = synthetiser(moteur.controler(FACTURES_DEMO["F-2026-0412"]))
        assert synthese.tva_deductible is False
        assert synthese.charge_deductible is True
        assert synthese.codes_regles == ["FAC-ACH-007"]
        assert not synthese.comptabilisation_interdite

    def test_un_niu_absent_rejette_la_tva_et_la_charge(self, moteur: MoteurConformite):
        # F-2026-0414 : la Sablière du Moungo ne porte pas de NIU.
        synthese = synthetiser(moteur.controler(FACTURES_DEMO["F-2026-0414"]))
        assert synthese.tva_deductible is False
        assert synthese.charge_deductible is False
        assert "FAC-ID-003" in synthese.codes_regles
        assert synthese.comptabilisation_interdite

    def test_le_motif_est_lisible_par_un_humain(self, moteur: MoteurConformite):
        synthese = synthetiser(moteur.controler(FACTURES_DEMO["F-2026-0412"]))
        assert "espèces" in synthese.motif
        # Jamais un code seul : c'est ce que lira le comptable, et le contrôleur.
        assert synthese.motif != "FAC-ACH-007"

    def test_les_constats_qualitatifs_ne_changent_aucune_ecriture(
        self, moteur: MoteurConformite
    ):
        # F-2026-0415 : désignation « Travaux divers ». Le constat existe et reste
        # au dossier, mais il ne refuse aucune déduction.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0415"])
        assert rapport.constats  # le contexte D a bien relevé quelque chose
        assert synthetiser(rapport).sans_consequence


class TestComptabilisationInterdite:
    def test_une_piece_bloquante_refuse_la_comptabilisation(self, moteur: MoteurConformite):
        # « D décide, E applique » — flux du § 2 de 10-flux-fonctionnels.md.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0414"])
        with pytest.raises(ComptabilisationInterdite, match="FAC-ID-003"):
            verifier_comptabilisation_autorisee(rapport)

    def test_une_anomalie_majeure_n_empeche_pas_la_comptabilisation(
        self, moteur: MoteurConformite
    ):
        # Majeur = comptabilisable, avec conséquence fiscale. Ce n'est pas bloquant.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0412"])
        verifier_comptabilisation_autorisee(rapport)


class TestReperageDesLignes:
    def test_il_distingue_la_tva_des_charges(self):
        ecriture = _achat("F-2026-0412", 1_970_650, 379_350)
        tva, charges = reperer_lignes(ecriture)
        assert tva == [1]
        assert charges == [0]

    def test_les_lignes_de_tiers_sont_ignorees(self):
        # La conséquence fiscale frappe la charge et la taxe, jamais la dette.
        ecriture = _achat("F-2026-0412", 1_970_650, 379_350)
        tva, charges = reperer_lignes(ecriture)
        assert 2 not in tva and 2 not in charges


class TestChaineComplete:
    """Le test le plus important du contexte E."""

    def test_de_la_facture_a_l_attribut_fiscal(self, moteur: MoteurConformite):
        facture = FACTURES_DEMO["F-2026-0412"]
        rapport = moteur.controler(facture)

        ecriture = _achat("F-2026-0412", 1_970_650, 379_350)
        assert not ecriture.porte_une_consequence_fiscale

        qualifiee = appliquer_rapport(ecriture, rapport)

        # La ligne de TVA porte désormais le refus, sa cause et sa traçabilité.
        attribut = qualifiee.lignes[1].attribut_fiscal
        assert attribut is not None
        assert attribut.rejette_tva
        assert attribut.code_regle_origine == "FAC-ACH-007"
        assert attribut.reference_rapport == "F-2026-0412"
        assert attribut.poste_reintegration == "TAB_PASSAGE.TVA_NON_DEDUCTIBLE"

        # La charge, elle, reste déductible : FAC-ACH-007 ne rejette que la TVA.
        assert qualifiee.lignes[0].attribut_fiscal is None

    def test_le_montant_rejete_est_celui_du_verdict(self, moteur: MoteurConformite):
        # 379 350 FCFA — le chiffre que le cabinet a vu sur ses maquettes, et que
        # le bandeau de verdict affiche à l'écran E02.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0412"])
        qualifiee = appliquer_rapport(_achat("F-2026-0412", 1_970_650, 379_350), rapport)

        assert montant_tva_rejetee(qualifiee) == D(379_350)
        assert rapport.enjeu_total == D(379_350)

    def test_une_charge_rejetee_remonte_au_tableau_de_passage(
        self, moteur: MoteurConformite
    ):
        # F-2026-0414 : NIU absent, la charge est réintégrée en plus de la TVA.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0414"])
        qualifiee = appliquer_rapport(_achat("F-2026-0414", 418_000, 80_465), rapport)

        assert montant_tva_rejetee(qualifiee) == D(80_465)
        assert qualifiee.montant_a_reintegrer == D(418_000)
        assert (
            qualifiee.lignes[0].attribut_fiscal.poste_reintegration
            == "TAB_PASSAGE.CHARGES_NON_DEDUCTIBLES"
        )

    def test_une_facture_conforme_laisse_l_ecriture_intacte(self, moteur: MoteurConformite):
        # Un attribut neutre n'apporte rien et alourdirait le grand livre.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0413"])
        ecriture = _achat("F-2026-0413", 1_200_000, 231_000)
        assert appliquer_rapport(ecriture, rapport) is ecriture

    def test_l_ecriture_reste_equilibree_apres_qualification(
        self, moteur: MoteurConformite
    ):
        # Poser un attribut fiscal ne déplace aucun montant : c'est une annotation,
        # pas une écriture d'ajustement.
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0412"])
        qualifiee = appliquer_rapport(_achat("F-2026-0412", 1_970_650, 379_350), rapport)
        assert qualifiee.total_debit == qualifiee.total_credit == D(2_350_000)

    def test_on_ne_qualifie_pas_une_ecriture_deja_validee(self, moteur: MoteurConformite):
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0412"])
        validee = _achat("F-2026-0412", 1_970_650, 379_350).valider(
            "Rodrigue BIYA'A", datetime(2026, 8, 1, 9, 0)
        )
        assert validee.etat is EtatEcriture.VALIDEE
        with pytest.raises(ValueError, match="Contre-passer"):
            appliquer_rapport(validee, rapport)
