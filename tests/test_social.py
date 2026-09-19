"""Contexte G · Social et paie.

Ce que ces tests protègent, dans l'ordre où l'erreur coûte le plus cher :

1. **Le plafond CNPS et son asymétrie.** Pensions et prestations familiales sont
   plafonnées, les accidents du travail ne le sont pas. Employer une seule
   assiette est l'erreur la plus fréquente du calcul, et elle reste **invisible
   tant qu'aucun salarié ne dépasse le plafond** — c'est-à-dire jusqu'au jour où
   le cabinet gagne un client qui paie ses cadres.
2. **L'IRPP annualisé et progressif.** Deux erreurs classiques : appliquer un
   barème annuel à un salaire mensuel, et appliquer le taux de la tranche
   atteinte à l'assiette entière. La première divise l'impôt par dix, la seconde
   fait baisser le net d'un salarié augmenté.
3. **Les avantages en nature dans l'assiette et hors du net.** Les laisser dans
   le net paierait le logement deux fois.
4. **Le refus de calculer** quand un taux manque. Un bulletin amputé d'une ligne
   sort un net trop élevé, et l'écart se découvre au contrôle CNPS sur trois ans.

Le référentiel réel est employé, jamais un jeu de taux inventé : ces tests
vérifient la mécanique **et** que les valeurs du dépôt la traversent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.referentiel.api import (
    DepotBaremesYaml,
    DepotParametresYaml,
    ServiceBaremes,
    ServiceParametres,
    TrancheBareme,
    VersionBareme,
)
from app.contextes.referentiel.api import Fondement as FondementRef
from app.contextes.social.api import (
    CONTRATS_DEMO,
    RATTACHEMENTS_DEMO,
    SALARIES_DEMO,
    AvantageNature,
    Contrat,
    DepotContratsMemoire,
    DepotSalariesMemoire,
    GroupeRisque,
    NatureAvantage,
    ParametrePaieAbsent,
    Periode,
    SalarieIntrouvable,
    TypeContrat,
    calculer_bulletin,
    etablir_la_declaration,
    evaluer_les_avantages,
)
from app.infrastructure.config import configuration

JUILLET = Periode(annee=2026, mois=7)
DOSSIER = "M065544332211L"


@pytest.fixture(scope="module")
def parametres() -> ServiceParametres:
    return ServiceParametres.depuis_depot(
        DepotParametresYaml(configuration().dossier_referentiel / "parametres.yaml")
    )


@pytest.fixture(scope="module")
def baremes() -> ServiceBaremes:
    return ServiceBaremes.depuis_depot(
        DepotBaremesYaml(configuration().dossier_referentiel / "baremes.yaml")
    )


def _contrat(
    base: str = "400000",
    primes: str = "0",
    avantages: tuple = (),
    debut: date = date(2020, 1, 1),
    fin: date | None = None,
) -> Contrat:
    return Contrat(
        salarie="SAL-TEST",
        type_contrat=TypeContrat.CDI,
        debut=debut,
        fin=fin,
        salaire_base=Decimal(base),
        primes=Decimal(primes),
        avantages=avantages,
    )


def _ligne(bulletin, code: str):
    return next(ligne for ligne in bulletin.lignes if ligne.code == code)


# ══ Le barème progressif ══════════════════════════════════════════════════════
class TestBaremeProgressif:
    def _version(self, tranches: list[TrancheBareme]) -> VersionBareme:
        return VersionBareme(
            tranches=tranches,
            applicable_du=date(2020, 1, 1),
            fondement=FondementRef(texte="test", source="test"),
        )

    def test_le_bareme_taxe_tranche_par_tranche_et_non_au_taux_atteint(self):
        """L'erreur qui ferait baisser le net d'un salarié augmenté.

        2 500 000 doit produire 200 000 (10 % sur les deux premiers millions) plus
        75 000 (15 % sur les cinq cent mille suivants) — soit 275 000. Le taux de
        la tranche atteinte appliqué au tout donnerait 375 000.
        """
        version = self._version(
            [
                TrancheBareme(plancher=Decimal(0), plafond=Decimal(2000000), taux=Decimal(10)),
                TrancheBareme(
                    plancher=Decimal(2000000), plafond=Decimal(3000000), taux=Decimal(15)
                ),
                TrancheBareme(plancher=Decimal(3000000), plafond=None, taux=Decimal(25)),
            ]
        )
        assert version.appliquer(Decimal(2500000)) == Decimal(275000)

    def test_une_assiette_nulle_ou_negative_ne_produit_aucun_impot(self):
        version = self._version(
            [TrancheBareme(plancher=Decimal(0), plafond=None, taux=Decimal(10))]
        )
        assert version.appliquer(Decimal(0)) == Decimal(0)
        assert version.appliquer(Decimal(-1)) == Decimal(0)

    def test_un_trou_entre_deux_tranches_est_refuse(self):
        """Un revenu tombant dans le trou n'aurait aucun taux."""
        with pytest.raises(ValueError, match="non contiguës"):
            self._version(
                [
                    TrancheBareme(
                        plancher=Decimal(0), plafond=Decimal(1000000), taux=Decimal(10)
                    ),
                    TrancheBareme(plancher=Decimal(2000000), plafond=None, taux=Decimal(20)),
                ]
            )

    def test_une_derniere_tranche_fermee_est_refusee(self):
        """Les revenus au-delà n'auraient aucun taux."""
        with pytest.raises(ValueError, match="dernière tranche est fermée"):
            self._version(
                [
                    TrancheBareme(
                        plancher=Decimal(0), plafond=Decimal(1000000), taux=Decimal(10)
                    )
                ]
            )

    def test_un_bareme_qui_ne_part_pas_de_zero_est_refuse(self):
        with pytest.raises(ValueError, match="revenus faibles"):
            self._version(
                [TrancheBareme(plancher=Decimal(100), plafond=None, taux=Decimal(10))]
            )

    def test_le_bareme_irpp_du_referentiel_se_lit_et_calcule(self, baremes):
        resolu = baremes.resoudre("IRPP_SALAIRES", date(2026, 7, 31))
        # 4 330 000 : 2M à 10 %, 1M à 15 %, 1,33M à 25 % → 682 500
        assert resolu.appliquer(Decimal(4330000)) == Decimal(682500)


# ══ Le plafond CNPS — le piège principal ══════════════════════════════════════
class TestPlafondCnps:
    def test_les_branches_plafonnees_et_celles_qui_ne_le_sont_pas(
        self, parametres, baremes
    ):
        """LE TEST LE PLUS IMPORTANT DU CONTEXTE.

        Un cadre à 1 200 000 dépasse le plafond de 750 000. Les pensions et les
        prestations familiales s'arrêtent au plafond ; les accidents du travail,
        le CFC et le FNE portent sur le salaire réel.

        Une seule assiette pour toutes les branches passerait tous les autres
        tests de ce fichier : aucun salarié du jeu ordinaire ne dépasse le
        plafond. C'est exactement pourquoi ce cas existe.
        """
        bulletin = calculer_bulletin(
            _contrat("1200000"), DOSSIER, JUILLET, parametres, baremes
        )
        plafond = Decimal(750000)
        assert bulletin.brut_taxable == Decimal(1200000)

        for code in ("CNPS_PVID_S", "CNPS_PVID_P", "CNPS_PF"):
            assert _ligne(bulletin, code).assiette == plafond, (
                f"{code} doit être plafonnée"
            )
        for code in ("CNPS_AT", "CFC_S", "CFC_P", "FNE"):
            assert _ligne(bulletin, code).assiette == bulletin.brut_taxable, (
                f"{code} porte sur le salaire réel, jamais sur le plafond"
            )

    def test_sous_le_plafond_toutes_les_assiettes_coincident(self, parametres, baremes):
        """Le cas ordinaire — et celui qui masque l'erreur si on ne teste que lui."""
        bulletin = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres, baremes
        )
        assiettes = {
            ligne.assiette
            for ligne in bulletin.lignes
            if ligne.code.startswith(("CNPS_", "CFC_", "FNE"))
        }
        assert assiettes == {bulletin.brut_taxable}

    def test_le_groupe_de_risque_change_la_cotisation_accidents(self, parametres, baremes):
        """Le groupe est notifié par la CNPS : le saisir de travers coûte le triple."""
        montants = {}
        for groupe in (GroupeRisque.A, GroupeRisque.B, GroupeRisque.C):
            bulletin = calculer_bulletin(
                _contrat("400000"), DOSSIER, JUILLET, parametres, baremes,
                groupe_risque=groupe,
            )
            montants[groupe] = _ligne(bulletin, "CNPS_AT").montant
        assert montants[GroupeRisque.A] < montants[GroupeRisque.B] < montants[GroupeRisque.C]
        assert montants[GroupeRisque.C] > montants[GroupeRisque.A] * Decimal(2)


# ══ Les avantages en nature ═══════════════════════════════════════════════════
class TestAvantagesNature:
    def test_le_forfait_porte_sur_le_brut_en_especes_et_non_sur_lui_meme(
        self, parametres
    ):
        """Sinon le calcul serait circulaire : le logement à 15 % du brut, brut
        qui inclut le logement, qui inclut…"""
        contrat = _contrat(
            "400000", "100000", (AvantageNature(nature=NatureAvantage.LOGEMENT),)
        )
        montant, _ = evaluer_les_avantages(contrat, parametres, JUILLET)
        assert montant == Decimal(75000), "15 % de 500 000, pas de 575 000"

    def test_les_avantages_entrent_dans_l_assiette_et_sortent_du_net(
        self, parametres, baremes
    ):
        """Les laisser dans le net paierait le logement deux fois : une fois en
        clés, une fois en espèces."""
        sans = calculer_bulletin(_contrat("400000"), DOSSIER, JUILLET, parametres, baremes)
        avec = calculer_bulletin(
            _contrat("400000", avantages=(AvantageNature(nature=NatureAvantage.LOGEMENT),)),
            DOSSIER, JUILLET, parametres, baremes,
        )
        assert avec.brut_taxable > sans.brut_taxable, "l'avantage est imposable"
        assert avec.net_a_payer < sans.net_a_payer, (
            "l'avantage ne se verse pas : il augmente les retenues sans augmenter le versé"
        )
        assert avec.salaire_base + avec.primes - avec.retenues_salariales == avec.net_a_payer

    def test_seul_le_domestique_se_compte(self):
        with pytest.raises(ValueError, match="ne se compte pas"):
            AvantageNature(nature=NatureAvantage.LOGEMENT, nombre=2)
        double = AvantageNature(nature=NatureAvantage.DOMESTIQUE, nombre=2)
        assert double.nombre == 2

    def test_deux_domestiques_valent_deux_fois_le_forfait(self, parametres):
        un = evaluer_les_avantages(
            _contrat("400000", avantages=(AvantageNature(nature=NatureAvantage.DOMESTIQUE),)),
            parametres, JUILLET,
        )[0]
        deux = evaluer_les_avantages(
            _contrat(
                "400000",
                avantages=(AvantageNature(nature=NatureAvantage.DOMESTIQUE, nombre=2),),
            ),
            parametres, JUILLET,
        )[0]
        assert deux == un * 2


# ══ L'IRPP ════════════════════════════════════════════════════════════════════
class TestIrpp:
    def test_l_impot_est_annualise_avant_le_bareme(self, parametres, baremes):
        """Appliquer le barème annuel au salaire mensuel placerait tout revenu
        dans la première tranche : un cadre paierait le taux d'un manœuvre.

        Vérifié par le calcul complet : 575 000 mensuel → 6 900 000 annuel,
        −30 % de frais = 4 830 000, −500 000 d'abattement = 4 330 000, barème
        = 682 500, +10 % de CAC = 750 750, mensualisé = 62 563.
        """
        contrat = _contrat(
            "450000", "50000", (AvantageNature(nature=NatureAvantage.LOGEMENT),)
        )
        bulletin = calculer_bulletin(contrat, DOSSIER, JUILLET, parametres, baremes)
        assert bulletin.brut_taxable == Decimal(575000)
        assert _ligne(bulletin, "IRPP").montant == Decimal(62563)

    def test_un_revenu_sous_les_abattements_n_est_pas_imposable(self, parametres, baremes):
        """Un impôt négatif serait un crédit que le droit ne prévoit pas."""
        bulletin = calculer_bulletin(
            _contrat("50000"), DOSSIER, JUILLET, parametres, baremes
        )
        assert _ligne(bulletin, "IRPP").montant == Decimal(0)

    def test_l_irpp_ne_porte_aucun_taux_unique(self, parametres, baremes):
        """Un barème progressif ne se résume pas à un taux : la ligne le dit en
        laissant `taux` à None plutôt qu'en affichant un taux moyen trompeur."""
        bulletin = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres, baremes
        )
        assert _ligne(bulletin, "IRPP").taux is None

    def test_l_impot_croit_avec_le_revenu_sans_jamais_faire_baisser_le_net(
        self, parametres, baremes
    ):
        """La propriété qui rend un barème progressif défendable devant un salarié."""
        nets = []
        for base in ("200000", "400000", "800000", "1500000", "3000000"):
            bulletin = calculer_bulletin(
                _contrat(base), DOSSIER, JUILLET, parametres, baremes
            )
            nets.append(bulletin.net_a_payer)
        assert nets == sorted(nets), "un salarié augmenté ne doit jamais toucher moins"


# ══ Le refus de calculer ══════════════════════════════════════════════════════
class TestRefusDeCalculer:
    def test_un_parametre_absent_leve_plutot_que_d_amputer_le_bulletin(self, baremes):
        """Un bulletin amputé d'une ligne sort un net trop élevé, et l'écart se
        découvre au contrôle CNPS — sur toute la masse salariale et sur trois ans.

        C'est la différence avec le diagnostic de création, qui tolère un
        paramètre absent : là on ne peut plus *vérifier*, ici on ne peut plus
        *calculer*.
        """
        vide = ServiceParametres([])
        with pytest.raises(ParametrePaieAbsent, match="ne peut pas être calculé"):
            calculer_bulletin(_contrat(), DOSSIER, JUILLET, vide, baremes)

    def test_le_message_nomme_le_parametre_et_la_periode(self, baremes):
        vide = ServiceParametres([])
        with pytest.raises(ParametrePaieAbsent) as refus:
            calculer_bulletin(_contrat(), DOSSIER, JUILLET, vide, baremes)
        assert "2026-07-31" in str(refus.value)

    def test_les_valeurs_non_validees_sont_signalees_sur_le_bulletin(
        self, parametres_non_arretes, baremes
    ):
        """Un employeur qui remet une fiche de paie engage sa responsabilité : il
        doit savoir sur quoi elle repose."""
        bulletin = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres_non_arretes, baremes
        )
        assert bulletin.repose_sur_des_valeurs_non_validees
        assert all(ligne.non_valide for ligne in bulletin.lignes)

    def test_les_cotisations_validees_ne_portent_plus_de_reserve(self, parametres, baremes):
        """Les six taux CNPS, le CFC, le FNE et les abattements IRPP ont été
        confrontés aux textes le 18 août 2026.

        Le barème progressif de l'IRPP, lui, reste A_VALIDER : le bulletin porte
        donc encore une réserve globale, mais plus sur ses lignes de cotisation.
        C'est la granularité qui fait la valeur du signalement — « tout est
        douteux » ne dit rien à personne."""
        bulletin = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres, baremes
        )
        cotisations = [
            ligne for ligne in bulletin.lignes if "CNPS" in ligne.libelle.upper()
        ]
        assert cotisations, "le bulletin doit porter des lignes de cotisation CNPS"
        assert not any(ligne.non_valide for ligne in cotisations)


# ══ La traçabilité ════════════════════════════════════════════════════════════
class TestTracabilite:
    def test_chaque_ligne_porte_le_code_du_parametre_employe(self, parametres, baremes):
        """Un bulletin qui n'afficherait que des montants serait indéfendable :
        ni le salarié qui conteste, ni l'inspecteur ne peuvent rien faire d'un
        nombre isolé."""
        bulletin = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres, baremes
        )
        assert all(ligne.fondement_code for ligne in bulletin.lignes)

    def test_la_paie_d_un_mois_passe_emploie_les_taux_de_ce_mois(
        self, parametres, baremes
    ):
        """La lecture se fait au dernier jour de la période, jamais au jour du
        calcul : un contrôle en 2029 sur une paie de 2026 doit retomber juste."""
        janvier = calculer_bulletin(
            _contrat("400000"), DOSSIER, Periode(annee=2026, mois=1), parametres, baremes
        )
        juillet = calculer_bulletin(
            _contrat("400000"), DOSSIER, JUILLET, parametres, baremes
        )
        # Les taux n'ont pas changé entre janvier et juillet 2026 : les deux
        # bulletins coïncident. Le jour où une version datée les séparera, ce
        # test le montrera en échouant — et c'est ce qu'on lui demande.
        assert janvier.retenues_salariales == juillet.retenues_salariales


# ══ Le DIPE ═══════════════════════════════════════════════════════════════════
class TestDeclaration:
    def _contrats_agro(self) -> list[Contrat]:
        return [c for c in CONTRATS_DEMO if RATTACHEMENTS_DEMO[c.salarie] == DOSSIER]

    def test_un_contrat_couvrant_partiellement_le_mois_est_declare(
        self, parametres, baremes
    ):
        """Un salarié embauché le 20 est déclaré au titre du mois : exiger le mois
        entier ferait disparaître les embauches, que le DIPE existe pour signaler."""
        embauche = _contrat("150000", debut=date(2026, 7, 20))
        declaration = etablir_la_declaration(
            DOSSIER, JUILLET, [embauche], parametres, baremes
        )
        assert declaration.effectif == 1

    def test_les_entrees_et_les_sorties_du_mois_sont_relevees(self, parametres, baremes):
        """La CNPS ouvre et ferme les droits sur ces mouvements. Un départ non
        déclaré laisse un salarié réputé en poste."""
        declaration = etablir_la_declaration(
            DOSSIER, JUILLET, self._contrats_agro(), parametres, baremes
        )
        sorties = [m for m in declaration.mouvements if m.sens == "SORTIE"]
        assert len(sorties) == 1
        # `fin` est exclue : le contrat finit le 2026-08-01, le dernier jour
        # travaillé est le 31 juillet.
        assert sorties[0].survenu_le == date(2026, 7, 31)

    def test_le_total_a_verser_comprend_la_retenue_salariale(self, parametres, baremes):
        """L'erreur de trésorerie classique : ne provisionner que la part patronale.

        La retenue salariale n'appartient pas à l'employeur — il la détient pour
        le compte de l'administration, et il la doit intégralement.
        """
        declaration = etablir_la_declaration(
            DOSSIER, JUILLET, self._contrats_agro(), parametres, baremes
        )
        assert declaration.total_a_verser == (
            declaration.retenues_salariales + declaration.charges_patronales
        )
        assert declaration.total_a_verser > declaration.charges_patronales

    def test_un_dossier_sans_salarie_declare_a_neant(self, parametres, baremes):
        """La CNPS attend une déclaration à zéro, pas une absence de déclaration."""
        declaration = etablir_la_declaration(DOSSIER, JUILLET, [], parametres, baremes)
        assert declaration.effectif == 0
        assert declaration.total_a_verser == Decimal(0)

    def test_l_echeance_est_le_quinze_du_mois_suivant(self, parametres, baremes):
        declaration = etablir_la_declaration(
            DOSSIER, JUILLET, self._contrats_agro(), parametres, baremes
        )
        assert declaration.a_deposer_avant == date(2026, 8, 15)

    def test_le_retard_se_juge_a_une_date_et_jamais_dans_l_absolu(
        self, parametres, baremes
    ):
        declaration = etablir_la_declaration(
            DOSSIER, JUILLET, self._contrats_agro(), parametres, baremes
        )
        assert not declaration.en_retard_au(date(2026, 8, 15))
        assert declaration.en_retard_au(date(2026, 8, 16))
        with pytest.raises(NotImplementedError):
            _ = declaration.est_en_retard

    def test_la_declaration_de_decembre_echoit_en_janvier_suivant(
        self, parametres, baremes
    ):
        """Le passage d'année, que tout calcul de période finit par rencontrer."""
        declaration = etablir_la_declaration(
            DOSSIER, Periode(annee=2026, mois=12), [], parametres, baremes
        )
        assert declaration.a_deposer_avant == date(2027, 1, 15)


# ══ Les périodes et les contrats ══════════════════════════════════════════════
class TestPeriodesEtContrats:
    def test_fevrier_bissextile(self):
        assert Periode(annee=2024, mois=2).dernier_jour == date(2024, 2, 29)
        assert Periode(annee=2026, mois=2).dernier_jour == date(2026, 2, 28)

    def test_decembre_enchaine_sur_janvier(self):
        assert Periode(annee=2026, mois=12).suivante() == Periode(annee=2027, mois=1)

    def test_un_contrat_ferme_avant_son_debut_est_refuse(self):
        with pytest.raises(ValueError, match="antérieure ou égale"):
            _contrat(debut=date(2026, 5, 1), fin=date(2026, 5, 1))

    def test_la_borne_haute_est_exclue(self):
        contrat = _contrat(debut=date(2026, 1, 1), fin=date(2026, 8, 1))
        assert contrat.couvre(date(2026, 7, 31))
        assert not contrat.couvre(date(2026, 8, 1))


# ══ Les dépôts ════════════════════════════════════════════════════════════════
class TestDepotsMemoire:
    def test_le_fichier_du_personnel_ne_se_purge_pas(self):
        """La CNPS peut réclamer un état nominatif des années après un départ."""
        depot = DepotSalariesMemoire(list(SALARIES_DEMO))
        assert len(depot.du_dossier(DOSSIER)) == 3

    def test_un_matricule_inconnu_leve(self):
        with pytest.raises(SalarieIntrouvable):
            DepotSalariesMemoire([]).lire("SAL-INEXISTANT")

    def test_le_filtre_a_une_date_emploie_l_intervalle_du_contrat(self):
        depot = DepotContratsMemoire(list(CONTRATS_DEMO))
        for matricule, entreprise in RATTACHEMENTS_DEMO.items():
            depot.rattacher(matricule, entreprise)
        # Le CDD de SAL-0003 finit le 2026-08-01 (exclu) ; celui de SAL-0005
        # commence le 2026-07-20.
        en_juillet = {c.salarie for c in depot.du_dossier(DOSSIER, a_la_date=date(2026, 7, 25))}
        en_aout = {c.salarie for c in depot.du_dossier(DOSSIER, a_la_date=date(2026, 8, 5))}
        assert "SAL-0003" in en_juillet
        assert "SAL-0003" not in en_aout

    def test_un_salarie_peut_enchainer_plusieurs_contrats(self):
        """Un CDD puis un CDI chez le même employeur est le cas courant : prendre
        le seul matricule comme clé écraserait le premier, et la paie du mois
        précédent se recalculerait au nouveau salaire."""
        depot = DepotContratsMemoire([])
        depot.enregistrer(_contrat("200000", debut=date(2025, 1, 1), fin=date(2026, 1, 1)))
        depot.enregistrer(_contrat("300000", debut=date(2026, 1, 1)))
        assert len(depot.du_salarie("SAL-TEST")) == 2
