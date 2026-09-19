"""Un cas passant et un cas échouant par règle du catalogue.

Exigence du § 4.5 du cadrage : « un test unitaire par règle, avec cas passant et cas
échouant, exécuté en CI ». Ajouter une règle sans son couple de tests fait échouer
`test_integrite_referentiel.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.application.moteur_conformite import MoteurConformite
from app.contextes.conformite.domaine.entites import (
    Document,
    FactureAControler,
    LigneFacture,
    ModeReglement,
    Montants,
    Partie,
    RegimeEmetteur,
    Reglement,
    Severite,
)

JUILLET = date(2026, 7, 15)


def _facture(**remplacements) -> FactureAControler:
    """Facture conforme de référence, que chaque test dégrade sur un seul point."""
    base = {
        "document": Document(reference="F-TEST-0001", date_emission=JUILLET),
        "emetteur": Partie(
            denomination="FOURNISSEUR TEST",
            niu="M053311224455R",
            niu_actif=True,
            rccm="RC/DLA/2015/B/0881",
            regime=RegimeEmetteur.REEL,
        ),
        # Le destinataire est l'adhérent. Son régime commande la déductibilité :
        # sans lui, les règles de TVA seraient hors portée et ne se
        # déclencheraient jamais. Voir Portee.regimes_destinataire.
        "destinataire": Partie(
            denomination="SARL BATIMENT PLUS",
            niu="M081234567890P",
            niu_actif=True,
            regime=RegimeEmetteur.REEL,
        ),
        "montants": Montants(total_ht=D(100_000), total_tva=D(19_250), total_ttc=D(119_250)),
        "reglement": Reglement(mode=ModeReglement.VIREMENT),
        "lignes": [LigneFacture(designation="Ciment CPJ 42,5 — 20 sacs", montant_ht=D(100_000))],
    }
    return FactureAControler(**{**base, **remplacements})


def _codes(moteur: MoteurConformite, facture: FactureAControler) -> set[str]:
    rapport = moteur.controler(facture)
    assert not rapport.regles_en_echec, rapport.regles_en_echec
    return {c.code_regle for c in rapport.constats}


class TestFactureDeReference:
    def test_la_facture_de_reference_est_conforme(self, moteur: MoteurConformite):
        # Sans quoi les tests suivants ne prouveraient rien : chacun ne dégrade qu'un point.
        assert _codes(moteur, _facture()) == set()


class TestFAC_ID_003:
    """NIU du fournisseur présent, valide et actif — BLOQUANT."""

    def test_passe_avec_un_niu_valide_et_actif(self, moteur: MoteurConformite):
        assert "FAC-ID-003" not in _codes(moteur, _facture())

    def test_echoue_si_le_niu_est_absent(self, moteur: MoteurConformite):
        facture = _facture(emetteur=Partie(denomination="X", niu=None, regime=RegimeEmetteur.REEL))
        assert "FAC-ID-003" in _codes(moteur, facture)

    def test_echoue_si_le_niu_est_malforme(self, moteur: MoteurConformite):
        facture = _facture(
            emetteur=Partie(
                denomination="X", niu="12345", niu_actif=True, regime=RegimeEmetteur.REEL
            )
        )
        assert "FAC-ID-003" in _codes(moteur, facture)

    def test_echoue_si_le_fournisseur_est_radie(self, moteur: MoteurConformite):
        facture = _facture(
            emetteur=Partie(
                denomination="X", niu="M053311224455R", niu_actif=False, regime=RegimeEmetteur.REEL
            )
        )
        assert "FAC-ID-003" in _codes(moteur, facture)

    def test_ecartee_pour_un_fournisseur_etranger(self, moteur: MoteurConformite):
        # La LF 2025 exclut de la déduction les charges sans mentions obligatoires,
        # SAUF pour les fournisseurs étrangers.
        facture = _facture(
            emetteur=Partie(denomination="X", niu=None, etranger=True, regime=RegimeEmetteur.REEL)
        )
        assert "FAC-ID-003" not in _codes(moteur, facture)

    def test_rejette_tva_et_charge(self, moteur: MoteurConformite):
        facture = _facture(emetteur=Partie(denomination="X", niu=None, regime=RegimeEmetteur.REEL))
        rapport = moteur.controler(facture)
        assert rapport.comptabilisation_interdite
        assert not rapport.tva_deductible
        assert not rapport.charge_deductible


class TestFAC_ACH_007:
    """Règlement en espèces au-delà du seuil — MAJEUR."""

    def test_passe_pour_un_virement_meme_eleve(self, moteur: MoteurConformite):
        facture = _facture(
            montants=Montants(
                total_ht=D(10_000_000), total_tva=D(1_925_000), total_ttc=D(11_925_000)
            ),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(10_000_000))],
            reglement=Reglement(mode=ModeReglement.VIREMENT),
        )
        assert "FAC-ACH-007" not in _codes(moteur, facture)

    def test_passe_en_especes_sous_le_seuil(self, moteur: MoteurConformite):
        # Sous 100 000 FCFA TTC, le règlement en espèces n'exclut rien.
        facture = _facture(
            montants=Montants(total_ht=D(50_000), total_tva=D(9_625), total_ttc=D(59_625)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(50_000))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        assert "FAC-ACH-007" not in _codes(moteur, facture)

    def test_la_borne_du_seuil_est_stricte(self, moteur: MoteurConformite):
        """À EXACTEMENT 100 000 FCFA, LA DÉDUCTION EST REFUSÉE.

        Le CGI art. 143 vise les opérations « d'une valeur au moins égale à cent
        mille » : la borne est incluse dans l'interdiction. Un prédicat écrit
        « <= seuil » au lieu de « < seuil » laisserait passer la facture pile au
        seuil, et c'est précisément le montant qu'un fournisseur choisit quand il
        cherche la limite. Ce test tient la borne.
        """
        pile = _facture(
            montants=Montants(total_ht=D(83_857), total_tva=D(16_143), total_ttc=D(100_000)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(83_857))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        assert "FAC-ACH-007" in _codes(moteur, pile)

    def test_l_ancien_seuil_errone_ne_laisse_plus_passer(self, moteur: MoteurConformite):
        """La facture qui motivait la correction du 18 août 2026.

        300 000 FCFA réglés en espèces : sous l'ancien seuil de 500 000, aucun
        constat n'était émis et la TVA était portée en déductible. L'erreur ne se
        voyait pas à la saisie — elle se serait vue au contrôle, en rappel.
        """
        facture = _facture(
            montants=Montants(total_ht=D(251_572), total_tva=D(48_428), total_ttc=D(300_000)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(251_572))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        assert "FAC-ACH-007" in _codes(moteur, facture)

    def test_echoue_en_especes_au_dela_du_seuil(self, moteur: MoteurConformite):
        facture = _facture(
            montants=Montants(total_ht=D(1_970_650), total_tva=D(379_350), total_ttc=D(2_350_000)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(1_970_650))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        assert "FAC-ACH-007" in _codes(moteur, facture)

    def test_rejette_la_tva_seule(self, moteur: MoteurConformite):
        facture = _facture(
            montants=Montants(total_ht=D(1_970_650), total_tva=D(379_350), total_ttc=D(2_350_000)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(1_970_650))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        rapport = moteur.controler(facture)
        assert not rapport.tva_deductible
        assert rapport.charge_deductible
        assert rapport.enjeu_total == D(379_350)

    def test_sans_objet_pour_un_adherent_au_regime_synthetique(
        self, moteur: MoteurConformite
    ):
        """Un adhérent non assujetti ne récupère jamais la TVA.

        Lui annoncer « TVA non déductible : 379 350 FCFA » énoncerait un
        préjudice qui n'existe pas : qu'il paie en espèces ou par virement, la
        taxe reste un coût définitif incorporé au prix d'achat.

        C'est `Portee.regimes_destinataire` qui écarte la règle — et c'est une
        distinction que le filtre sur le régime de l'ÉMETTEUR ne pouvait pas
        exprimer.
        """
        facture = _facture(
            destinataire=Partie(
                denomination="ETS TCHOUMBA & FILS",
                niu="P019876543210K",
                niu_actif=True,
                regime=RegimeEmetteur.IGS,
            ),
            montants=Montants(total_ht=D(1_970_650), total_tva=D(379_350), total_ttc=D(2_350_000)),
            lignes=[LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(1_970_650))],
            reglement=Reglement(mode=ModeReglement.ESPECES),
        )
        rapport = moteur.controler(facture)
        assert "FAC-ACH-007" not in {c.code_regle for c in rapport.constats}
        assert rapport.enjeu_total == D(0)

    def test_une_regle_hors_portee_n_est_pas_comptee_comme_appliquee(
        self, moteur: MoteurConformite
    ):
        # Sinon le bandeau annoncerait « 5 règles appliquées » alors que l'une
        # d'elles n'a jamais été évaluée.
        au_reel = moteur.controler(_facture())
        au_synthetique = moteur.controler(
            _facture(
                destinataire=Partie(denomination="X", regime=RegimeEmetteur.IGS),
            )
        )
        assert au_synthetique.regles_appliquees == au_reel.regles_appliquees - 1


class TestFAC_CAL_002:
    """Somme des lignes égale au total hors taxes — MAJEUR."""

    def test_passe_quand_les_lignes_totalisent_le_ht(self, moteur: MoteurConformite):
        assert "FAC-CAL-002" not in _codes(moteur, _facture())

    def test_echoue_sur_un_ecart(self, moteur: MoteurConformite):
        facture = _facture(
            lignes=[
                LigneFacture(designation="Ciment CPJ 42,5", montant_ht=D(50_000)),
                LigneFacture(designation="Fers à béton HA12", montant_ht=D(37_600)),
            ]
        )  # 87 600 contre 100 000 annoncés
        assert "FAC-CAL-002" in _codes(moteur, facture)

    def test_pas_de_faux_positif_sans_detail_de_lignes(self, moteur: MoteurConformite):
        # Sans lignes, le contrôle arithmétique n'a pas de prise.
        assert "FAC-CAL-002" not in _codes(moteur, _facture(lignes=[]))

    def test_ne_chiffre_pas_d_enjeu(self, moteur: MoteurConformite):
        facture = _facture(lignes=[LigneFacture(designation="Ciment", montant_ht=D(50_000))])
        constat = next(
            c for c in moteur.controler(facture).constats if c.code_regle == "FAC-CAL-002"
        )
        assert constat.enjeu is None
        assert constat.consequence.rectification_requise


class TestFAC_DOC_011:
    """Désignation suffisamment précise — AVERTISSEMENT."""

    def test_passe_sur_une_designation_precise(self, moteur: MoteurConformite):
        assert "FAC-DOC-011" not in _codes(moteur, _facture())

    def test_echoue_sur_travaux_divers(self, moteur: MoteurConformite):
        facture = _facture(
            lignes=[LigneFacture(designation="Travaux divers", montant_ht=D(100_000))]
        )
        assert "FAC-DOC-011" in _codes(moteur, facture)

    def test_insensible_a_la_casse(self, moteur: MoteurConformite):
        facture = _facture(lignes=[LigneFacture(designation="PRESTATIONS", montant_ht=D(100_000))])
        assert "FAC-DOC-011" in _codes(moteur, facture)

    def test_une_seule_ligne_imprecise_suffit(self, moteur: MoteurConformite):
        facture = _facture(
            lignes=[
                LigneFacture(designation="Ciment CPJ 42,5 — 10 sacs", montant_ht=D(60_000)),
                LigneFacture(designation="divers", montant_ht=D(40_000)),
            ]
        )
        assert "FAC-DOC-011" in _codes(moteur, facture)


class TestFAC_VRA_005:
    """Absence de doublon possible — AVERTISSEMENT."""

    def test_passe_sans_doublon(self, moteur: MoteurConformite):
        assert "FAC-VRA-005" not in _codes(moteur, _facture())

    def test_echoue_avec_un_doublon_repere(self, moteur: MoteurConformite):
        from app.contextes.conformite.domaine.entites import ContexteControle

        facture = _facture(contexte=ContexteControle(doublons_potentiels=1))
        assert "FAC-VRA-005" in _codes(moteur, facture)


class TestJeuDeDemonstration:
    """Les verdicts du § 13.5 du dossier de design doivent être reproduits à l'identique.

    Ce sont les chiffres que le cabinet a vus sur les maquettes : s'ils changent, soit une
    règle a dérivé, soit un paramètre a été modifié sans mesurer l'effet.
    """

    ATTENDUS = {
        "F-2026-0412": (Severite.MAJEUR, D(379_350)),
        "F-2026-0413": (None, D(0)),
        "F-2026-0414": (Severite.BLOQUANT, D(536_625)),
        "F-2026-0415": (Severite.AVERTISSEMENT, D(0)),
        "F-2026-0416": (None, D(0)),
    }

    @pytest.mark.parametrize("reference", sorted(ATTENDUS))
    def test_verdict_conforme_au_dossier_de_design(
        self, moteur: MoteurConformite, reference: str
    ):
        severite_attendue, enjeu_attendu = self.ATTENDUS[reference]
        rapport = moteur.controler(FACTURES_DEMO[reference])
        assert not rapport.regles_en_echec, rapport.regles_en_echec
        assert rapport.severite_maximale is severite_attendue
        assert rapport.enjeu_total == enjeu_attendu

    def test_la_facture_bloquante_porte_aussi_l_avertissement_doublon(
        self, moteur: MoteurConformite
    ):
        rapport = moteur.controler(FACTURES_DEMO["F-2026-0414"])
        assert {c.code_regle for c in rapport.constats} == {"FAC-ID-003", "FAC-VRA-005"}

    def test_les_factures_conformes_le_sont_vraiment(self, moteur: MoteurConformite):
        for reference in ("F-2026-0413", "F-2026-0416"):
            rapport = moteur.controler(FACTURES_DEMO[reference])
            assert rapport.conforme, (reference, rapport.constats)
            assert rapport.regles_appliquees > 0, "aucune règle appliquée : contrôle vide"
