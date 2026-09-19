"""Contexte H · Clôture et DSF.

Ce que ces tests protègent, dans l'ordre où l'erreur coûte le plus cher :

1. **La distinction TVA rejetée / charge refusée.** Les confondre réintègre une
   somme qui n'a jamais diminué le résultat : l'adhérent paie l'impôt deux fois
   sur le même montant, une fois en TVA non récupérée et une fois en base
   majorée. Personne ne s'en plaindra à l'administration.
2. **Le sens du solde décide du côté du bilan.** Un compte 44 débiteur est une
   créance sur l'État, pas une dette ; un compte bancaire créditeur est un
   découvert, pas un actif. L'erreur fait paraître l'entreprise d'autant plus
   solide qu'elle est plus à découvert.
3. **Le refus d'équilibrer d'office.** Une balance fausse doit produire une
   liasse fausse **et un contrôle en échec**, jamais un ajustement silencieux.
4. **L'abattement CGA.** Jamais sur un déficit, jamais sans adhésion, et toujours
   après les réintégrations.

Le référentiel réel est employé : ces tests vérifient la mécanique **et** que les
valeurs du dépôt la traversent.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.contextes.cloture.api import (
    ConsequenceAReintegrer,
    NaturePassage,
    SensPoste,
    SystemeDsf,
    assembler_les_etats,
    determiner_le_systeme,
    etablir_le_passage,
    moissonner,
    poste_du_compte,
)
from app.contextes.comptabilite.api import (
    AttributFiscal,
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    SoldeCompte,
)
from app.contextes.referentiel.api import (
    DepotParametresYaml,
    ServiceParametres,
)
from app.infrastructure.config import configuration

CLOTURE = date(2026, 12, 31)


@pytest.fixture(scope="module")
def parametres() -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    )


def _solde(compte: str, debit: str = "0", credit: str = "0") -> SoldeCompte:
    return SoldeCompte(
        compte=compte, total_debit=Decimal(debit), total_credit=Decimal(credit)
    )


def _balance_equilibree() -> list[SoldeCompte]:
    """Une balance qui tient : capital 5 M, vente 15 M, achat 9,24 M HT.

    Débits  : banque 20 000 000 + achat 9 240 000 + TVA 1 778 700 = 31 018 700
    Crédits : capital 5 000 000 + vente 15 000 000 + fournisseur 11 018 700
    """
    return [
        _solde("101", credit="5000000"),
        _solde("401", credit="11018700"),
        _solde("4451", debit="1778700"),
        _solde("521", debit="20000000"),
        _solde("601", debit="9240000"),
        _solde("701", credit="15000000"),
    ]


# ══ Le classement des comptes ═════════════════════════════════════════════════
class TestClassementDesComptes:
    def test_un_compte_de_tiers_debiteur_est_une_creance(self):
        """LE TEST QUI PORTE LA RÈGLE CENTRALE DU PLAN.

        Un crédit de TVA reportable est un compte 44 **débiteur** : c'est une
        créance sur l'État. Le ranger au passif parce que « 44 est ordinairement
        une dette » le compterait du mauvais côté du bilan.
        """
        poste = poste_du_compte("4451", SystemeDsf.NORMAL, solde_debiteur=True)
        assert poste is not None
        assert poste.sens is SensPoste.ACTIF

    def test_le_meme_compte_crediteur_est_une_dette(self):
        poste = poste_du_compte("4441", SystemeDsf.NORMAL, solde_debiteur=False)
        assert poste is not None
        assert poste.sens is SensPoste.PASSIF

    def test_une_banque_crediteur_est_un_decouvert(self):
        """L'entreprise paraîtrait d'autant plus solide qu'elle est à découvert."""
        actif = poste_du_compte("521", SystemeDsf.NORMAL, solde_debiteur=True)
        passif = poste_du_compte("521", SystemeDsf.NORMAL, solde_debiteur=False)
        assert actif is not None and actif.sens is SensPoste.ACTIF
        assert passif is not None and passif.sens is SensPoste.PASSIF

    def test_un_client_crediteur_est_une_avance_recue(self):
        poste = poste_du_compte("411", SystemeDsf.NORMAL, solde_debiteur=False)
        assert poste is not None
        assert poste.sens is SensPoste.PASSIF

    def test_le_prefixe_le_plus_long_l_emporte(self):
        """« 601 » gagne sur « 60 » : sans cet arbitrage, l'ordre de déclaration
        du plan déciderait du résultat."""
        assert poste_du_compte("6011", SystemeDsf.NORMAL).code == "RA"
        assert poste_du_compte("6021", SystemeDsf.NORMAL).code == "RB"

    def test_un_compte_hors_plan_rend_none_et_non_un_fourre_tout(self):
        """Le poste « divers » équilibre le bilan en dissimulant exactement ce
        qu'il faudrait voir."""
        assert poste_du_compte("999", SystemeDsf.NORMAL) is None


# ══ L'assemblage des états ════════════════════════════════════════════════════
class TestAssemblage:
    def test_une_balance_equilibree_donne_deux_resultats_concordants(self, parametres):
        """Le contrôle inter-états le plus important de SYSCOHADA.

        Les deux chemins sont indépendants : produits − charges d'un côté,
        actif − passif de l'autre. Les faire dériver l'un de l'autre rendrait le
        contrôle toujours satisfait et parfaitement inutile.
        """
        etats = assembler_les_etats(_balance_equilibree(), SystemeDsf.NORMAL)
        assert etats.resultat_comptable == Decimal(5760000)
        assert etats.resultat_par_le_bilan == etats.resultat_comptable
        assert etats.coherent

    def test_une_balance_fausse_n_est_pas_equilibree_d_office(self, parametres):
        """LE TEST QUI SÉPARE UN OUTIL DÉFENDABLE D'UN OUTIL QUI « MARCHE ».

        La tentation d'ajouter un poste d'écart est grande, et elle transforme
        une erreur visible en erreur invisible.
        """
        fausse = [*_balance_equilibree(), _solde("601", debit="1000000")]
        etats = assembler_les_etats(fausse, SystemeDsf.NORMAL)
        balance_ok = next(c for c in etats.controles if c.code == "BALANCE_EQUILIBREE")
        assert not balance_ok.satisfait
        assert balance_ok.ecart == Decimal(1000000)
        assert not etats.coherent

    def test_un_compte_non_couvert_est_signale_nommement(self):
        avec_intrus = [*_balance_equilibree(), _solde("999", debit="500000")]
        etats = assembler_les_etats(avec_intrus, SystemeDsf.NORMAL)
        assert etats.comptes_non_couverts == ("999",)
        controle = next(c for c in etats.controles if c.code == "TOUS_COMPTES_VENTILES")
        assert not controle.satisfait
        assert "999" in controle.explication

    def test_un_compte_solde_n_apparait_pas(self):
        """Une ligne à zéro n'apprend rien et allonge l'état."""
        avec_zero = [*_balance_equilibree(), _solde("706", debit="100", credit="100")]
        etats = assembler_les_etats(avec_zero, SystemeDsf.NORMAL)
        assert all(ligne.montant != 0 for ligne in etats.lignes)

    def test_les_amortissements_restent_negatifs(self):
        """Ils viennent en diminution de l'actif brut. Les mettre en valeur
        absolue doublerait l'actif immobilisé."""
        soldes = [
            _solde("241", debit="10000000"),
            _solde("2841", credit="4000000"),
            _solde("101", credit="6000000"),
        ]
        etats = assembler_les_etats(soldes, SystemeDsf.NORMAL)
        amortissements = next(ligne for ligne in etats.lignes if ligne.poste == "AZ")
        assert amortissements.montant == Decimal(-4000000)
        assert etats.total_actif == Decimal(6000000)

    def test_chaque_ligne_porte_les_comptes_qui_l_ont_alimentee(self):
        """La trace vers la balance : « pourquoi ce montant ? » se pose à chaque
        revue, et y répondre en ouvrant le code n'est pas une réponse."""
        etats = assembler_les_etats(_balance_equilibree(), SystemeDsf.NORMAL)
        assert all(ligne.comptes for ligne in etats.lignes)


# ══ Le système de présentation ════════════════════════════════════════════════
class TestSysteme:
    def test_le_systeme_se_constate_a_partir_du_chiffre_d_affaires(self, parametres):
        petit, _ = determiner_le_systeme(Decimal(10000000), parametres, CLOTURE)
        grand, _ = determiner_le_systeme(Decimal(200000000), parametres, CLOTURE)
        assert petit is SystemeDsf.MINIMAL
        assert grand is SystemeDsf.NORMAL

    def test_a_la_valeur_meme_du_seuil_le_systeme_est_normal(self, parametres):
        """⚠️ **La borne vient du référentiel.** Le système minimal est réservé à qui
        « reste sous » le seuil : à 60 000 000 exactement, la liasse relève du normal.

        La comparaison était écrite ici avec `>=`, et elle avait raison. Ce cas la
        rattache au texte, pour qu'elle ne reste pas juste par hasard.
        """
        seuil = parametres.resoudre("SEUIL_SYSTEME_NORMAL", CLOTURE).valeur_decimale
        au_seuil, _ = determiner_le_systeme(seuil, parametres, CLOTURE)
        juste_sous, _ = determiner_le_systeme(seuil - 1, parametres, CLOTURE)
        assert au_seuil is SystemeDsf.NORMAL
        assert juste_sous is SystemeDsf.MINIMAL

    def test_le_seuil_non_valide_est_signale(self, parametres_non_arretes):
        _, non_valide = determiner_le_systeme(
            Decimal(10000000), parametres_non_arretes, CLOTURE
        )
        assert non_valide, "un seuil A_VALIDER : le classement doit le dire"

    def test_le_seuil_valide_ne_declenche_aucune_reserve(self, parametres):
        """Le pendant du précédent, et il compte autant.

        Un produit qui signalerait une réserve sur *toute* valeur, validée ou non,
        n'apprendrait rien au lecteur : le bandeau deviendrait décor. Depuis que
        `SEUIL_SYSTEME_NORMAL` est validé sur l'AUDCIF, le classement doit se
        taire."""
        _, non_valide = determiner_le_systeme(Decimal(10000000), parametres, CLOTURE)
        assert not non_valide

    def test_un_seuil_absent_rend_le_systeme_normal(self):
        """Le régime de droit commun : le retenir par défaut ne fait courir aucun
        risque de rejet, là où un SMT indu est refusé."""
        systeme, non_valide = determiner_le_systeme(
            Decimal(1000), ServiceParametres([]), CLOTURE
        )
        assert systeme is SystemeDsf.NORMAL
        assert non_valide


# ══ La moisson des réintégrations ═════════════════════════════════════════════
def _ecriture(
    *,
    piece: str,
    lignes: list[LigneEcriture],
    etat: EtatEcriture = EtatEcriture.VALIDEE,
) -> EcritureComptable:
    """Fabrique une écriture pour les tests.

    ⚠️ `validee_par` et `validee_le` sont obligatoires dès que l'état est
    VALIDEE : le domaine le refuse autrement. « Le Centre engage sa
    responsabilité sur ce qu'il présente : la validation est un acte personnel,
    pas un changement d'état anonyme. » Ce fabricant les renseigne donc plutôt
    que de contourner l'invariant — un test qui contourne un invariant finit par
    tester un objet que le produit ne peut pas construire.
    """
    validation = (
        {"validee_par": "a.bouba@cga-brcg.cm", "validee_le": datetime(2026, 10, 16, 9, 0)}
        if etat is EtatEcriture.VALIDEE
        else {}
    )
    return EcritureComptable(
        journal="AC",
        exercice="2026",
        numero=1,
        date_operation=date(2026, 10, 15),
        libelle="Achat",
        piece_justificative=piece,
        lignes=lignes,
        etat=etat,
        **validation,
    )


def _ligne(compte: str, montant: str, sens: Sens, attribut=None) -> LigneEcriture:
    return LigneEcriture(
        compte=compte,
        libelle="ligne",
        sens=sens,
        montant=Decimal(montant),
        attribut_fiscal=attribut,
    )


class TestMoisson:
    def test_une_tva_rejetee_n_est_pas_reintegree(self):
        """LE TEST LE PLUS IMPORTANT DU CONTEXTE.

        Une TVA non déductible ne touche pas le résultat mais la déclaration de
        TVA. La réintégrer ferait payer l'impôt deux fois sur la même somme : une
        fois en TVA non récupérée, une fois en base imposable majorée. Et
        personne ne s'en plaindrait à l'administration.
        """
        rejet = AttributFiscal(
            tva_deductible=False,
            motif_non_deductibilite="Règlement en espèces au-delà du seuil",
            code_regle_origine="FAC-ACH-007",
        )
        ecriture = _ecriture(
            piece="F-2026-0412",
            lignes=[
                _ligne("601", "2000000", Sens.DEBIT),
                _ligne("4451", "379350", Sens.DEBIT, rejet),
                _ligne("401", "2379350", Sens.CREDIT),
            ],
        )
        moisson = moissonner([ecriture])
        assert moisson.a_reintegrer == (), "une TVA rejetée ne se réintègre pas"
        assert moisson.tva_rejetee == Decimal(379350)
        assert moisson.pieces_a_verifier == ("F-2026-0412",)

    def test_une_charge_refusee_se_reintegre(self):
        refus = AttributFiscal(
            charge_deductible=False,
            motif_non_deductibilite="Désignation trop imprécise",
            code_regle_origine="FAC-DOC-011",
        )
        ecriture = _ecriture(
            piece="F-2026-0418",
            lignes=[
                _ligne("628", "120000", Sens.DEBIT, refus),
                _ligne("401", "120000", Sens.CREDIT),
            ],
        )
        moisson = moissonner([ecriture])
        assert moisson.total_a_reintegrer == Decimal(120000)
        assert moisson.a_reintegrer[0].reference_piece == "F-2026-0418"
        assert moisson.a_reintegrer[0].regles == ("FAC-DOC-011",)

    def test_une_fraction_refusee_ne_reintegre_que_cette_fraction(self):
        """Une règle peut refuser la quote-part privée d'un véhicule : réintégrer
        la ligne entière ferait payer sur une dépense partiellement
        professionnelle."""
        partiel = AttributFiscal(
            charge_deductible=False,
            motif_non_deductibilite="Quote-part privée du véhicule",
            montant_a_reintegrer=Decimal(30000),
            code_regle_origine="FAC-CHA-020",
        )
        ecriture = _ecriture(
            piece="F-2026-0421",
            lignes=[
                _ligne("624", "100000", Sens.DEBIT, partiel),
                _ligne("401", "100000", Sens.CREDIT),
            ],
        )
        assert moissonner([ecriture]).total_a_reintegrer == Decimal(30000)

    def test_un_brouillon_est_ignore(self):
        """Le réintégrer ferait dépendre le résultat fiscal d'une saisie que
        personne n'a arrêtée, et le montant changerait entre deux consultations."""
        refus = AttributFiscal(
            charge_deductible=False,
            motif_non_deductibilite="Charge refusée",
            code_regle_origine="FAC-DOC-011",
        )
        brouillon = _ecriture(
            piece="F-2026-0499",
            lignes=[
                _ligne("628", "999999", Sens.DEBIT, refus),
                _ligne("401", "999999", Sens.CREDIT),
            ],
            etat=EtatEcriture.BROUILLON,
        )
        assert moissonner([brouillon]).a_reintegrer == ()

    def test_la_reference_est_celle_de_la_piece_pas_de_l_ecriture(self):
        """C'est la facture que le vérificateur demandera, pas l'écriture qui
        l'enregistre."""
        refus = AttributFiscal(
            charge_deductible=False,
            motif_non_deductibilite="Charge refusée",
            code_regle_origine="FAC-DOC-011",
        )
        ecriture = _ecriture(
            piece="F-2026-0418",
            lignes=[
                _ligne("628", "1000", Sens.DEBIT, refus),
                _ligne("401", "1000", Sens.CREDIT),
            ],
        )
        assert moissonner([ecriture]).a_reintegrer[0].reference_piece == "F-2026-0418"


# ══ Le tableau de passage ═════════════════════════════════════════════════════
class TestPassageFiscal:
    def _conséquence(self, montant: str = "120000") -> ConsequenceAReintegrer:
        return ConsequenceAReintegrer(
            reference_piece="F-2026-0418",
            motif="Charge non déductible — désignation trop imprécise",
            montant=Decimal(montant),
            regles=("FAC-DOC-011",),
        )

    def test_le_resultat_fiscal_part_du_comptable_et_applique_les_deux_sens(
        self, parametres
    ):
        passage = etablir_le_passage(
            Decimal(5000000),
            [self._conséquence()],
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=False,
        )
        assert passage.total_reintegrations == Decimal(120000)
        assert passage.total_deductions == Decimal(0)
        assert passage.resultat_fiscal == Decimal(5120000)

    def test_l_abattement_porte_sur_le_resultat_apres_reintegrations(self, parametres):
        """L'appliquer avant réduirait la base sur laquelle les réintégrations
        viennent ensuite s'ajouter : l'avantage serait à la fois plus faible et
        faux."""
        passage = etablir_le_passage(
            Decimal(5000000),
            [self._conséquence()],
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=True,
        )
        abattement = next(
            ligne for ligne in passage.lignes if ligne.nature is NaturePassage.DEDUCTION
        )
        # 50 % de (5 000 000 + 120 000)
        assert abattement.montant == Decimal(2560000)
        assert passage.resultat_fiscal == Decimal(2560000)

    def test_pas_d_abattement_sans_adhesion(self, parametres):
        """L'accorder à qui a adhéré après la clôture ferait un avantage
        rétroactif, et exposerait le Centre autant que l'adhérent."""
        passage = etablir_le_passage(
            Decimal(5000000), [], parametres, CLOTURE, droit_a_l_abattement_cga=False
        )
        assert passage.total_deductions == Decimal(0)

    def test_pas_d_abattement_sur_un_deficit(self, parametres):
        """L'aggraver augmenterait le report déficitaire, donc réduirait l'impôt
        des exercices suivants d'un montant auquel l'adhérent n'a pas droit.
        L'erreur est invisible l'année où elle est commise."""
        passage = etablir_le_passage(
            Decimal(-3000000), [], parametres, CLOTURE, droit_a_l_abattement_cga=True
        )
        assert passage.total_deductions == Decimal(0)
        assert passage.resultat_fiscal == Decimal(-3000000)

    def test_un_deficit_ecarte_l_abattement_et_le_dit(self, parametres):
        """⚠️ **Le droit ouvert, et pas d'abattement : l'écart se nomme.**

        Le motif du droit dit « adhésion sur tout l'exercice, chiffre d'affaires sous
        le seuil ». Sans une phrase pour l'absence de la ligne, le réviseur lit un
        oubli, et il le « corrige » en ajoutant l'abattement sur un déficit.
        """
        passage = etablir_le_passage(
            Decimal(-3000000), [], parametres, CLOTURE, droit_a_l_abattement_cga=True
        )
        assert passage.abattement_cga_ecarte is not None
        assert "déficitaire" in passage.abattement_cga_ecarte

    def test_rien_n_est_dit_quand_l_abattement_figure_ou_que_le_droit_est_ferme(
        self, parametres
    ):
        """La contre-épreuve : un motif écrit dans tous les cas ne dirait rien. Un
        droit fermé s'explique par son propre motif, pas par celui-ci."""
        figure = etablir_le_passage(
            Decimal(5000000), [], parametres, CLOTURE, droit_a_l_abattement_cga=True
        )
        assert [ligne.code for ligne in figure.lignes] == ["ABATT_CGA"]
        assert figure.abattement_cga_ecarte is None
        ferme = etablir_le_passage(
            Decimal(-3000000), [], parametres, CLOTURE, droit_a_l_abattement_cga=False
        )
        assert ferme.abattement_cga_ecarte is None

    def test_un_taux_absent_du_referentiel_ecarte_l_abattement_et_le_dit(self):
        """⚠️ **Ce cas était muet.** Le taux introuvable, et la liasse sans abattement
        ni explication. S'abstenir est juste, un taux supposé serait une valeur légale
        inventée ; mais l'abstention doit se lire comme telle."""
        reels = DepotParametresYaml(
            configuration().dossier_referentiel / "parametres.yaml"
        ).charger()
        sans_taux = ServiceParametres([p for p in reels if p.code != "ABATTEMENT_CGA_BENEFICE"])
        assert len(reels) - 1 == len(sans_taux.codes), "le cas retire bien le taux"
        passage = etablir_le_passage(
            Decimal(5000000), [], sans_taux, CLOTURE, droit_a_l_abattement_cga=True
        )
        assert passage.total_deductions == Decimal(0)
        assert passage.abattement_cga_ecarte is not None
        assert "aucun taux d'abattement" in passage.abattement_cga_ecarte

    def test_des_reintegrations_peuvent_rendre_beneficiaire_un_deficit(self, parametres):
        """Et l'abattement s'applique alors — sur le résultat après passage, qui
        est le seul qui compte."""
        passage = etablir_le_passage(
            Decimal(-100000),
            [self._conséquence("500000")],
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=True,
        )
        assert passage.total_deductions == Decimal(200000)  # 50 % de 400 000
        assert passage.resultat_fiscal == Decimal(200000)

    def test_chaque_ligne_porte_son_origine(self, parametres):
        """« D'où sort ce montant ? » doit se répondre sans rouvrir le dossier."""
        passage = etablir_le_passage(
            Decimal(5000000),
            [self._conséquence()],
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=True,
        )
        assert all(ligne.origine for ligne in passage.lignes)
        reintegration = next(
            ligne
            for ligne in passage.lignes
            if ligne.nature is NaturePassage.REINTEGRATION
        )
        assert "F-2026-0418" in reintegration.origine
        assert "FAC-DOC-011" in reintegration.origine

    def test_l_abattement_signale_reposer_sur_une_valeur_non_validee(
        self, parametres_non_arretes
    ):
        passage = etablir_le_passage(
            Decimal(5000000),
            [],
            parametres_non_arretes,
            CLOTURE,
            droit_a_l_abattement_cga=True,
        )
        assert passage.repose_sur_des_valeurs_non_validees

    def test_le_libelle_de_la_reintegration_reprend_le_motif_de_la_regle(
        self, parametres
    ):
        """Le retrouver mot pour mot montre à l'adhérent que le contrôle de
        janvier et l'impôt de mars sont la même chose."""
        passage = etablir_le_passage(
            Decimal(5000000),
            [self._conséquence()],
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=False,
        )
        assert passage.lignes[0].libelle.startswith("Charge non déductible")


# ══ La chaîne complète ════════════════════════════════════════════════════════
class TestChaineComplete:
    def test_de_la_balance_au_resultat_fiscal(self, parametres):
        """LE TEST QUI VÉRIFIE LA PROMESSE DU PRODUIT.

        « Une conséquence fiscale chiffrée qui se propage jusqu'à la liasse
        annuelle. » Ce test part d'une balance et d'une écriture portant un
        constat de conformité, et arrive à un résultat fiscal. Si ce test tombe,
        la phrase de la fiche de présentation devient creuse.
        """
        etats = assembler_les_etats(_balance_equilibree(), SystemeDsf.NORMAL)
        assert etats.coherent

        refus = AttributFiscal(
            charge_deductible=False,
            motif_non_deductibilite="Désignation trop imprécise",
            code_regle_origine="FAC-DOC-011",
        )
        ecriture = _ecriture(
            piece="F-2026-0418",
            lignes=[
                _ligne("628", "240000", Sens.DEBIT, refus),
                _ligne("401", "240000", Sens.CREDIT),
            ],
        )
        moisson = moissonner([ecriture])
        passage = etablir_le_passage(
            etats.resultat_comptable,
            list(moisson.a_reintegrer),
            parametres,
            CLOTURE,
            droit_a_l_abattement_cga=True,
        )

        assert passage.resultat_comptable == Decimal(5760000)
        assert passage.total_reintegrations == Decimal(240000)
        # 50 % de 6 000 000
        assert passage.total_deductions == Decimal(3000000)
        assert passage.resultat_fiscal == Decimal(3000000)
