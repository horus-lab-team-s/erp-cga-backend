"""Contexte F · Obligations — l'échéance calculée, et la TVA rejetée rendue visible.

Deux choses sont protégées ici.

La première est une règle de conception : **une date limite se calcule, elle ne se
stocke pas**. Elle dépend du type d'obligation, de la date de clôture et du centre
de rattachement — tous deux historisés. Le 15 mars n'est pas une constante.

La seconde est la raison d'être du produit : la ligne « TVA rejetée par le contrôle
de conformité ». C'est le troisième maillon de la chaîne, celui qui transforme un
constat en argent, et le dernier test du fichier le déroule de la facture jusqu'au
montant à verser au Trésor.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.contextes.comptabilite.api import (
    EcritureComptable,
    LigneEcriture,
    PlanImputation,
    Sens,
    proposer_ecriture_achat,
)
from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.obligations.api import (
    ObligationInstance,
    Periodicite,
    StatutObligation,
    TypeObligation,
    calculer_echeance,
    calculer_penalite,
    etablir_declaration_tva,
    generer_echeancier,
    mois_de_retard,
    relances_du_jour,
)
from app.contextes.portefeuille.api import (
    CentreRattachement,
    Entreprise,
    Exercice,
    FormeJuridique,
    MotifChangement,
    RegimeFiscal,
    StatutRattachement,
    StatutRegime,
)
from app.contextes.transverse.api import Portail

CREATION = date(2020, 1, 1)


def SANS_SALARIES(_du, _au):
    """Le fichier du personnel répond : personne n'a été employé sur la période."""
    return False


def AUCUN_ACCUSE(_reference):
    """Le portail répond : rien n'a été déposé."""
    return None


def AVEC_SALARIES(_du, _au):
    """Le fichier du personnel répond : quelqu'un a été employé sur la période."""
    return True
VALIDE_LE = datetime(2026, 8, 1, 9, 0)

TVA = TypeObligation(
    code="TVA",
    portail=Portail.DGI_TELEDECLARATION,
    libelle="Déclaration de taxe sur la valeur ajoutée",
    periodicite=Periodicite.MENSUELLE,
    jour_limite=15,
    exige_assujettissement_tva=True,
    declaration_neant_due=True,
)

DSF = TypeObligation(
    code="DSF",
    portail=Portail.DGI_TELEDECLARATION,
    libelle="Déclaration Statistique et Fiscale",
    periodicite=Periodicite.ANNUELLE_EXERCICE,
    delai_jours_apres_cloture=75,
    decalage_par_centre={
        CentreRattachement.DGE: 0,
        CentreRattachement.CIME: 15,
        CentreRattachement.CDI: 30,
    },
)

CNPS = TypeObligation(
    code="CNPS",
    portail=Portail.CNPS_DIPE,
    libelle="Cotisations sociales et DIPE",
    periodicite=Periodicite.MENSUELLE,
    jour_limite=15,
    exige_salaries=True,
)


def _entreprise(*, bascule: date | None = None, **extra) -> Entreprise:
    """Un dossier au réel depuis toujours, ou basculant à la date donnée."""
    if bascule is None:
        regimes = [
            StatutRegime(
                debut=CREATION, regime=RegimeFiscal.REEL, motif=MotifChangement.CREATION
            )
        ]
    else:
        regimes = [
            StatutRegime(
                debut=CREATION,
                fin=bascule,
                regime=RegimeFiscal.IGS,
                motif=MotifChangement.CREATION,
            ),
            StatutRegime(
                debut=bascule,
                regime=RegimeFiscal.REEL,
                motif=MotifChangement.DEPASSEMENT_SEUIL,
            ),
        ]
    base: dict = {
        "niu": "M081234567890P",
        "denomination": "SARL BATIMENT PLUS",
        "forme_juridique": FormeJuridique.SARL,
        "date_creation": CREATION,
        "regimes": regimes,
        "rattachements": [
            StatutRattachement(
                debut=CREATION,
                centre=CentreRattachement.CIME,
                motif=MotifChangement.CREATION,
            )
        ],
        "exercices": [
            Exercice(
                libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31)
            )
        ],
    }
    return Entreprise(**(base | extra))


EXERCICE_2026 = Exercice(
    libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31)
)


# ── Le calcul de l'échéance ──────────────────────────────────────────────────────


class TestCalculDeLEcheance:
    def test_une_obligation_mensuelle_echoit_le_mois_suivant(self):
        assert calculer_echeance(
            TVA, fin_de_periode=date(2026, 7, 31), centre=CentreRattachement.CIME
        ) == date(2026, 8, 15)

    def test_le_jour_limite_est_ramene_au_dernier_jour_du_mois(self):
        # Un jour limite au 31 n'existe pas en février. Bloquer un échéancier
        # entier pour cela serait absurde.
        fin_de_mois = TypeObligation(
            code="X", libelle="X", periodicite=Periodicite.MENSUELLE, jour_limite=31,
            portail=Portail.DGI_TELEDECLARATION,
        )
        assert calculer_echeance(
            fin_de_mois, fin_de_periode=date(2026, 1, 31), centre=CentreRattachement.CDI
        ) == date(2026, 2, 28)

    def test_le_15_mars_n_est_pas_une_constante(self):
        # Clôture au 31 décembre 2026, plus 75 jours, plus 15 jours de décalage
        # CIME. Rien de tout cela n'est écrit en dur nulle part.
        assert calculer_echeance(
            DSF,
            fin_de_periode=date(2026, 12, 31),
            centre=CentreRattachement.CIME,
            cloture_exercice=date(2026, 12, 31),
        ) == date(2027, 3, 31)

    def test_un_exercice_decale_decale_l_echeance(self):
        # Clos au 30 juin : l'échéance tombe en septembre, pas en mars.
        echeance = calculer_echeance(
            DSF,
            fin_de_periode=date(2026, 6, 30),
            centre=CentreRattachement.DGE,
            cloture_exercice=date(2026, 6, 30),
        )
        assert echeance == date(2026, 9, 13)

    def test_l_echelonnement_par_centre_est_applique(self):
        dates = {
            centre: calculer_echeance(
                DSF,
                fin_de_periode=date(2026, 12, 31),
                centre=centre,
                cloture_exercice=date(2026, 12, 31),
            )
            for centre in CentreRattachement
        }
        assert dates[CentreRattachement.DGE] < dates[CentreRattachement.CIME]
        assert dates[CentreRattachement.CIME] < dates[CentreRattachement.CDI]

    def test_sans_cloture_une_obligation_d_exercice_ne_se_calcule_pas(self):
        with pytest.raises(ValueError, match="clôture"):
            calculer_echeance(
                DSF, fin_de_periode=date(2026, 12, 31), centre=CentreRattachement.CDI
            )

    def test_une_formule_incomplete_est_refusee_au_chargement(self):
        with pytest.raises(ValidationError, match="jour limite"):
            TypeObligation(
                code="X", libelle="X", periodicite=Periodicite.MENSUELLE,
                portail=Portail.DGI_TELEDECLARATION,
            )


class TestPorteeDesObligations:
    def test_la_tva_ne_concerne_que_les_assujettis(self):
        assert TVA.concerne(RegimeFiscal.REEL, assujettie_tva=True, a_des_salaries=False)
        assert not TVA.concerne(
            RegimeFiscal.IGS, assujettie_tva=False, a_des_salaries=False
        )

    def test_le_liberatoire_n_efface_pas_les_cotisations_sociales(self):
        # Le quatrième des huit pièges, et le plus coûteux : un forfait libère de
        # l'impôt sur le bénéfice, et de lui seul.
        assert CNPS.concerne(RegimeFiscal.IGS, assujettie_tva=False, a_des_salaries=True)


# ── Les pénalités ────────────────────────────────────────────────────────────────


class TestPenalites:
    ECHEANCE = date(2026, 8, 15)

    def test_avant_l_echeance_il_n_y_a_rien(self):
        penalite = calculer_penalite(
            D(1_000_000),
            echeance=self.ECHEANCE,
            a_la_date=date(2026, 8, 15),
            taux_fixe=D(25),
            taux_mensuel=D("1.5"),
        )
        assert penalite.total == 0
        assert penalite.a_regler == D(1_000_000)

    def test_toute_fraction_de_mois_compte_pour_un_mois(self):
        assert mois_de_retard(self.ECHEANCE, date(2026, 8, 16)) == 1
        assert mois_de_retard(self.ECHEANCE, date(2026, 9, 15)) == 1
        assert mois_de_retard(self.ECHEANCE, date(2026, 9, 16)) == 2

    def test_la_penalite_se_decompose_pour_etre_explicable(self):
        # 25 % de pénalité fixe, plus 1,5 % par mois. Du 15 août au 15 novembre :
        # exactement trois mois. Un jour de plus en ferait quatre, puisque toute
        # fraction de mois compte pour un mois entier.
        penalite = calculer_penalite(
            D(1_000_000),
            echeance=self.ECHEANCE,
            a_la_date=date(2026, 11, 15),
            taux_fixe=D(25),
            taux_mensuel=D("1.5"),
        )
        assert penalite.mois_de_retard == 3
        assert penalite.penalite_fixe == D(250_000)
        assert penalite.majoration_mensuelle == D(45_000)
        assert penalite.a_regler == D(1_295_000)


# ── L'instance d'obligation ──────────────────────────────────────────────────────


class TestObligationInstance:
    def _instance(self, **extra) -> ObligationInstance:
        base: dict = {
            "entreprise": "M081234567890P",
            "code_obligation": "TVA",
            "libelle": "Déclaration de TVA",
            "periode_debut": date(2026, 7, 1),
            "periode_fin": date(2026, 7, 31),
            "echeance": date(2026, 8, 15),
        }
        return ObligationInstance(**(base | extra))

    def test_le_retard_est_calcule_jamais_stocke(self):
        obligation = self._instance()
        assert not obligation.en_retard(date(2026, 8, 15))
        assert obligation.en_retard(date(2026, 8, 16))

    def test_une_obligation_deposee_n_est_jamais_en_retard(self):
        deposee = self._instance(
            statut=StatutObligation.DECLAREE, declaree_le=date(2026, 8, 10)
        )
        assert not deposee.en_retard(date(2026, 12, 31))

    def test_on_ne_declare_pas_une_periode_avant_qu_elle_soit_ecoulee(self):
        with pytest.raises(ValidationError, match="antérieure à la fin de la période"):
            self._instance(echeance=date(2026, 7, 15))

    def test_une_declaration_deposee_porte_sa_date(self):
        # C'est elle qui prouve que l'obligation a été remplie dans les délais.
        with pytest.raises(ValidationError, match="date de dépôt"):
            self._instance(statut=StatutObligation.DECLAREE)

    def test_l_obligation_avance_mais_ne_recule_pas(self):
        prete = self._instance(statut=StatutObligation.PRETE)
        declaree = prete.avancer(StatutObligation.DECLAREE, declaree_le=date(2026, 8, 12))
        assert declaree.deposee
        with pytest.raises(ValueError, match="on ne revient pas"):
            declaree.avancer(StatutObligation.A_FAIRE)

    def test_la_completude_relativise_le_montant_estime(self):
        obligation = self._instance(
            montant_estime=D(4_820_000), pieces_recues=22, pieces_attendues=24
        )
        assert obligation.completude == D("0.92")
        assert not obligation.complete


# ── La génération de l'échéancier ────────────────────────────────────────────────


class TestEcheancier:
    def test_une_obligation_mensuelle_donne_douze_instances(self):
        echeancier = generer_echeancier(
            _entreprise(), [TVA], EXERCICE_2026, emploie_sur=SANS_SALARIES, accuse_de=AUCUN_ACCUSE
        )
        assert len(echeancier) == 12
        assert echeancier[0].periode_debut == date(2026, 1, 1)
        assert echeancier[-1].echeance == date(2027, 1, 15)

    def test_l_echeancier_se_genere_depuis_le_profil_pas_depuis_les_pieces(self):
        # Une déclaration néant est due même sans opération. Un échéancier
        # alimenté par les factures reçues serait structurellement faux.
        echeancier = generer_echeancier(
            _entreprise(), [TVA], EXERCICE_2026, emploie_sur=SANS_SALARIES, accuse_de=AUCUN_ACCUSE
        )
        assert all(o.montant_estime is None for o in echeancier)
        assert len(echeancier) == 12

    def test_le_profil_est_evalue_a_la_fin_de_chaque_periode(self):
        # Franchissement de seuil au 1er septembre : quatre déclarations de TVA,
        # pas douze — et pas zéro non plus.
        entreprise = _entreprise(bascule=date(2026, 9, 1))
        echeancier = generer_echeancier(
            entreprise, [TVA], EXERCICE_2026, emploie_sur=SANS_SALARIES,
            accuse_de=AUCUN_ACCUSE,
        )
        assert len(echeancier) == 4
        assert echeancier[0].periode_debut == date(2026, 9, 1)

    def test_sans_salaries_pas_d_obligation_sociale(self):
        assert generer_echeancier(
            _entreprise(), [CNPS], EXERCICE_2026, emploie_sur=SANS_SALARIES, accuse_de=AUCUN_ACCUSE
        ) == []
        avec = generer_echeancier(
            _entreprise(), [CNPS], EXERCICE_2026, emploie_sur=AVEC_SALARIES, accuse_de=AUCUN_ACCUSE
        )
        assert len(avec) == 12
        assert not any(o.effectif_a_confirmer for o in avec)

    def test_la_question_se_pose_periode_par_periode(self):
        """⚠️ **Une embauche en juin donne la CNPS de juin, pas celle de janvier.** (pas 56)

        L'ancien booléen valait pour tout l'exercice : il aurait fallu choisir entre
        cinq cotisations fantômes et sept cotisations manquées.
        """
        posees = []

        def embauche_le_20_juin(du, au_inclus):
            posees.append((du, au_inclus))
            return au_inclus >= date(2026, 6, 20)

        cnps = generer_echeancier(
            _entreprise(), [CNPS], EXERCICE_2026, emploie_sur=embauche_le_20_juin,
            accuse_de=AUCUN_ACCUSE
        )
        assert [o.periode_debut.month for o in cnps] == list(range(6, 13))
        assert posees[5] == (date(2026, 6, 1), date(2026, 6, 30)), "le mois entier est demandé"

    def test_un_personnel_muet_affiche_l_obligation_a_confirmer(self):
        """Le social qui ne répond pas ne fait disparaître aucune obligation : une
        obligation montrée à tort se vérifie, une obligation omise se paie."""
        muet = generer_echeancier(
            _entreprise(), [CNPS, TVA], EXERCICE_2026, emploie_sur=lambda _du, _au: None,
            accuse_de=AUCUN_ACCUSE
        )
        cnps = [o for o in muet if o.code_obligation == "CNPS"]
        tva = [o for o in muet if o.code_obligation == "TVA"]
        assert len(cnps) == 12 and all(o.effectif_a_confirmer for o in cnps)
        assert len(tva) == 12 and not any(o.effectif_a_confirmer for o in tva)

    def test_la_tva_n_attend_pas_le_fichier_du_personnel(self):
        """La question n'est posée qu'aux obligations qui en dépendent."""

        def interdit(_du, _au):
            raise AssertionError("le personnel a été interrogé pour la TVA")

        assert len(
            generer_echeancier(
                _entreprise(), [TVA], EXERCICE_2026, emploie_sur=interdit,
                accuse_de=AUCUN_ACCUSE,
            )
        ) == 12

    def test_un_exercice_court_donne_moins_de_periodes(self):
        court = Exercice(
            libelle="2026", ouverture=date(2026, 10, 15), cloture=date(2026, 12, 31)
        )
        echeancier = generer_echeancier(
            _entreprise(), [TVA], court, emploie_sur=SANS_SALARIES,
            accuse_de=AUCUN_ACCUSE,
        )
        assert len(echeancier) == 3
        # Le premier mois est tronqué : l'entreprise n'existait pas avant le 15.
        assert echeancier[0].periode_debut == date(2026, 10, 15)

    def test_l_echeancier_est_trie_par_echeance(self):
        echeancier = generer_echeancier(
            _entreprise(), [DSF, TVA], EXERCICE_2026, emploie_sur=AVEC_SALARIES,
            accuse_de=AUCUN_ACCUSE
        )
        assert [o.echeance for o in echeancier] == sorted(o.echeance for o in echeancier)
        assert echeancier[-1].code_obligation == "DSF"


class TestUneObligationDeposeeNEstPlusARelancer:
    """⚠️ **L'échéancier ne lisait pas les accusés.** (pas 58)

    Recalculé à chaque lecture, il naissait « à faire » : une TVA de juillet déposée
    restait en retard, et la relance J+1 partait vers un adhérent à jour.
    """

    @staticmethod
    def _accuse(code, debut, fin, *, numero="DGI-2026-0007741", le=datetime(2026, 8, 14, 10, 22)):
        from app.contextes.transverse.api import (
            AccuseReception,
            ModeDepot,
            Portail,
            reference_de_depot,
        )

        return AccuseReception(
            numero=numero,
            portail=Portail.DGI_TELEDECLARATION,
            reference_document=reference_de_depot("M081234567890P", code, debut, fin),
            depose_le=le,
            mode=ModeDepot.MANUEL,
            empreinte_deposee="0" * 64,
            depose_par="a.bouba",
        )

    def _echeancier(self, *accuses):
        index = {a.reference_document: a for a in accuses}
        return generer_echeancier(
            _entreprise(), [TVA, CNPS], EXERCICE_2026,
            emploie_sur=AVEC_SALARIES, accuse_de=index.get,
        )

    def test_l_obligation_deposee_est_declaree_a_la_date_de_l_accuse(self):
        juillet = self._accuse("TVA", date(2026, 7, 1), date(2026, 7, 31))
        echeancier = self._echeancier(juillet)
        (tva_juillet,) = [
            o for o in echeancier
            if o.code_obligation == "TVA" and o.periode_debut == date(2026, 7, 1)
        ]
        assert tva_juillet.statut is StatutObligation.DECLAREE
        # ⚠️ La date de l'accusé, pas celle de la lecture : c'est elle qui dit si le
        # dépôt était à temps.
        assert tva_juillet.declaree_le == date(2026, 8, 14)
        assert tva_juillet.reference_depot == "DGI-2026-0007741"
        assert tva_juillet.en_retard(date(2026, 9, 20)) is False

    def test_rien_d_autre_ne_change(self):
        """La contre-épreuve : un accusé de juillet ne dépose ni août, ni la CNPS de
        juillet. Une référence mal formée marquerait tout, ou rien."""
        echeancier = self._echeancier(self._accuse("TVA", date(2026, 7, 1), date(2026, 7, 31)))
        deposees = [(o.code_obligation, o.periode_debut) for o in echeancier if o.deposee]
        assert deposees == [("TVA", date(2026, 7, 1))]

    def test_sans_accuse_l_obligation_echue_reste_en_retard(self):
        (tva_juillet,) = [
            o for o in self._echeancier()
            if o.code_obligation == "TVA" and o.periode_debut == date(2026, 7, 1)
        ]
        assert tva_juillet.en_retard(date(2026, 9, 20)) is True


class TestRelances:
    ECHEANCE = date(2026, 8, 15)

    def _obligation(self, **extra) -> ObligationInstance:
        base: dict = {
            "entreprise": "M081234567890P",
            "code_obligation": "TVA",
            "libelle": "Déclaration de TVA",
            "periode_debut": date(2026, 7, 1),
            "periode_fin": date(2026, 7, 31),
            "echeance": self.ECHEANCE,
        }
        return ObligationInstance(**(base | extra))

    def test_les_quatre_jalons_tombent_aux_bons_jours(self):
        obligation = self._obligation()
        jours = {
            date(2026, 7, 31): -15,
            date(2026, 8, 8): -7,
            date(2026, 8, 13): -2,
            date(2026, 8, 16): 1,
        }
        for jour, jalon in jours.items():
            relances = relances_du_jour([obligation], jour)
            assert len(relances) == 1, jour
            assert relances[0].jalon == jalon

    def test_aucune_relance_les_autres_jours(self):
        assert relances_du_jour([self._obligation()], date(2026, 8, 10)) == []

    def test_une_obligation_deposee_n_est_jamais_relancee(self):
        # C'est la première cause d'exaspération d'un adhérent, et elle
        # décrédibilise toutes les relances suivantes.
        deposee = self._obligation(
            statut=StatutObligation.DECLAREE, declaree_le=date(2026, 8, 5)
        )
        assert relances_du_jour([deposee], date(2026, 8, 8)) == []

    def test_le_jalon_apres_echeance_est_marque_depasse(self):
        relance = relances_du_jour([self._obligation()], date(2026, 8, 16))[0]
        assert relance.depassee
        assert relance.urgente


# ── La chaîne complète : facture → écriture → déclaration ────────────────────────


def _ecritures_de_juillet() -> list[EcritureComptable]:
    """Trois achats contrôlés par le vrai moteur, puis une vente.

    Les trois achats viennent du jeu de démonstration : l'un est réglé en espèces
    au-delà du seuil, les deux autres sont conformes.
    """
    from app.contextes.conformite.adaptateurs.sortant.depot_regles_yaml import (
        DepotReglesYaml,
    )
    from app.contextes.conformite.application.moteur_conformite import MoteurConformite
    from app.contextes.referentiel.api import DepotParametresYaml, ServiceParametres
    from app.infrastructure.config import RACINE_DEPOT

    referentiel = RACINE_DEPOT / "Docs" / "referentiel"
    moteur = MoteurConformite(
        regles=DepotReglesYaml(referentiel / "regles").charger(),
        parametres=ServiceParametres.depuis_depot(
            DepotParametresYaml(referentiel / "parametres.yaml")
        ),
    )
    plan = PlanImputation(compte_charge_par_defaut="604")

    ecritures = []
    for numero, reference in enumerate(
        ("F-2026-0412", "F-2026-0413", "F-2026-0419"), start=1
    ):
        facture = FACTURES_DEMO[reference]
        brouillon = proposer_ecriture_achat(
            facture,
            moteur.controler(facture),
            plan,
            journal="AC",
            exercice="2026",
            numero=numero,
        )
        ecritures.append(brouillon.valider("Rodrigue BIYA'A", VALIDE_LE))

    ecritures.append(
        EcritureComptable(
            journal="VE",
            exercice="2026",
            numero=1,
            date_operation=date(2026, 7, 20),
            libelle="Chantier Bonabéri",
            piece_justificative="V-2026-0071",
            lignes=[
                LigneEcriture(
                    compte="411", libelle="Client", sens=Sens.DEBIT, montant=D(6_192_500)
                ),
                LigneEcriture(
                    compte="701", libelle="Travaux", sens=Sens.CREDIT, montant=D(5_192_500)
                ),
                LigneEcriture(
                    compte="4431",
                    libelle="TVA collectée",
                    sens=Sens.CREDIT,
                    montant=D(1_000_000),
                ),
            ],
        ).valider("Rodrigue BIYA'A", VALIDE_LE)
    )
    return ecritures


class TestDeclarationTVA:
    """Le troisième maillon : le constat devient de l'argent."""

    def _declaration(self, **extra):
        return etablir_declaration_tva(
            _ecritures_de_juillet(),
            entreprise="M081234567890P",
            periode_debut=date(2026, 7, 1),
            periode_fin=date(2026, 7, 31),
            **extra,
        )

    def test_la_tva_rejetee_est_isolee_et_chiffree(self):
        # 379 350 FCFA — le montant exact du bandeau de verdict de l'écran E02,
        # obtenu par un chemin entièrement différent.
        declaration = self._declaration()
        assert declaration.tva_rejetee == D(379_350)
        assert declaration.cout_de_la_non_conformite == D(379_350)

    def test_la_deductible_admise_est_la_theorique_moins_le_rejet(self):
        declaration = self._declaration()
        assert declaration.tva_deductible_theorique == D(879_850)
        assert declaration.tva_deductible_admise == D(500_500)

    def test_le_solde_a_payer(self):
        declaration = self._declaration()
        assert declaration.tva_collectee == D(1_000_000)
        assert declaration.solde == D(499_500)
        assert declaration.tva_a_payer == D(499_500)
        assert declaration.credit_a_reporter == 0

    def test_chaque_rejet_est_justifie_piece_par_piece(self):
        # C'est ce qu'aucune déclaration ordinaire ne montre : dans une
        # déclaration classique, la case « TVA déductible » porte simplement un
        # chiffre plus faible, et rien n'explique pourquoi.
        declaration = self._declaration()
        assert len(declaration.detail_rejets) == 1
        rejet = declaration.detail_rejets[0]
        assert rejet.piece == "F-2026-0412"
        assert rejet.code_regle == "FAC-ACH-007"
        assert "espèces" in rejet.motif

    def test_un_credit_anterieur_se_reporte(self):
        declaration = self._declaration(credit_reporte_anterieur=D(600_000))
        assert declaration.solde == D(-100_500)
        assert declaration.tva_a_payer == 0
        assert declaration.credit_a_reporter == D(100_500)

    def test_sans_operation_la_declaration_est_neant_mais_reste_due(self):
        declaration = etablir_declaration_tva(
            [],
            entreprise="M081234567890P",
            periode_debut=date(2026, 8, 1),
            periode_fin=date(2026, 8, 31),
        )
        assert declaration.neant
        assert declaration.solde == 0

    def test_les_brouillons_sont_exclus(self):
        # Un brouillon n'est pas de la comptabilité : fonder une déclaration
        # dessus produirait un chiffre que personne ne pourrait justifier.
        brouillons = [e.model_copy(update={"etat": "BROUILLON"}) for e in _ecritures_de_juillet()]
        declaration = etablir_declaration_tva(
            brouillons,
            entreprise="M081234567890P",
            periode_debut=date(2026, 7, 1),
            periode_fin=date(2026, 7, 31),
        )
        assert declaration.neant

    def test_hors_periode_rien_n_est_retenu(self):
        declaration = etablir_declaration_tva(
            _ecritures_de_juillet(),
            entreprise="M081234567890P",
            periode_debut=date(2026, 8, 1),
            periode_fin=date(2026, 8, 31),
        )
        assert declaration.neant
