"""Contexte B · Portefeuille — le statut daté, et ce qu'il empêche.

Ces tests protègent le troisième des huit pièges du dossier de vision : traiter
le régime fiscal comme un attribut figé.

Le dossier de démonstration est celui de SARL BATIMENT PLUS, avec son histoire
réelle : créée en 2021 au régime synthétique, adhérente au Centre depuis 2022,
reclassée au réel en 2023 pour dépassement de seuil. C'est cette histoire qui rend
les lectures datées vérifiables — un régime unique ne prouverait rien.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.contextes.portefeuille.api import (
    Adhesion,
    CentreRattachement,
    Entreprise,
    Exercice,
    FormeJuridique,
    MandatDeclaratif,
    MotifChangement,
    RegimeFiscal,
    StatutIntrouvable,
    StatutRattachement,
    StatutRegime,
    Tiers,
    TypeTiers,
    diagnostiquer_seuil,
    retour_au_synthetique_admis,
    verifier_identifiants,
)
from app.contextes.portefeuille.domaine.temporel import Periode, verifier_succession
from app.contextes.referentiel.application.service_parametres import ServiceParametres
from app.contextes.referentiel.contrats import Borne

CREATION = date(2021, 3, 15)
BASCULE = date(2023, 1, 1)


def _exercices(clos_jusqu_a: int = 2025) -> list[Exercice]:
    """Le premier exercice est court — créée en mars —, les suivants civils."""
    liste = [
        Exercice(
            libelle="2021", ouverture=CREATION, cloture=date(2021, 12, 31), clos=True
        )
    ]
    for annee in range(2022, 2027):
        liste.append(
            Exercice(
                libelle=str(annee),
                ouverture=date(annee, 1, 1),
                cloture=date(annee, 12, 31),
                clos=annee <= clos_jusqu_a,
            )
        )
    return liste


def _batiment_plus(**extra) -> Entreprise:
    base: dict = {
        "niu": "M081234567890P",
        "denomination": "SARL BATIMENT PLUS",
        "forme_juridique": FormeJuridique.SARL,
        "date_creation": CREATION,
        "rccm": "RC/DLA/2021/B/0977",
        "capital": D(1_000_000),
        "activite": "BTP",
        "siege": "Douala Bonabéri",
        "regimes": [
            StatutRegime(
                debut=CREATION,
                fin=BASCULE,
                regime=RegimeFiscal.IGS,
                motif=MotifChangement.CREATION,
            ),
            StatutRegime(
                debut=BASCULE,
                regime=RegimeFiscal.REEL,
                motif=MotifChangement.DEPASSEMENT_SEUIL,
            ),
        ],
        "rattachements": [
            StatutRattachement(
                debut=CREATION,
                fin=BASCULE,
                centre=CentreRattachement.CDI,
                motif=MotifChangement.CREATION,
            ),
            StatutRattachement(
                debut=BASCULE,
                centre=CentreRattachement.CIME,
                motif=MotifChangement.RECLASSEMENT_AUTOMATIQUE,
            ),
        ],
        "adhesions": [
            Adhesion(
                debut=date(2022, 1, 1), motif=MotifChangement.ADHESION, numero="ADH-0042"
            )
        ],
        "exercices": _exercices(),
    }
    return Entreprise(**(base | extra))


# ── Le patron temporel ───────────────────────────────────────────────────────────


class TestPeriode:
    def test_la_borne_haute_est_exclue(self):
        # Une entreprise qui change de régime le 1er janvier relève encore de
        # l'ancien le 31 décembre.
        periode = Periode(
            debut=date(2021, 1, 1), fin=date(2023, 1, 1), motif=MotifChangement.CREATION
        )
        assert periode.couvre(date(2022, 12, 31))
        assert not periode.couvre(BASCULE)

    def test_une_periode_de_duree_nulle_est_refusee(self):
        with pytest.raises(ValidationError, match="jamais existé"):
            Periode(
                debut=BASCULE, fin=BASCULE, motif=MotifChangement.CORRECTION
            )


class TestSuccession:
    def _p(self, debut: date, fin: date | None = None) -> Periode:
        return Periode(debut=debut, fin=fin, motif=MotifChangement.CORRECTION)

    def test_un_chevauchement_est_refuse(self):
        with pytest.raises(ValueError, match="[Cc]hevauchement"):
            verifier_succession(
                [self._p(date(2021, 1, 1), date(2023, 6, 1)), self._p(BASCULE)],
                quoi="test",
                continue_=False,
            )

    def test_deux_periodes_ouvertes_sont_refusees(self):
        with pytest.raises(ValueError, match="ouvertes en même temps"):
            verifier_succession(
                [self._p(date(2021, 1, 1)), self._p(BASCULE)], quoi="test", continue_=False
            )

    def test_un_trou_est_refuse_sur_un_statut_continu(self):
        # Une entreprise relève d'un régime à chaque instant de son existence.
        with pytest.raises(ValueError, match="[Tt]rou"):
            verifier_succession(
                [self._p(date(2021, 1, 1), date(2022, 1, 1)), self._p(BASCULE)],
                quoi="régimes",
                continue_=True,
            )

    def test_un_trou_est_admis_sur_un_statut_discontinu(self):
        # Un adhérent peut partir et revenir. Boucher le trou lui accorderait
        # rétroactivement des avantages qu'il n'avait pas.
        periodes = verifier_succession(
            [self._p(date(2021, 1, 1), date(2022, 1, 1)), self._p(BASCULE)],
            quoi="adhésions",
            continue_=False,
        )
        assert len(periodes) == 2


# ── L'exercice ───────────────────────────────────────────────────────────────────


class TestExercice:
    def test_un_premier_exercice_peut_etre_court(self):
        exercice = Exercice(
            libelle="2021", ouverture=CREATION, cloture=date(2021, 12, 31)
        )
        assert exercice.duree.days == 292
        assert not exercice.est_long
        assert not exercice.est_annee_civile

    def test_un_premier_exercice_peut_etre_long(self):
        # Créée en octobre, elle clôture au 31 décembre de l'année suivante.
        exercice = Exercice(
            libelle="2025-2026",
            ouverture=date(2025, 10, 1),
            cloture=date(2026, 12, 31),
        )
        assert exercice.est_long
        assert exercice.duree.days == 457
        # La conversion en mois est approchée — un mois moyen vaut 30,4375 jours,
        # soit 365,25 / 12. C'est assez précis pour une proratisation et cela
        # évite d'inventer une arithmétique calendaire dont personne n'a besoin.
        assert exercice.duree_mois == D("15.01")

    def test_au_dela_de_deux_ans_c_est_une_erreur_de_saisie(self):
        with pytest.raises(ValidationError, match="erreur de saisie"):
            Exercice(
                libelle="absurde",
                ouverture=date(2020, 1, 1),
                cloture=date(2024, 12, 31),
            )

    def test_le_prorata_se_calcule_sur_la_duree_reelle(self):
        # Sur un exercice de quinze mois, rapporter à 365 jours fausserait de 25 %.
        long = Exercice(
            libelle="long", ouverture=date(2025, 10, 1), cloture=date(2026, 12, 31)
        )
        civil = Exercice(
            libelle="civil", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31)
        )
        assert long.prorata(90) < civil.prorata(90)

    def test_les_exercices_se_suivent_sans_trou(self):
        with pytest.raises(ValidationError, match="sans trou"):
            _batiment_plus(
                exercices=[
                    Exercice(
                        libelle="2021",
                        ouverture=CREATION,
                        cloture=date(2021, 12, 31),
                        clos=True,
                    ),
                    Exercice(
                        libelle="2023",
                        ouverture=date(2023, 1, 1),
                        cloture=date(2023, 12, 31),
                    ),
                ]
            )


# ── Les lectures datées ──────────────────────────────────────────────────────────


class TestLectureDatee:
    def test_le_regime_se_lit_a_une_date(self):
        entreprise = _batiment_plus()
        assert entreprise.regime_au(date(2022, 6, 30)) is RegimeFiscal.IGS
        assert entreprise.regime_au(date(2024, 6, 30)) is RegimeFiscal.REEL

    def test_une_facture_de_2022_se_controle_avec_le_regime_de_2022(self):
        # Le cœur du contexte. Sans lui, le contrôle d'une pièce ancienne serait
        # faux, et faux en silence.
        entreprise = _batiment_plus()
        assert not entreprise.assujettie_tva_au(date(2022, 11, 30))
        assert entreprise.assujettie_tva_au(date(2023, 1, 1))

    def test_avant_la_creation_le_statut_est_introuvable(self):
        # Jamais de valeur par défaut : supposer le réel produirait un contrôle faux.
        with pytest.raises(StatutIntrouvable, match="aucun régime connu"):
            _batiment_plus().regime_au(date(2020, 1, 1))

    def test_le_rattachement_suit_le_reclassement(self):
        entreprise = _batiment_plus()
        assert entreprise.rattachement_au(date(2022, 1, 1)) is CentreRattachement.CDI
        assert entreprise.rattachement_au(date(2024, 1, 1)) is CentreRattachement.CIME

    def test_l_adhesion_a_une_date_d_effet(self):
        # Elle est fiscalement porteuse : elle ouvre l'abattement.
        entreprise = _batiment_plus()
        assert not entreprise.est_adherente_au(date(2021, 12, 31))
        assert entreprise.est_adherente_au(date(2022, 1, 1))
        assert entreprise.adhesion_au(date(2026, 1, 1)).numero == "ADH-0042"

    def test_l_absence_d_adhesion_est_une_reponse_pas_une_erreur(self):
        entreprise = _batiment_plus(adhesions=[])
        assert entreprise.adhesion_au(date(2026, 1, 1)) is None
        assert not entreprise.est_adherente_au(date(2026, 1, 1))

    def test_l_exercice_couvrant_une_date(self):
        entreprise = _batiment_plus()
        assert entreprise.exercice_couvrant(date(2021, 6, 1)).libelle == "2021"
        assert entreprise.exercice_couvrant(date(2026, 7, 15)).libelle == "2026"

    def test_hors_de_tout_exercice_on_leve(self):
        with pytest.raises(StatutIntrouvable, match="aucun exercice"):
            _batiment_plus().exercice_couvrant(date(2030, 1, 1))


class TestMandatDeclaratif:
    def test_le_centre_n_a_qualite_que_dans_le_perimetre_du_mandat(self):
        # Déposer hors mandat n'est pas une négligence de procédure : c'est agir
        # sans qualité.
        entreprise = _batiment_plus(
            mandats=[
                MandatDeclaratif(
                    debut=date(2022, 1, 1),
                    motif=MotifChangement.ADHESION,
                    obligations=["TVA", "DSF"],
                    signe_le=date(2021, 12, 20),
                    signe_par="Paule Diane HIMSTA",
                )
            ]
        )
        assert entreprise.mandatee_pour("TVA", date(2026, 1, 1))
        assert not entreprise.mandatee_pour("DIPE", date(2026, 1, 1))
        assert not entreprise.mandatee_pour("TVA", date(2021, 6, 1))


class TestInvariantsDuDossier:
    def test_un_statut_ne_commence_pas_avant_la_creation(self):
        with pytest.raises(ValidationError, match="avant la création"):
            _batiment_plus(
                adhesions=[
                    Adhesion(debut=date(2019, 1, 1), motif=MotifChangement.ADHESION)
                ]
            )

    def test_le_regime_doit_etre_continu(self):
        with pytest.raises(ValidationError, match="[Tt]rou"):
            _batiment_plus(
                regimes=[
                    StatutRegime(
                        debut=CREATION,
                        fin=date(2022, 1, 1),
                        regime=RegimeFiscal.IGS,
                        motif=MotifChangement.CREATION,
                    ),
                    StatutRegime(
                        debut=BASCULE,
                        regime=RegimeFiscal.REEL,
                        motif=MotifChangement.DEPASSEMENT_SEUIL,
                    ),
                ]
            )


# ── Le franchissement de seuil ───────────────────────────────────────────────────


class TestDiagnosticSeuil:
    SEUIL = D(50_000_000)

    def _au_synthetique(self) -> Entreprise:
        return _batiment_plus(
            regimes=[
                StatutRegime(
                    debut=CREATION, regime=RegimeFiscal.IGS, motif=MotifChangement.CREATION
                )
            ],
            rattachements=[
                StatutRattachement(
                    debut=CREATION,
                    centre=CentreRattachement.CDI,
                    motif=MotifChangement.CREATION,
                )
            ],
        )

    def test_l_alerte_tombe_avant_le_franchissement(self):
        # C'est là qu'est la valeur : trois mois pour s'organiser, plutôt qu'un
        # avis de redressement deux ans plus tard.
        diagnostic = diagnostiquer_seuil(
            self._au_synthetique(),
            D(45_000_000),
            self.SEUIL,
            date(2026, 9, 30),
            borne=Borne.EXCLUSE,
        )
        assert not diagnostic.franchi
        assert diagnostic.alerte_anticipee
        assert diagnostic.taux_d_approche == D("0.9000")
        assert diagnostic.a_signaler

    def test_loin_du_seuil_on_ne_derange_personne(self):
        diagnostic = diagnostiquer_seuil(
            self._au_synthetique(), D(20_000_000), self.SEUIL, date(2026, 9, 30),
            borne=Borne.EXCLUSE,
        )
        assert not diagnostic.a_signaler

    def test_le_franchissement_impose_un_reclassement(self):
        diagnostic = diagnostiquer_seuil(
            self._au_synthetique(), D(52_000_000), self.SEUIL, date(2026, 9, 30),
            borne=Borne.EXCLUSE,
        )
        assert diagnostic.franchi
        assert diagnostic.reclassement_requis

    def test_deja_au_reel_le_franchissement_n_impose_rien(self):
        diagnostic = diagnostiquer_seuil(
            _batiment_plus(), D(240_000_000), self.SEUIL, date(2026, 9, 30), borne=Borne.EXCLUSE
        )
        assert diagnostic.franchi
        assert not diagnostic.reclassement_requis

    def test_le_regime_est_lu_a_la_date_du_diagnostic(self):
        # Un diagnostic porté sur 2022 raisonne avec le régime de 2022.
        entreprise = _batiment_plus()
        ancien = diagnostiquer_seuil(entreprise, D(52_000_000), self.SEUIL, date(2022, 6, 1),
        borne=Borne.EXCLUSE,)
        assert ancien.regime_actuel is RegimeFiscal.IGS
        assert ancien.reclassement_requis

    def test_a_la_valeur_meme_d_un_seuil_superieur_a_rien_n_est_franchi(self):
        """⚠️ **Le défaut que ce cas existe pour empêcher de revenir.**

        Le CGI dit « supérieur à 50 000 000 ». Le diagnostic comparait avec `>=`,
        et déclarait en franchissement une entreprise à 50 000 000 exactement, en
        l'invitant à un reclassement au réel que la loi ne lui impose pas. Aucun
        cas ne mesurait la borne ; la borne n'était écrite nulle part.
        """
        diagnostic = diagnostiquer_seuil(
            self._au_synthetique(),
            self.SEUIL,
            self.SEUIL,
            date(2026, 9, 30),
            borne=Borne.EXCLUSE,
        )
        assert not diagnostic.franchi
        assert not diagnostic.reclassement_requis
        # Et le franc suivant franchit.
        assert diagnostiquer_seuil(
            self._au_synthetique(),
            self.SEUIL + 1,
            self.SEUIL,
            date(2026, 9, 30),
            borne=Borne.EXCLUSE,
        ).franchi

    def test_a_la_valeur_meme_d_un_seuil_des_tout_est_franchi(self):
        """La contre-épreuve : « dès 10 M », la valeur même oblige. Sans ce cas, une
        comparaison stricte partout passerait le précédent."""
        assert diagnostiquer_seuil(
            self._au_synthetique(),
            self.SEUIL,
            self.SEUIL,
            date(2026, 9, 30),
            borne=Borne.INCLUSE,
        ).franchi

    def test_un_seuil_nul_est_refuse(self):
        with pytest.raises(ValueError, match="seuil nul"):
            diagnostiquer_seuil(
                self._au_synthetique(), D(1), D(0), date(2026, 9, 30), borne=Borne.EXCLUSE
            )


class TestPeriodeProbatoire:
    def test_apres_assez_d_exercices_le_retour_est_admis(self):
        # Reclassée le 1er janvier 2023 ; 2023, 2024 et 2025 sont clos.
        assert retour_au_synthetique_admis(_batiment_plus(), date(2026, 7, 15), 2)

    def test_avant_le_terme_il_ne_l_est_pas(self):
        assert not retour_au_synthetique_admis(_batiment_plus(), date(2026, 7, 15), 5)

    def test_un_exercice_a_cheval_sur_la_bascule_ne_compte_pas(self):
        # Le retenir abrégerait la période probatoire d'un exercice complet.
        entreprise = _batiment_plus()
        assert entreprise.exercices_clos_depuis(BASCULE) == 3  # 2023, 2024, 2025

    def test_deja_au_synthetique_la_question_ne_se_pose_pas(self):
        entreprise = _batiment_plus(
            regimes=[
                StatutRegime(
                    debut=CREATION, regime=RegimeFiscal.IGS, motif=MotifChangement.CREATION
                )
            ],
            rattachements=[
                StatutRattachement(
                    debut=CREATION,
                    centre=CentreRattachement.CDI,
                    motif=MotifChangement.CREATION,
                )
            ],
        )
        assert not retour_au_synthetique_admis(entreprise, date(2026, 7, 15), 2)


# ── Les identifiants, contrôlés contre le référentiel daté ───────────────────────


class TestValidationDesIdentifiants:
    LE = date(2026, 7, 15)

    def test_un_dossier_sain_ne_produit_aucune_anomalie(self, parametres: ServiceParametres):
        assert verifier_identifiants(_batiment_plus(), parametres, self.LE) == []

    def test_un_niu_malforme_est_bloquant(self, parametres: ServiceParametres):
        anomalies = verifier_identifiants(
            _batiment_plus(niu="12345"), parametres, self.LE
        )
        assert len(anomalies) == 1
        assert anomalies[0].champ == "niu"
        assert anomalies[0].bloquante

    def test_un_rccm_absent_est_signale_sans_bloquer(self, parametres: ServiceParametres):
        # Toutes les formes n'ont pas de RCCM.
        anomalies = verifier_identifiants(_batiment_plus(rccm=None), parametres, self.LE)
        assert [a.champ for a in anomalies] == ["rccm"]
        assert not anomalies[0].bloquante

    def test_le_niu_d_un_tiers_est_controle(self, parametres: ServiceParametres):
        entreprise = _batiment_plus(
            tiers=[
                Tiers(
                    code="FRN-001",
                    denomination="QUINCAILLERIE DU WOURI",
                    type=TypeTiers.FOURNISSEUR,
                    niu="PAS-UN-NIU",
                )
            ]
        )
        anomalies = verifier_identifiants(entreprise, parametres, self.LE)
        assert [a.champ for a in anomalies] == ["tiers.FRN-001.niu"]

    def test_un_fournisseur_etranger_n_a_pas_a_porter_de_niu(
        self, parametres: ServiceParametres
    ):
        # Même exclusion que la règle FAC-ID-003 du moteur de conformité.
        entreprise = _batiment_plus(
            tiers=[
                Tiers(
                    code="FRN-999",
                    denomination="GLOBAL STEEL TRADING LTD",
                    type=TypeTiers.FOURNISSEUR,
                    etranger=True,
                )
            ]
        )
        assert verifier_identifiants(entreprise, parametres, self.LE) == []


# ── La composition : B → D → E ───────────────────────────────────────────────────


class TestCompositionAvecLesAutresContextes:
    """Le régime lu chez B commande le contrôle chez D et l'écriture chez E.

    C'est la démonstration que le statut daté sert à quelque chose. Les trois
    contextes ne se connaissent pas : c'est la couche de composition — ici le
    test — qui lit le régime chez B et le transmet.

    ⚠️ `RegimeFiscal` (contexte B) et `RegimeEmetteur` (contexte D) sont deux
    énumérations distinctes portant les mêmes valeurs. La duplication est
    **délibérée** : chaque contexte borné garde son vocabulaire, et la
    correspondance se fait à la composition. Les fusionner créerait une
    dépendance entre D et B qui n'a aucune raison d'être — le moteur de
    conformité n'a pas besoin de connaître le portefeuille pour évaluer une règle.
    """

    def _facture_en_especes(self, regime_adherent: str):
        from datetime import date as _date
        from decimal import Decimal

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

        return FactureAControler(
            document=Document(reference="F-COMPO-001", date_emission=_date(2022, 6, 15)),
            emetteur=Partie(
                denomination="QUINCAILLERIE DU WOURI",
                niu="M053311224455R",
                niu_actif=True,
                regime=RegimeEmetteur.REEL,
            ),
            destinataire=Partie(
                denomination="SARL BATIMENT PLUS",
                niu="M081234567890P",
                niu_actif=True,
                # La valeur vient de B, lue à la date de la facture.
                regime=RegimeEmetteur(regime_adherent),
            ),
            montants=Montants(
                total_ht=Decimal(1_970_650),
                total_tva=Decimal(379_350),
                total_ttc=Decimal(2_350_000),
            ),
            reglement=Reglement(mode=ModeReglement.ESPECES),
            lignes=[
                LigneFacture(designation="Ciment CPJ 42,5", montant_ht=Decimal(1_970_650))
            ],
        )

    def test_au_synthetique_la_regle_de_tva_est_hors_portee(self, moteur):
        # Juin 2022 : BATIMENT PLUS est encore au régime synthétique.
        entreprise = _batiment_plus()
        regime = entreprise.regime_au(date(2022, 6, 15))
        assert regime is RegimeFiscal.IGS

        rapport = moteur.controler(self._facture_en_especes(regime.value))
        assert "FAC-ACH-007" not in {c.code_regle for c in rapport.constats}
        assert rapport.enjeu_total == D(0)

    def test_au_reel_la_meme_facture_coute_379_350(self, moteur):
        # Janvier 2024 : la même entreprise, le même fournisseur, le même montant.
        entreprise = _batiment_plus()
        regime = entreprise.regime_au(date(2024, 1, 15))
        assert regime is RegimeFiscal.REEL

        rapport = moteur.controler(self._facture_en_especes(regime.value))
        assert "FAC-ACH-007" in {c.code_regle for c in rapport.constats}
        assert rapport.enjeu_total == D(379_350)

    def test_l_ecriture_change_de_forme_avec_le_regime(self, moteur):
        # Et jusqu'au bout de la chaîne : l'écriture proposée par E n'a pas le
        # même nombre de lignes selon le régime lu chez B.
        from app.contextes.comptabilite.api import PlanImputation, proposer_ecriture_achat

        plan = PlanImputation(compte_charge_par_defaut="604")
        entreprise = _batiment_plus()

        au_synthetique = self._facture_en_especes(
            entreprise.regime_au(date(2022, 6, 15)).value
        )
        au_reel = self._facture_en_especes(entreprise.regime_au(date(2024, 1, 15)).value)

        ecriture_igs = proposer_ecriture_achat(
            au_synthetique, moteur.controler(au_synthetique), plan,
            journal="AC", exercice="2022", numero=1,
        )
        ecriture_reel = proposer_ecriture_achat(
            au_reel, moteur.controler(au_reel), plan,
            journal="AC", exercice="2024", numero=1,
        )

        assert [ligne.compte for ligne in ecriture_igs.lignes] == ["604", "401"]
        assert [ligne.compte for ligne in ecriture_reel.lignes] == ["604", "4451", "401"]
        # Au synthétique, la TVA est un coût incorporé au prix d'achat.
        assert ecriture_igs.lignes[0].montant == D(2_350_000)
        assert ecriture_reel.lignes[0].montant == D(1_970_650)
