"""L'affectation : à qui confier une demande, et pourquoi celui-là.

Trois familles, et la première est la plus importante :

* **la grille est de la configuration.** Les tests le prouvent en changeant un
  fichier de règles, jamais une ligne de code, et en constatant que la décision
  change. Un domaine « configurable » dont aucun test ne change la configuration
  est un domaine dont personne n'a vérifié qu'il l'était ;
* **le classement est reproductible.** Deux exécutions du même cas donnent la même
  réponse, quel que soit l'ordre des candidats ;
* **personne ne convient est un cas ordinaire**, pas une exception.

Le paquet de règles réel du référentiel est chargé et éprouvé à part, pour que le
jour où le centre le révise, un test le dise.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.souscription.application.affectation import (
    affecter_le_dossier,
    reaffecter_le_dossier,
)
from app.contextes.souscription.domaine.affectation import (
    SCHEMA_AFFECTATION,
    Candidature,
    RegleDAffectation,
    choisir,
    choix_parmi,
    classer,
    evaluer_candidature,
)
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    EtatDossier,
    ouvrir_un_dossier,
)
from app.infrastructure.config import RACINE_DEPOT

LE_JOUR = date(2026, 9, 9)
L_INSTANT = datetime(2026, 9, 9, 10, 0)
GRILLE_REELLE = RACINE_DEPOT / "Docs" / "referentiel" / "affectation"

FONDEMENT = Fondement(texte="Critère d'essai", source="tests")


def _regle(code: str, predicat: dict, **surcharges) -> RegleDAffectation:
    defauts = {
        "code": code,
        "libelle": f"Critère {code}",
        "applicable_du": date(2026, 1, 1),
        "predicat": predicat,
        "penalite": 10,
        "fondement": FONDEMENT,
    }
    return RegleDAffectation(**{**defauts, **surcharges})


def _candidat(responsable: str, **surcharges) -> Candidature:
    defauts = {
        "responsable": responsable,
        "service": "creation-sarl",
        "region_demande": "Douala",
        "agence_responsable": "Douala",
        "competences": ("creation",),
        "competence_requise": "creation",
        "charge_ponderee": Decimal(0),
    }
    return Candidature(**{**defauts, **surcharges})


def _dossier():
    demande = DemandeDeContact(
        identifiant="dc-1",
        deposee_le=L_INSTANT,
        nom="Abena Ndzana",
        telephone="699112233",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.WHATSAPP,
        consentement=Consentement(
            accorde=True,
            recueilli_le=L_INSTANT,
            version_du_texte="consentement-whatsapp-v1",
        ),
    )
    return ouvrir_un_dossier("dos-1", demande)


# ── La candidature ────────────────────────────────────────────────────────────


class TestCandidature:
    def test_elle_expose_exactement_les_faits_du_schema(self):
        """Un fait exposé mais non déclaré serait invisible aux auteurs de
        règles ; un fait déclaré mais non exposé ferait taire silencieusement
        chaque critère qui le cite."""
        assert set(_candidat("resp-1").faits()) == set(SCHEMA_AFFECTATION.codes)

    def test_la_meme_agence_est_insensible_a_la_casse(self):
        """Les deux chaînes viennent de sources différentes : un formulaire
        public et un annuaire interne. Exiger qu'elles coïncident au caractère
        près ferait échouer la proximité sur « Douala » contre « douala »."""
        assert _candidat("r", region_demande=" douala ", agence_responsable="Douala").meme_agence

    def test_une_region_non_declaree_ne_vaut_jamais_la_meme_agence(self):
        """Le visiteur n'a rien dit ; prétendre qu'il est du coin serait
        inventer, et l'inventer pénaliserait l'agence qui n'a rien fait."""
        assert not _candidat("r", region_demande="", agence_responsable="").meme_agence
        assert not _candidat("r", region_demande="   ", agence_responsable="Douala").meme_agence


# ── La règle ──────────────────────────────────────────────────────────────────


class TestRegleDAffectation:
    def test_un_critere_sans_effet_est_refuse(self):
        """Ni rédhibitoire ni pénalisant : sa présence dans la grille laisserait
        croire le contraire à qui la lit."""
        with pytest.raises(ValueError, match="aucun effet"):
            _regle("AFF-X", {"==": [1, 1]}, penalite=0)

    def test_un_critere_redhibitoire_peut_ne_rien_penaliser(self):
        """Écarter est déjà le maximum : exiger en plus une pénalité obligerait à
        inventer un nombre qui ne servirait jamais."""
        regle = _regle("AFF-X", {"==": [1, 1]}, penalite=0, redhibitoire=True)
        assert regle.penalite == 0

    def test_un_critere_valide_sans_signataire_est_refuse(self):
        """Sans signataire nommé, la règle de routage n'est opposable à
        personne."""
        with pytest.raises(ValueError, match="valide_par"):
            _regle("AFF-X", {"==": [1, 1]}, statut=StatutValidation.VALIDE)

    def test_les_bornes_incoherentes_sont_refusees(self):
        with pytest.raises(ValueError, match="borne de validité"):
            _regle(
                "AFF-X",
                {"==": [1, 1]},
                applicable_du=date(2026, 6, 1),
                applicable_au=date(2026, 1, 1),
            )

    def test_une_regle_hors_vigueur_ne_s_applique_pas(self):
        """Une décision d'affectation se rejoue à la date où elle a été prise,
        pas à celle où on l'examine."""
        ancienne = _regle(
            "AFF-X",
            {"==": [{"var": "disponible"}, True]},
            applicable_du=date(2025, 1, 1),
            applicable_au=date(2026, 1, 1),
        )
        verdict = evaluer_candidature(
            _candidat("resp-1", disponible=False), [ancienne], LE_JOUR
        )
        assert verdict.motifs == ()


# ── L'évaluation d'une candidature ────────────────────────────────────────────


class TestEvaluerCandidature:
    def test_un_predicat_vrai_ne_declenche_rien(self):
        """La convention du projet : VRAI = ce responsable convient de ce point
        de vue."""
        verdict = evaluer_candidature(
            _candidat("resp-1"),
            [_regle("AFF-X", {"==": [{"var": "meme_agence"}, True]})],
            LE_JOUR,
        )
        assert verdict.penalite == 0
        assert verdict.eligible is True

    def test_un_predicat_faux_ajoute_sa_penalite(self):
        verdict = evaluer_candidature(
            _candidat("resp-1", agence_responsable="Yaounde"),
            [_regle("AFF-X", {"==": [{"var": "meme_agence"}, True]}, penalite=30)],
            LE_JOUR,
        )
        assert verdict.penalite == 30
        assert verdict.eligible is True

    def test_les_penalites_se_cumulent(self):
        """C'est ainsi qu'on exprime une progression par paliers sans opérateur
        de palier dans le moteur : deux seuils, deux règles, deux pénalités."""
        regles = [
            _regle("AFF-A", {"<": [{"var": "charge_ponderee"}, 60]}, penalite=20),
            _regle("AFF-B", {"<": [{"var": "charge_ponderee"}, 120]}, penalite=40),
        ]
        verdict = evaluer_candidature(
            _candidat("resp-1", charge_ponderee=Decimal(150)), regles, LE_JOUR
        )
        assert verdict.penalite == 60
        assert len(verdict.motifs) == 2

    def test_un_critere_redhibitoire_ecarte(self):
        verdict = evaluer_candidature(
            _candidat("resp-1", disponible=False),
            [
                _regle(
                    "AFF-DIS",
                    {"==": [{"var": "disponible"}, True]},
                    penalite=0,
                    redhibitoire=True,
                )
            ],
            LE_JOUR,
        )
        assert verdict.ecartee is True
        assert verdict.empechements == ("Critère AFF-DIS",)

    def test_une_regle_cassee_est_signalee_et_non_tue(self):
        """Un critère silencieusement absent est indiscernable d'un critère
        satisfait : il ferait passer pour idéal un responsable qu'on n'a pas
        jugé."""
        verdict = evaluer_candidature(
            _candidat("resp-1"),
            [_regle("AFF-CASSE", {"operateur-inexistant": [1, 2]})],
            LE_JOUR,
        )
        assert verdict.echecs
        assert "AFF-CASSE" in verdict.echecs[0]
        assert verdict.eligible is True


# ── Le classement ─────────────────────────────────────────────────────────────


class TestClassement:
    def test_le_moins_penalise_passe_en_tete(self):
        regles = [_regle("AFF-PRX", {"==": [{"var": "meme_agence"}, True]}, penalite=30)]
        verdicts = classer(
            [
                _candidat("bikoi", agence_responsable="Yaounde"),
                _candidat("awono"),
            ],
            regles,
            LE_JOUR,
        )
        assert [v.responsable for v in verdicts] == ["awono", "bikoi"]

    def test_les_ecartes_passent_en_dernier_meme_sans_penalite(self):
        """Un écarté à zéro point n'est pas meilleur qu'un éligible à cinquante :
        il n'est pas comparable, il est hors jeu."""
        regles = [
            _regle(
                "AFF-DIS",
                {"==": [{"var": "disponible"}, True]},
                penalite=0,
                redhibitoire=True,
            ),
            _regle("AFF-PRX", {"==": [{"var": "meme_agence"}, True]}, penalite=50),
        ]
        verdicts = classer(
            [
                _candidat("absent", disponible=False),
                _candidat("loin", agence_responsable="Yaounde"),
            ],
            regles,
            LE_JOUR,
        )
        assert [v.responsable for v in verdicts] == ["loin", "absent"]

    def test_les_egalites_se_tranchent_sur_l_identifiant(self):
        """Sans cette clé, le désigné dépendrait de l'ordre rendu par la base.
        Deux exécutions du même cas donneraient deux réponses, et l'on ne
        pourrait ni rejouer ni expliquer une décision."""
        candidats = [_candidat("zephirin"), _candidat("awono"), _candidat("mbala")]
        for ordre in (candidats, list(reversed(candidats)), candidats[1:] + candidats[:1]):
            verdicts = classer(ordre, [], LE_JOUR)
            assert [v.responsable for v in verdicts] == ["awono", "mbala", "zephirin"]

    def test_choix_parmi_prend_le_premier_eligible(self):
        regles = [
            _regle(
                "AFF-CMP",
                {"in": [{"var": "competence_requise"}, {"var": "competences"}]},
                penalite=0,
                redhibitoire=True,
            )
        ]
        verdicts = classer(
            [_candidat("awono", competences=()), _candidat("bikoi")], regles, LE_JOUR
        )
        assert choix_parmi(verdicts).responsable == "bikoi"

    def test_personne_ne_convient_rend_none(self):
        """Ce n'est pas une erreur : c'est le cas où la demande part en file de
        pôle avec son alerte."""
        regles = [
            _regle(
                "AFF-DIS",
                {"==": [{"var": "disponible"}, True]},
                penalite=0,
                redhibitoire=True,
            )
        ]
        assert choisir([_candidat("awono", disponible=False)], regles, LE_JOUR) is None

    def test_sans_candidat_il_n_y_a_pas_de_choix(self):
        assert choisir([], [], LE_JOUR) is None

    def test_le_motif_dit_le_nombre_de_candidats_et_les_reserves(self):
        """Un motif illisible est un motif que personne ne lit, et une
        traçabilité que personne ne lit ne trace rien."""
        regles = [_regle("AFF-PRX", {"==": [{"var": "meme_agence"}, True]}, penalite=30)]
        choix = choisir(
            [_candidat("awono", agence_responsable="Yaounde"), _candidat("bikoi")],
            regles,
            LE_JOUR,
        )
        assert "2 candidats" in choix.motif
        assert choix.responsable == "bikoi"
        assert "aucun critère déclenché" in choix.motif


# ── La grille est de la configuration ─────────────────────────────────────────


class TestLaGrilleEstConfiguree:
    """La démonstration littérale du principe : **la même ligne de code, deux
    grilles, deux décisions.** Un domaine dit configurable dont aucun test ne
    change la configuration est un domaine dont personne n'a vérifié qu'il
    l'était."""

    CANDIDATS = (
        # Proche, mais très chargé.
        ("proche", {"charge_ponderee": Decimal(150)}),
        # Loin, et disponible.
        ("loin", {"agence_responsable": "Yaounde"}),
    )

    def _candidats(self):
        return [_candidat(nom, **kw) for nom, kw in self.CANDIDATS]

    def test_la_proximite_l_emporte_quand_elle_pese_le_plus(self):
        grille = [
            _regle("AFF-PRX", {"==": [{"var": "meme_agence"}, True]}, penalite=100),
            _regle("AFF-CHG", {"<": [{"var": "charge_ponderee"}, 60]}, penalite=20),
        ]
        assert choisir(self._candidats(), grille, LE_JOUR).responsable == "proche"

    def test_la_charge_l_emporte_quand_le_centre_la_repondere(self):
        """Un seul nombre change dans un fichier. Aucune ligne de code ne
        bouge."""
        grille = [
            _regle("AFF-PRX", {"==": [{"var": "meme_agence"}, True]}, penalite=10),
            _regle("AFF-CHG", {"<": [{"var": "charge_ponderee"}, 60]}, penalite=200),
        ]
        assert choisir(self._candidats(), grille, LE_JOUR).responsable == "loin"

    def test_un_critere_bascule_de_penalisant_a_redhibitoire_sans_code(self):
        """Ce qui bloque aujourd'hui sera une préférence demain, et l'inverse.
        C'est un booléen dans un fichier."""
        predicat = {"<": [{"var": "charge_ponderee"}, 120]}
        souple = [_regle("AFF-CHG", predicat, penalite=40)]
        strict = [_regle("AFF-CHG", predicat, penalite=0, redhibitoire=True)]
        charge = [_candidat("seul", charge_ponderee=Decimal(150))]

        assert choisir(charge, souple, LE_JOUR).responsable == "seul"
        assert choisir(charge, strict, LE_JOUR) is None

    def test_une_grille_vide_retient_le_premier_par_ordre_d_identifiant(self):
        """Le cas du démarrage : aucune règle arrêtée, et il faut quand même
        rappeler les gens. Ne rien décider vaut mieux que ne rien faire."""
        choix = choisir([_candidat("zephirin"), _candidat("awono")], [], LE_JOUR)
        assert choix.responsable == "awono"


# ── Le paquet de règles réel ──────────────────────────────────────────────────


class TestGrilleDuReferentiel:
    @pytest.fixture(scope="class")
    def grille(self):
        return charger_la_grille_d_affectation(GRILLE_REELLE)

    def test_elle_se_charge(self, grille):
        assert len(grille) >= 5

    def test_sans_region_declaree_la_proximite_ne_signale_rien(self, grille):
        """⚠️ Pas 64 : le site ne collecte aucune région, et chaque affectation portait
        « réserves : Agence différente de la région déclarée »."""
        silencieux = _candidat("r", region_demande="", agence_responsable="Douala")
        verdict = evaluer_candidature(silencieux, grille, LE_JOUR)
        assert "Agence différente de la région déclarée" not in verdict.motifs, verdict

    def test_une_region_declaree_ailleurs_signale_toujours(self, grille):
        """La contre-épreuve : le critère n'a pas été éteint, il a cessé de mentir."""
        loin = _candidat("r", region_demande="Garoua", agence_responsable="Douala")
        verdict = evaluer_candidature(loin, grille, LE_JOUR)
        assert "Agence différente de la région déclarée" in verdict.motifs, verdict

    def test_elle_est_triee_par_code(self, grille):
        """Un ordre stable : le motif conservé pour audit énumère les critères
        déclenchés, et deux affectations identiques doivent produire le même
        texte."""
        assert [r.code for r in grille] == sorted(r.code for r in grille)

    def test_chaque_critere_porte_un_fondement(self, grille):
        """Un responsable de pôle à qui l'on refuse un dossier doit pouvoir lire
        pourquoi, et sur quelle décision du centre cela repose."""
        for regle in grille:
            assert regle.fondement.texte.strip()
            assert regle.fondement.source.strip()

    def test_aucun_critere_ne_cite_un_fait_inconnu(self, grille):
        """Déjà vérifié au chargement. Répété ici parce que c'est **le** défaut
        silencieux du mécanisme : JSONLogic rend `None` pour un chemin absent, et
        le critère devient toujours faux sans que rien ne le dise."""
        from app.moteur.chemins import chemins_cites

        for regle in grille:
            assert SCHEMA_AFFECTATION.inconnus(chemins_cites(regle.predicat)) == ()

    def test_la_competence_et_la_disponibilite_sont_redhibitoires(self, grille):
        redhibitoires = {r.code for r in grille if r.redhibitoire}
        assert redhibitoires == {"AFF-CMP-001", "AFF-DIS-001"}

    def test_un_service_sans_exigence_ne_bloque_personne(self, grille):
        """La plupart des prestations courantes n'exigent aucune habilitation.
        Un critère de compétence qui les bloquerait viderait la grille de tout
        candidat sur les demandes les plus fréquentes."""
        sans_exigence = _candidat(
            "resp-1", competences=(), competence_requise="", service="tenue-comptable"
        )
        assert evaluer_candidature(sans_exigence, grille, LE_JOUR).eligible

    def test_le_proche_disponible_et_leger_l_emporte(self, grille):
        candidats = [
            _candidat("awono"),
            _candidat("bikoi", agence_responsable="Yaounde"),
            _candidat("ceval", competences=("tenue",)),
            _candidat("denis", disponible=False),
        ]
        choix = choisir(candidats, grille, LE_JOUR)
        assert choix.responsable == "awono"
        assert choix.penalite == 0

    def test_quand_tout_le_monde_est_ecarte_personne_n_est_retenu(self, grille):
        candidats = [
            _candidat("ceval", competences=("tenue",)),
            _candidat("denis", disponible=False),
        ]
        assert choisir(candidats, grille, LE_JOUR) is None


# ── Le cas d'usage ────────────────────────────────────────────────────────────


class TestAffecterLeDossier:
    @pytest.fixture(scope="class")
    def grille(self):
        return charger_la_grille_d_affectation(GRILLE_REELLE)

    def test_le_dossier_passe_a_affectee_avec_son_motif(self, grille):
        resultat = affecter_le_dossier(
            _dossier(), [_candidat("awono")], grille, L_INSTANT
        )
        assert resultat.affecte is True
        assert resultat.dossier.etat is EtatDossier.AFFECTEE
        assert resultat.dossier.responsable == "awono"
        assert resultat.dossier.motif_affectation == resultat.choix.motif

    def test_personne_ne_convient_laisse_le_dossier_depose(self, grille):
        """Le dossier qui reste DÉPOSÉE **est** le signalement : la veille des
        deux heures le remontera. Inventer un état « en file de pôle » le ferait
        sortir du champ de l'alerte qui doit précisément le voir."""
        resultat = affecter_le_dossier(
            _dossier(), [_candidat("denis", disponible=False)], grille, L_INSTANT
        )
        assert resultat.affecte is False
        assert resultat.dossier.etat is EtatDossier.DEPOSEE
        assert resultat.dossier.responsable is None

    def test_les_empechements_disent_ce_qui_manquait(self, grille):
        """« Aucun responsable disponible » ne dit pas quoi faire. Les
        empêchements distincts disent s'il manque une compétence ou des bras."""
        resultat = affecter_le_dossier(
            _dossier(),
            [
                _candidat("denis", disponible=False),
                _candidat("ceval", competences=("tenue",)),
            ],
            grille,
            L_INSTANT,
        )
        assert set(resultat.empechements) == {
            "Responsable indisponible",
            "Compétence exigée par le service non détenue",
        }

    def test_sans_aucun_candidat_le_dossier_reste_intact(self, grille):
        resultat = affecter_le_dossier(_dossier(), [], grille, L_INSTANT)
        assert resultat.affecte is False
        assert resultat.verdicts == ()
        assert resultat.empechements == ()

    def test_les_verdicts_expliquent_pourquoi_pas_untel(self, grille):
        """Répondre sans rejouer, c'est ce que le classement complet permet."""
        resultat = affecter_le_dossier(
            _dossier(),
            [_candidat("awono"), _candidat("bikoi", agence_responsable="Yaounde")],
            grille,
            L_INSTANT,
        )
        ecarte = next(v for v in resultat.verdicts if v.responsable == "bikoi")
        assert ecarte.motifs == ("Agence différente de la région déclarée",)

    def test_une_grille_cassee_remonte_jusqu_au_resultat(self):
        """Une grille cassée doit se voir au moment où elle route, et non le jour
        où l'on cherchera pourquoi tout le monde a atterri chez la même
        personne."""
        resultat = affecter_le_dossier(
            _dossier(),
            [_candidat("awono")],
            [_regle("AFF-CASSE", {"operateur-inexistant": [1, 2]})],
            L_INSTANT,
        )
        assert resultat.echecs
        assert "AFF-CASSE" in resultat.echecs[0]

    def test_la_date_de_vigueur_se_separe_de_l_instant(self, grille):
        """Rejouer avec la grille d'aujourd'hui donnerait la bonne réponse
        d'aujourd'hui à une question d'il y a six mois."""
        future = [
            _regle(
                "AFF-FUTUR",
                {"==": [{"var": "meme_agence"}, True]},
                applicable_du=date(2027, 1, 1),
                penalite=99,
            )
        ]
        loin = [_candidat("bikoi", agence_responsable="Yaounde")]
        aujourd_hui = affecter_le_dossier(_dossier(), loin, future, L_INSTANT)
        plus_tard = affecter_le_dossier(
            _dossier(), loin, future, L_INSTANT, a_la_date=date(2027, 6, 1)
        )
        assert aujourd_hui.choix.penalite == 0
        assert plus_tard.choix.penalite == 99


class TestReaffecterLeDossier:
    @pytest.fixture(scope="class")
    def grille(self):
        return charger_la_grille_d_affectation(GRILLE_REELLE)

    def _affecte(self, grille, candidats):
        return affecter_le_dossier(_dossier(), candidats, grille, L_INSTANT).dossier

    def test_elle_ecarte_celui_qui_n_a_pas_rappele(self, grille):
        """Sans cette exclusion, la grille redésignerait le même : rien n'a
        changé dans ses critères, et la réaffectation tournerait en rond en
        consommant ses trois tours."""
        candidats = [_candidat("awono"), _candidat("bikoi")]
        dossier = self._affecte(grille, candidats)
        assert dossier.responsable == "awono"

        repris = reaffecter_le_dossier(
            dossier, candidats, grille, L_INSTANT + timedelta(hours=24)
        )
        assert repris.dossier.responsable == "bikoi"
        assert repris.dossier.reaffectations == 1

    def test_elle_ne_remet_pas_la_prise_en_charge_a_zero(self, grille):
        candidats = [_candidat("awono"), _candidat("bikoi")]
        dossier = self._affecte(grille, candidats)
        repris = reaffecter_le_dossier(
            dossier, candidats, grille, L_INSTANT + timedelta(hours=24)
        )
        assert repris.dossier.affecte_le == L_INSTANT

    def test_seul_candidat_il_n_y_a_personne_a_qui_passer_la_main(self, grille):
        """Le dossier reste chez lui, et l'alerte de veille continue de le
        remonter. Le vider de son responsable le rendrait invisible à la fois de
        lui et de la file."""
        candidats = [_candidat("awono")]
        dossier = self._affecte(grille, candidats)
        repris = reaffecter_le_dossier(
            dossier, candidats, grille, L_INSTANT + timedelta(hours=24)
        )
        assert repris.affecte is False
        assert repris.dossier.responsable == "awono"
        assert repris.dossier.reaffectations == 0


class TestLaRepartitionSousLeSeuil:
    """⚠️ **Le critère de charge n'avait aucun effet en dessous de soixante points.**

    ─────────────────────────────────────────────────────────────────────────────
    La grille du centre pénalise la charge **par seuils** : vingt points au-delà
    de soixante, quarante de plus au-delà de cent vingt. Le moteur valorise une
    pénalité fixe, pas une pénalité proportionnelle, et exprimer une progression
    fine demanderait douze règles.

    Conséquence : en dessous de soixante, tous les candidats obtenaient la même
    pénalité, et le classement tombait sur l'identifiant. **Mesuré : vingt
    dossiers, quatre collaborateurs équivalents et vides, vingt dossiers pour le
    premier dans l'ordre alphabétique.**

    Le document de conception écrit « l'affectation choisit le moins chargé ».
    C'était faux pour les soixante premiers points de chacun, c'est-à-dire pour
    toute la vie d'un cabinet qui démarre.

    Le correctif est une clé de tri, pas une règle : les seuils restent le
    jugement du centre, la répartition est une propriété du classement.
    ─────────────────────────────────────────────────────────────────────────────
    """

    EQUIPE = ("alpha", "beta", "gamma", "delta")

    @pytest.fixture
    def grille(self) -> list[RegleDAffectation]:
        """La grille réelle du centre, et non des règles forgées pour le test.

        ⚠️ Ce sont ses **seuils** qui produisaient le défaut : une grille inventée
        avec des pénalités proportionnelles n'aurait rien montré.
        """
        return charger_la_grille_d_affectation(GRILLE_REELLE)

    def _candidats(self, charge: dict[str, int]) -> list[Candidature]:
        return [
            Candidature(
                responsable=n,
                service="creation-sarl",
                competences=("CHARGE_FORMALITES",),
                competence_requise="CHARGE_FORMALITES",
                dossiers_ouverts=charge.get(n, 0),
                charge_ponderee=Decimal(charge.get(n, 0)),
                disponible=True,
            )
            for n in self.EQUIPE
        ]

    def test_vingt_dossiers_se_repartissent_au_lieu_d_aller_au_premier(self, grille):
        from collections import Counter

        charge: Counter[str] = Counter()
        for _ in range(20):
            choix = choisir(self._candidats(charge), grille, LE_JOUR)
            assert choix is not None
            charge[choix.responsable] += 1

        assert dict(charge) == {n: 5 for n in self.EQUIPE}, dict(charge)

    def test_le_moins_charge_gagne_meme_sans_franchir_aucun_seuil(self, grille):
        """La propriété, isolée. Trois points d'écart, aucun seuil franchi."""
        choix = choisir(
            self._candidats({"alpha": 3, "beta": 1, "gamma": 2, "delta": 4}),
            charger_la_grille_d_affectation(GRILLE_REELLE),
            LE_JOUR,
        )
        assert choix is not None
        assert choix.responsable == "beta"

    def test_une_penalite_de_grille_prime_toujours_sur_la_charge(self, grille):
        """⚠️ La contre-épreuve, et elle est essentielle.

        La charge départage **à préférence égale**, elle ne renverse pas le
        jugement du centre. Un collaborateur vide mais éloigné ne doit pas passer
        devant un collaborateur chargé de la bonne agence : la proximité vaut
        trente points, et trente points ne se rattrapent pas en étant moins
        chargé.
        """
        proche_charge = Candidature(
            responsable="alpha",
            service="creation-sarl",
            region_demande="douala",
            agence_responsable="douala",
            competences=("CHARGE_FORMALITES",),
            competence_requise="CHARGE_FORMALITES",
            dossiers_ouverts=40,
            charge_ponderee=Decimal(40),
            disponible=True,
        )
        loin_et_vide = Candidature(
            responsable="beta",
            service="creation-sarl",
            region_demande="douala",
            agence_responsable="yaounde",
            competences=("CHARGE_FORMALITES",),
            competence_requise="CHARGE_FORMALITES",
            dossiers_ouverts=0,
            charge_ponderee=Decimal(0),
            disponible=True,
        )
        choix = choisir([proche_charge, loin_et_vide], grille, LE_JOUR)
        assert choix is not None
        assert choix.responsable == "alpha"

    def test_l_identifiant_departage_encore_a_charge_egale(self, grille):
        """La reproductibilité n'a pas été perdue en route.

        ⚠️ Sans dernière clé, deux candidats à charge et pénalité égales seraient
        départagés par l'ordre dans lequel la base a rendu ses lignes, qu'aucune
        base ne garantit.
        """
        endroit = self._candidats({})
        envers = list(reversed(endroit))

        assert choisir(endroit, grille, LE_JOUR).responsable == "alpha"
        assert choisir(envers, grille, LE_JOUR).responsable == "alpha"
