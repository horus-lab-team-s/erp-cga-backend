"""La reprise : passer la main, et ne jamais tourner en rond.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES CAS GARDENT

La reprise est **le seul geste que la plateforme s'autorise sans qu'un humain le
demande**. Elle mérite donc plus de gardes que les autres, et pas moins.

⚠️ Le défaut central a été mesuré avant d'être corrigé : `reaffecter` n'écartait
que le titulaire du moment, et la grille choisit le moins chargé. Celui qui vient
de rendre le dossier redevient aussitôt le moins chargé. Sur **cinq**
collaborateurs équivalents, les trois reprises allaient à **deux** d'entre eux —
alpha, beta, alpha, beta — les trois autres n'étant jamais sollicités.

Le refus du domaine nommait pourtant le symptôme depuis le premier jour : « au-delà,
la règle d'affectation tourne en rond ». Elle tournait en rond bien avant la limite.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotDossiersMemoire,
)
from app.contextes.souscription.adaptateurs.sortant.regle_de_reprise import (
    charger_la_regle_de_reprise,
)
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.souscription.application.affectation import Candidature
from app.contextes.souscription.application.reprise_des_dossiers import (
    NOM_REPRIS,
    reprendre_les_dossiers,
)
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    REAFFECTATIONS_MAXIMALES,
    EtatDossier,
    ouvrir_un_dossier,
)
from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire

T0 = datetime(2026, 9, 1, 9, 0)
REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"

#: ⚠️ **La grille réelle du centre**, et non des règles forgées pour le test.
#: Une grille inventée éprouverait le mécanisme sans rien dire de ce que la
#: plateforme fera réellement le jour où elle passera la main toute seule.
REGLES = charger_la_grille_d_affectation(REFERENTIEL / "affectation")


def _demande(rang: int = 1) -> DemandeDeContact:
    return DemandeDeContact(
        identifiant=f"D{rang}",
        deposee_le=T0,
        nom="Abena Ndzana",
        telephone=f"6991122{rang:02d}",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(
            accorde=False, recueilli_le=T0, version_du_texte="v1"
        ),
    )


def _affecte(reference="DOS-1", a="alpha", *, quand=T0):
    return ouvrir_un_dossier(reference, _demande()).affecter(
        a, quand, motif="tour de rôle"
    )


def _equipe(porteur: str | None, *noms: str) -> list[Candidature]:
    """Des collaborateurs équivalents : seul le porteur actuel porte un dossier."""
    return [
        Candidature(
            responsable=n,
            service="creation-sarl",
            competences=("CHARGE_FORMALITES",),
            competence_requise="CHARGE_FORMALITES",
            dossiers_ouverts=1 if n == porteur else 0,
            charge_ponderee=Decimal(1 if n == porteur else 0),
            disponible=True,
        )
        for n in noms
    ]


@pytest.fixture
def depot() -> DepotDossiersMemoire:
    return DepotDossiersMemoire()


@pytest.fixture
def boite() -> BoiteDEnvoiMemoire:
    return BoiteDEnvoiMemoire()


def _reprendre(depot, boite, quand, *, equipe=("alpha", "beta", "gamma"), delai_h=24):
    return reprendre_les_dossiers(
        lambda service: _equipe(
            depot.ouverts(etat=EtatDossier.AFFECTEE)[0].responsable
            if depot.ouverts(etat=EtatDossier.AFFECTEE)
            else None,
            *equipe,
        ),
        REGLES,
        dossiers=depot,
        boite=boite,
        a_l_instant=quand,
        delai_depasse=lambda d: quand - d.depuis_le > timedelta(hours=delai_h),
        identifiant=lambda suffixe: f"reprise-{suffixe}",
    )


class TestQuandLaMainEstReprise:
    def test_un_dossier_dans_le_delai_n_est_pas_touche(self, depot, boite):
        """⚠️ La contre-épreuve du cas suivant. Sans elle, une reprise qui
        s'appliquerait à tout passerait pour correcte."""
        depot.enregistrer(_affecte())
        rapport = _reprendre(depot, boite, T0 + timedelta(hours=23))

        assert rapport.repris == ()
        assert rapport.examines == 0
        assert depot.lire("DOS-1").responsable == "alpha"
        assert boite.tous() == []

    def test_au_dela_du_delai_la_main_passe_a_un_autre(self, depot, boite):
        depot.enregistrer(_affecte())
        rapport = _reprendre(depot, boite, T0 + timedelta(hours=25))

        assert rapport.repris == ("DOS-1",)
        repris = depot.lire("DOS-1")
        assert repris.responsable == "beta"
        assert repris.reaffectations == 1
        assert repris.responsables_passes == ("alpha",)

    def test_l_anciennete_repart_de_zero_pour_le_nouveau(self, depot, boite):
        """⚠️ **C'est ce qui donne son sens à l'alerte de la veille.**

        La veille signale `AFFECTÉE` à 48 heures, la reprise agit à 24. Comme la
        reprise remet `depuis_le` à l'instant du passage de main, un dossier
        n'atteint jamais les 48 heures tant que la reprise aboutit. L'alerte
        cesse de dire « personne n'a rappelé » et dit « la machine a essayé de
        passer la main et n'a pas pu ».
        """
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=25)
        _reprendre(depot, boite, tard)

        repris = depot.lire("DOS-1")
        assert repris.depuis_le == tard
        # `affecte_le` ne bouge pas : c'est la date de prise en charge par le
        # cabinet, et la remettre à zéro effacerait le retard qu'on mesure.
        assert repris.affecte_le == T0

    def test_l_evenement_dit_de_qui_la_main_a_ete_reprise(self, depot, boite):
        depot.enregistrer(_affecte())
        _reprendre(depot, boite, T0 + timedelta(hours=25))

        (evenement,) = boite.tous()
        assert evenement.nom == NOM_REPRIS
        assert evenement.cle == "DOS-1"
        assert evenement.charge["precedent"] == "alpha"
        assert evenement.charge["responsable"] == "beta"
        assert evenement.charge["reprise_numero"] == 1
        assert evenement.charge["reprises_restantes"] == REAFFECTATIONS_MAXIMALES - 1

    def test_l_evenement_ne_transporte_aucune_donnee_de_prospect(self, depot, boite):
        """Ni le nom, ni le numéro : un événement traverse une file, des journaux
        et des sauvegardes, qui le recopient tous."""
        depot.enregistrer(_affecte())
        _reprendre(depot, boite, T0 + timedelta(hours=25))

        contenu = str(boite.tous()[0].charge)
        assert "Abena" not in contenu
        assert "699112201" not in contenu


class TestLeVaEtVientQuiConsommaitLesTroisTours:
    """⚠️ **Le défaut mesuré, retourné en garde.**"""

    def _tourner(self, depot, boite, *equipe: str):
        touches = {"alpha"}
        quand = T0
        for _ in range(REAFFECTATIONS_MAXIMALES + 1):
            quand += timedelta(hours=25)
            rapport = _reprendre(depot, boite, quand, equipe=equipe)
            if not rapport.repris:
                break
            touches.add(depot.lire("DOS-1").responsable)
        return touches, depot.lire("DOS-1")

    def test_cinq_collaborateurs_font_quatre_mains_et_non_deux(self, depot, boite):
        """Avant la correction : alpha, beta, alpha, beta. Trois tours consommés,
        deux personnes touchées, trois jamais sollicitées."""
        depot.enregistrer(_affecte())
        touches, dossier = self._tourner(
            depot, boite, "alpha", "beta", "gamma", "delta", "epsilon"
        )

        assert len(touches) == 4, touches
        assert dossier.reaffectations == REAFFECTATIONS_MAXIMALES
        # Déterminé, et pas seulement « varié » : écartés en dernier, pénalité
        # croissante, puis identifiant croissant. `delta` précède `gamma`.
        assert dossier.responsables_passes == ("alpha", "beta", "delta")
        assert dossier.responsable == "epsilon"

    def test_a_deux_collaborateurs_un_seul_tour_est_consomme(self, depot, boite):
        """⚠️ Et c'est mieux que d'en brûler trois entre les deux mêmes.

        Les deux reprises restantes demeurent disponibles pour un arbitrage
        humain, et la veille remontera le dossier à 48 heures.
        """
        depot.enregistrer(_affecte())
        touches, dossier = self._tourner(depot, boite, "alpha", "beta")

        assert touches == {"alpha", "beta"}
        assert dossier.reaffectations == 1
        assert dossier.responsable == "beta"

    def test_le_domaine_refuse_de_rendre_le_dossier_a_quelqu_un_qui_l_a_deja_eu(self):
        """La garde vit au domaine, pas seulement dans le filtrage de l'appelant.

        ⚠️ Filtrer dans `reaffecter_le_dossier` est une économie : on ne monte pas
        un classement pour écarter ensuite. Le refus du domaine est la vérité, et
        il tient même si un appelant futur oublie de filtrer.
        """
        from app.contextes.souscription.domaine.dossier_commercial import (
            TransitionDossierRefusee,
        )

        dossier = _affecte().reaffecter("beta", T0, motif="reprise")
        with pytest.raises(TransitionDossierRefusee, match="déjà tenu"):
            dossier.reaffecter("alpha", T0, motif="retour en arrière")


class TestQuandLaReprisNAboutitPas:
    def test_sans_repreneur_rien_n_est_touche_et_rien_n_est_signale(
        self, depot, boite
    ):
        """⚠️ **Aucune alerte ici, et c'est délibéré.**

        C'est la veille qui remontera le dossier à 48 heures. Deux mécanismes qui
        alerteraient sur le même silence produiraient deux alertes pour un fait,
        et celle qu'on lit finirait par être celle qu'on croit.
        """
        depot.enregistrer(_affecte())
        rapport = _reprendre(depot, boite, T0 + timedelta(hours=25), equipe=("alpha",))

        assert rapport.repris == ()
        assert rapport.sans_repreneur == 1
        assert boite.tous() == []
        assert depot.lire("DOS-1").responsable == "alpha"
        # Intact : ni l'état, ni l'ancienneté, donc la veille le verra dormir.
        assert depot.lire("DOS-1").depuis_le == T0

    def test_un_dossier_a_la_limite_n_est_meme_plus_examine(self, depot, boite):
        """Il sort de la file de travail, et le rapport le dit plutôt que de le
        taire : c'est ce nombre qui apprend qu'ils sont là."""
        dossier = _affecte()
        for suivant in ("beta", "gamma", "delta"):
            dossier = dossier.reaffecter(suivant, T0, motif="reprise")
        depot.enregistrer(dossier)

        rapport = _reprendre(depot, boite, T0 + timedelta(hours=25))

        assert rapport.examines == 0
        assert rapport.limite_atteinte == 1
        assert boite.tous() == []

    def test_un_dossier_en_conversation_n_est_jamais_repris(self, depot, boite):
        """⚠️ Reprendre un dossier dont l'échange a commencé couperait une
        conversation en cours, ce qu'aucun délai ne justifie."""
        depot.enregistrer(_affecte().premier_contact(T0))

        rapport = _reprendre(depot, boite, T0 + timedelta(days=90))

        assert rapport.examines == 0
        assert depot.lire("DOS-1").responsable == "alpha"


class TestLaRegleDuReferentiel:
    def test_le_referentiel_reel_autorise_le_geste(self):
        """L'état des lieux, et la contre-épreuve du cas suivant.

        ⚠️ Sans lui, un travail qui ne reprendrait jamais rien passerait le cas
        d'à côté sans broncher : « coupé » et « cassé » rendent le même silence.
        """
        regle = charger_la_regle_de_reprise(REFERENTIEL / "acquisition")
        assert regle.actif is True
        assert regle.apres == timedelta(hours=24)

    def test_le_centre_peut_couper_le_geste_sans_redeploiement(
        self, monkeypatch, tmp_path, depot, boite
    ):
        """⚠️ **Le seul geste automatique de la plateforme doit pouvoir s'arrêter
        par un fichier**, et non par un redéploiement : couper une réaffectation
        qu'on vient de juger mauvaise ne peut pas attendre une mise en production.

        Le cas passe par le **travail** et non par le balayage, parce que c'est le
        travail qui lit le drapeau. Éprouver le balayage ne dirait rien de la
        lecture, et c'est la lecture qui peut être oubliée.
        """
        from app.contextes.souscription.adaptateurs.entrant import travail_de_reprise
        from app.infrastructure import config

        (tmp_path / "acquisition").mkdir()
        (tmp_path / "acquisition" / "reprise.yaml").write_text(
            "actif: false\napres_heures: 24\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            config.configuration(), "dossier_referentiel", tmp_path, raising=False
        )

        depot.enregistrer(_affecte())
        compte_rendu = travail_de_reprise.reprendre(boite)

        # ⚠️ Un compte rendu explicite, et non un passage muet : « rien fait » et
        # « coupé au référentiel » se ressemblent dans un journal, et c'est la
        # première chose qu'un exploitant cherche quand plus rien ne bouge.
        assert "coupée au référentiel" in compte_rendu
        assert boite.tous() == []

    def test_le_delai_reel_est_plus_court_que_celui_de_la_veille(self):
        """⚠️ La propriété qui rend l'alerte lisible, vérifiée sur les fichiers
        réels du centre et non sur des valeurs de test.

        Si la veille devenait plus courte que la reprise, elle alerterait sur des
        dossiers que la machine est en train de traiter.
        """
        from app.contextes.souscription.adaptateurs.sortant.delais_de_veille import (
            charger_les_delais_de_veille,
        )

        reprise = charger_la_regle_de_reprise(REFERENTIEL / "acquisition")
        veille = charger_les_delais_de_veille(REFERENTIEL / "acquisition")

        assert reprise.apres < veille[EtatDossier.AFFECTEE]


class TestLeRapport:
    def test_il_distingue_ce_qui_dort_de_ce_qui_n_a_pas_ete_lu(self, depot, boite):
        vide = _reprendre(depot, boite, T0)
        assert (vide.examines, vide.repris) == (0, ())

        depot.enregistrer(_affecte())
        rapport = _reprendre(depot, boite, T0 + timedelta(hours=25))
        assert rapport.examines == 1
        assert "1 repris" in rapport.resume


class TestPlusieursDossiersDansUnMemePassage:
    """⚠️ **La charge est relue à chaque dossier, jamais une fois par passage.**

    ─────────────────────────────────────────────────────────────────────────────
    C'est écrit dans `travail_de_reprise` et dans `reprendre_les_dossiers`, et
    aucun cas ne le mesurait : une mutation qui remplaçait la charge réelle par un
    dictionnaire vide survivait à toute la suite.

    Elle survivait parce que **tous les cas ne reprenaient qu'un dossier**, et la
    propriété ne se voit qu'à partir de deux : un collaborateur qui vient de
    recevoir un dossier repris doit être plus chargé pour le suivant.

    Sans cela, trois dossiers repris dans le même passage iraient au même, et
    précisément parce qu'il était le moins chargé au début.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def _candidatures_reelles(self, depot, *noms: str):
        """Montées depuis la charge que le dépôt porte **à l'instant de l'appel**."""

        def pour(_service: str):
            charge = depot.charge_par_responsable()
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
                for n in noms
            ]

        return pour

    def test_deux_dossiers_repris_ne_vont_pas_au_meme(self, depot, boite):
        depot.enregistrer(_affecte("DOS-1", "alpha"))
        depot.enregistrer(_affecte("DOS-2", "alpha"))
        tard = T0 + timedelta(hours=25)

        rapport = reprendre_les_dossiers(
            self._candidatures_reelles(depot, "alpha", "beta", "gamma"),
            REGLES,
            dossiers=depot,
            boite=boite,
            a_l_instant=tard,
            delai_depasse=lambda d: tard - d.depuis_le > timedelta(hours=24),
            identifiant=lambda suffixe: f"reprise-{suffixe}",
        )

        assert len(rapport.repris) == 2
        preneurs = {depot.lire(r).responsable for r in ("DOS-1", "DOS-2")}
        assert preneurs == {"beta", "gamma"}, (
            f"les deux dossiers sont allés à {preneurs} : la charge n'a pas été "
            "relue entre les deux reprises du même passage"
        )

    def test_les_deux_alertes_ont_des_identifiants_distincts(self, depot, boite):
        """Sinon la seconde écraserait la première : le dépôt est une réécriture."""
        depot.enregistrer(_affecte("DOS-1", "alpha"))
        depot.enregistrer(_affecte("DOS-2", "alpha"))
        tard = T0 + timedelta(hours=25)

        reprendre_les_dossiers(
            self._candidatures_reelles(depot, "alpha", "beta", "gamma"),
            REGLES,
            dossiers=depot,
            boite=boite,
            a_l_instant=tard,
            delai_depasse=lambda d: tard - d.depuis_le > timedelta(hours=24),
            identifiant=lambda suffixe: f"reprise-{suffixe}",
        )

        assert len({e.identifiant for e in boite.tous()}) == 2
