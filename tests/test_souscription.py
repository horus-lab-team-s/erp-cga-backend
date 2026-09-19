"""Contexte M · Souscription — offre, devis, encaissement, ouverture d'accès.

Ces tests portent d'abord sur les propriétés qui protègent l'argent : un
encaissement ne doit jamais être compté deux fois, ni perdu, ni activé pour un
montant qui ne correspond pas. Chacun devrait pouvoir être lu à voix haute devant
le cabinet : « rejouer la notification n'ouvre pas un second compte »,
« l'encaissement dont la notification s'est perdue est repêché », « un montant
différent est refusé, pas absorbé ».
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_http import (
    reinitialiser_comptoir,
)
from app.contextes.souscription.api import (
    CATALOGUE,
    DELAI_EXPIRATION,
    DemandeLigne,
    DepotDevisMemoire,
    DepotPaiementsMemoire,
    DepotServicesMemoire,
    DepotSouscriptionsMemoire,
    Devis,
    DevisImpossible,
    EngagementRefuse,
    EtatDevis,
    EtatSouscription,
    EvenementPaiement,
    FournisseurTara,
    Initiation,
    MontantIncoherent,
    NatureService,
    Paiement,
    Periodicite,
    Prospect,
    Service,
    ServiceIntrouvable,
    StatutPaiement,
    StrategieRapprochement,
    Tarif,
    TarifIndisponible,
    engager,
    etablir_devis,
    nouvelle_cle_idempotence,
    rapprocher,
    reconcilier,
    service_par_code,
    traiter_notification,
)
from app.contextes.transverse.adaptateurs.entrant.dependances import (
    atelier,
    reinitialiser_atelier,
)
from app.contextes.transverse.api import (
    JournalAuditMemoire,
    Role,
    roles_au,
    verifier_chaine,
)
from app.main import creer_application
from app.partage.horloge import horloge_figee
from app.partage.telephone import NumeroInvalide, normaliser_telephone

INSTANT = datetime(2026, 8, 15, 10, 0)
AUJOURD_HUI = INSTANT.date()
NIU = "M081234567890P"


# ── Outillage ────────────────────────────────────────────────────────────────


def _prospect(**remplace) -> Prospect:
    defauts = dict(
        nom="ATANGANA",
        prenom="Sylvie",
        courriel="s.atangana@boulangerie-nkolbisson.cm",
        telephone="699 11 22 33",
        denomination="BOULANGERIE DE NKOLBISSON",
        niu=NIU,
        chiffre_affaires_declare=Decimal(28_000_000),
    )
    return Prospect(**{**defauts, **remplace})


class _AccesFactice:
    """Réalisation de `ServiceOuvertureAcces` qui compte ses appels.

    Compter est le point : ce qu'on veut prouver n'est pas qu'un compte est
    créé, c'est qu'il n'en est créé **qu'un seul** quand la notification est
    rejouée.
    """

    def __init__(self, *, casse: bool = False) -> None:
        self.appels = 0
        self.casse = casse

    def ouvrir(self, souscription, a_l_instant) -> str:
        self.appels += 1
        if self.casse:
            raise RuntimeError("service de courriel indisponible")
        return f"A-{self.appels:03d}"


class _FournisseurFactice:
    """Réalisation de `FournisseurPaiement` pilotée par le test."""

    def __init__(
        self,
        *,
        accepte: bool = True,
        statut: EvenementPaiement | None = None,
        marchand: str | None = "CGA-TEST",
    ) -> None:
        self.accepte = accepte
        self.statut = statut
        self.marchand = marchand
        self.initiations: list[str] = []

    def initier(self, *, montant, telephone, cle_idempotence, libelle):
        self.initiations.append(cle_idempotence)
        return Initiation(
            accepte=self.accepte,
            reference_externe=f"REF-{cle_idempotence[:8]}" if self.accepte else None,
            message=None if self.accepte else "solde insuffisant",
        )

    def interroger_statut(self, *, cle_idempotence, reference_externe):
        return self.statut

    def lire_notification(self, charge_utile):
        return charge_utile.get("_evenement")

    def identifiant_marchand(self):
        return self.marchand


@pytest.fixture
def atelier_m():
    """Les quatre dépôts de M, plus le journal d'audit et l'ouverture d'accès."""
    return {
        "services": DepotServicesMemoire(),
        "devis": DepotDevisMemoire(),
        "souscriptions": DepotSouscriptionsMemoire(),
        "paiements": DepotPaiementsMemoire(),
        "journal": JournalAuditMemoire(),
        "acces": _AccesFactice(),
    }


def _devis(atelier_m, codes=("ADHESION",), prospect=None) -> Devis:
    devis = etablir_devis(
        reference="DV-1",
        prospect=prospect or _prospect(),
        demandes=[DemandeLigne(service=code) for code in codes],
        services=atelier_m["services"].charger(AUJOURD_HUI),
        a_la_date=AUJOURD_HUI,
    )
    atelier_m["devis"].enregistrer(devis)
    return devis


def _engager(atelier_m, fournisseur=None, reference="DV-1"):
    return engager(
        reference,
        reference_souscription="SO-1",
        identifiant_paiement="PM-1",
        cle_idempotence="cle-idempotence-0001",
        devis=atelier_m["devis"],
        souscriptions=atelier_m["souscriptions"],
        paiements=atelier_m["paiements"],
        services=atelier_m["services"],
        fournisseur=fournisseur or _FournisseurFactice(),
        journal=atelier_m["journal"],
        a_l_instant=INSTANT,
    )


def _suites(atelier_m):
    """La table des suites, montée comme le comptoir la monte.

    ⚠️ Seule celle de la souscription est câblée ici : ces cas éprouvent le flux
    des abonnements. La suite du parcours d'acquisition a ses propres cas, et une
    table à demi montée dirait justement ce qui arrive quand une nature n'a pas de
    suite — c'est un cas, pas un accident.
    """
    from app.contextes.souscription.api import NaturePaiement, SuiteDeSouscription

    return {
        NaturePaiement.SOUSCRIPTION: SuiteDeSouscription(
            souscriptions=atelier_m["souscriptions"],
            acces=atelier_m["acces"],
            journal=atelier_m["journal"],
        )
    }


def _verifier(atelier_m, reference="SO-1", a_l_instant=INSTANT):
    """Le geste du cabinet qui ouvre l'accès, après vérification d'identité (pas 83).

    ⚠️ Depuis le pas 83, un encaissement n'ouvre plus d'accès à un dossier : un inconnu
    pouvait payer avec le NIU d'une autre entreprise et lire son dossier. Les cas qui
    éprouvent l'ouverture passent donc par ce geste, comme en service.
    """
    from app.contextes.souscription.api import VerificationDIdentite, activer_souscription

    return activer_souscription(
        reference,
        verification=VerificationDIdentite(
            par="C-002", comment="RCCM et CNI du gérant présentés au cabinet ce jour."
        ),
        souscriptions=atelier_m["souscriptions"],
        acces=atelier_m["acces"],
        journal=atelier_m["journal"],
        a_l_instant=a_l_instant,
    )


def _notifier(atelier_m, evenement, fournisseur=None, a_l_instant=INSTANT):
    return traiter_notification(
        {"_evenement": evenement},
        fournisseur=fournisseur or _FournisseurFactice(),
        paiements=atelier_m["paiements"],
        suites=_suites(atelier_m),
        journal=atelier_m["journal"],
        a_l_instant=a_l_instant,
    )


# ── Le numéro camerounais ────────────────────────────────────────────────────


class TestTelephone:
    @pytest.mark.parametrize(
        "brut",
        [
            "699112233",
            "+237699112233",
            "237 699 11 22 33",
            "00237-699-11-22-33",
            "0699112233",
            "(237) 699.11.22.33",
        ],
    )
    def test_six_ecritures_du_meme_numero(self, brut: str):
        """Le rapprochement par téléphone échouerait sur un format qui diffère
        d'un préfixe, et l'argent resterait non affecté."""
        assert normaliser_telephone(brut) == "+237699112233"

    def test_un_fixe_est_accepte(self):
        assert normaliser_telephone("233 42 15 15") == "+237233421515"

    @pytest.mark.parametrize("brut", ["12345", "899112233", "", "abcdefghi"])
    def test_un_numero_inexploitable_est_refuse(self, brut: str):
        """Lever plutôt que d'enregistrer tel quel : un numéro qu'on n'a pas su
        normaliser est un numéro sur lequel aucun rapprochement ne marchera."""
        with pytest.raises(NumeroInvalide):
            normaliser_telephone(brut)


# ── L'offre ──────────────────────────────────────────────────────────────────


class TestOffre:
    def test_le_barème_se_lit_a_une_date(self):
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        libératoire = adhesion.formule_pour(Decimal(28_000_000))
        assert libératoire.tarif_au(date(2025, 6, 1)).montant == Decimal(10_000)
        assert libératoire.tarif_au(date(2026, 6, 1)).montant == Decimal(12_500)

    def test_la_borne_haute_du_tarif_est_exclue(self):
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        formule = adhesion.formule_pour(Decimal(0))
        assert formule.tarif_au(date(2025, 12, 31)).montant == Decimal(10_000)
        assert formule.tarif_au(date(2026, 1, 1)).montant == Decimal(12_500)

    def test_une_date_non_couverte_leve(self):
        """Facturer « le tarif le plus proche » reviendrait à inventer un prix."""
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        with pytest.raises(TarifIndisponible):
            adhesion.formule_pour(Decimal(0)).tarif_au(date(2019, 1, 1))

    def test_les_tranches_de_chiffre_d_affaires(self):
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        assert adhesion.formule_pour(Decimal(49_999_999)).code == "LIBERATOIRE"
        assert adhesion.formule_pour(Decimal(50_000_000)).code == "REEL_SIMPLIFIE"
        assert adhesion.formule_pour(Decimal(120_000_000)).code == "REEL"

    def test_la_tranche_haute_n_est_pas_souscriptible(self):
        """Au-delà de cent millions, l'entreprise sort du régime du centre agréé
        et la prestation change de nature : un prix affiché serait un prix faux."""
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        assert not adhesion.formule_pour(Decimal(120_000_000)).souscriptible_en_ligne

    def test_la_creation_n_est_pas_souscriptible_en_ligne(self):
        """Ses frais officiels ne sont pas au référentiel, et l'on sait déjà que
        les montants de la vitrine sont approximatifs."""
        assert not service_par_code(CATALOGUE, "CREATION").souscriptible_en_ligne
        assert service_par_code(CATALOGUE, "CREATION").nature == NatureService.SUR_ETUDE

    def test_seule_l_adhesion_ouvre_un_dossier(self):
        """Une domiciliation ou une formation payées n'ont aucune raison de
        donner accès à une comptabilité."""
        ouvrent = {s.code for s in CATALOGUE if s.ouvre_un_dossier}
        assert ouvrent == {"ADHESION"}

    def test_un_service_sans_barème_est_refuse(self):
        """Deux barèmes concurrents produiraient deux prix, et rien ne dirait
        lequel fait foi. Aucun n'en produirait aucun."""
        with pytest.raises(ValueError, match="jamais les deux ni aucun"):
            Service(
                code="X",
                libelle="X",
                nature=NatureService.PONCTUEL,
                periodicite=Periodicite.UNIQUE,
                tarifs=[],
                formules=[],
            )

    def test_un_service_a_deux_barèmes_est_refuse(self):
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        with pytest.raises(ValueError, match="jamais les deux ni aucun"):
            Service(
                code="X",
                libelle="X",
                nature=NatureService.PONCTUEL,
                periodicite=Periodicite.UNIQUE,
                tarifs=[Tarif(montant=Decimal(1), du=date(2026, 1, 1))],
                formules=adhesion.formules,
            )

    def test_un_code_inconnu_leve(self):
        with pytest.raises(ServiceIntrouvable, match="ADHESION"):
            service_par_code(CATALOGUE, "INEXISTANT")


# ── Le devis ─────────────────────────────────────────────────────────────────


class TestDevis:
    def test_le_devis_fige_le_montant(self, atelier_m):
        """Un devis reçu le 20 mars et ouvert le 2 avril doit afficher le prix de
        mars. Les lignes portent donc des montants, pas des renvois."""
        devis = _devis(atelier_m)
        assert devis.lignes[0].montant == Decimal(12_500)
        assert devis.lignes[0].formule == "LIBERATOIRE"

    def test_la_validite_est_de_trente_jours(self, atelier_m):
        devis = _devis(atelier_m)
        assert devis.valide_jusqu_au == AUJOURD_HUI + timedelta(days=30)
        assert not devis.caduc(AUJOURD_HUI + timedelta(days=30))
        assert devis.caduc(AUJOURD_HUI + timedelta(days=31))

    def test_un_abonnement_seul_encaisse_sa_premiere_echeance(self, atelier_m):
        devis = _devis(atelier_m, codes=("ADHESION",))
        assert devis.abonnement_mensuel == Decimal(12_500)
        assert devis.montant_a_regler == Decimal(12_500)

    def test_l_abonnement_ne_gonfle_pas_un_total_de_creation(self, atelier_m):
        """« Cet abonnement se règle mensuellement : il n'entre pas dans le total
        à régler à la création. » Le backend doit dire la même chose que la
        vitrine."""
        devis = _devis(atelier_m, codes=("DOMICILIATION", "ADHESION"))
        assert devis.montant_total == Decimal(192_500)
        assert devis.montant_a_regler == Decimal(180_000)
        assert devis.abonnement_mensuel == Decimal(12_500)

    def test_les_douze_mois_ne_sont_jamais_reclames(self, atelier_m):
        devis = _devis(atelier_m, codes=("ADHESION",))
        assert devis.montant_a_regler != Decimal(150_000)

    def test_une_ligne_sur_etude_figure_au_devis_sans_montant(self, atelier_m):
        devis = _devis(atelier_m, codes=("CREATION", "ADHESION"))
        creation = next(x for x in devis.lignes if x.service == "CREATION")
        assert creation.montant is None
        assert "référentiel" in (creation.precision or "")
        assert not devis.complet
        assert not devis.payable(AUJOURD_HUI)

    def test_un_devis_incomplet_n_est_pas_payable(self, atelier_m):
        devis = _devis(atelier_m, codes=("CREATION",))
        assert not devis.payable(AUJOURD_HUI)

    def test_sans_chiffre_d_affaires_la_formule_ne_se_devine_pas(self, atelier_m):
        with pytest.raises(DevisImpossible, match="chiffre d'affaires"):
            etablir_devis(
                reference="DV-X",
                prospect=_prospect(chiffre_affaires_declare=None),
                demandes=[DemandeLigne(service="ADHESION")],
                services=atelier_m["services"].charger(AUJOURD_HUI),
                a_la_date=AUJOURD_HUI,
            )

    def test_une_formule_peut_etre_imposee(self, atelier_m):
        devis = etablir_devis(
            reference="DV-X",
            prospect=_prospect(chiffre_affaires_declare=None),
            demandes=[DemandeLigne(service="ADHESION", formule="REEL_SIMPLIFIE")],
            services=atelier_m["services"].charger(AUJOURD_HUI),
            a_la_date=AUJOURD_HUI,
        )
        assert devis.lignes[0].montant == Decimal(35_000)

    def test_un_devis_sans_prestation_est_refuse(self, atelier_m):
        with pytest.raises(DevisImpossible, match="rien à chiffrer"):
            etablir_devis(
                reference="DV-X",
                prospect=_prospect(),
                demandes=[],
                services=atelier_m["services"].charger(AUJOURD_HUI),
                a_la_date=AUJOURD_HUI,
            )


# ── L'engagement ─────────────────────────────────────────────────────────────


class TestEngagement:
    def test_l_engagement_cree_la_souscription_et_le_paiement(self, atelier_m):
        _devis(atelier_m)
        souscription, paiement = _engager(atelier_m)
        assert souscription.etat == EtatSouscription.EN_ATTENTE_PAIEMENT
        assert souscription.montant == Decimal(12_500)
        assert souscription.niu == NIU
        assert paiement.statut == StatutPaiement.EN_ATTENTE
        assert paiement.telephone == "+237699112233"

    def test_l_ecriture_precede_l_appel_au_prestataire(self, atelier_m):
        """Entre un enregistrement de trop et un encaissement perdu, on choisit
        l'enregistrement de trop : le paiement existe même si l'initiation
        échoue."""
        _devis(atelier_m)
        _engager(atelier_m, _FournisseurFactice(accepte=False))
        assert atelier_m["paiements"].lire("PM-1").statut == StatutPaiement.REJETE
        assert atelier_m["souscriptions"].lire("SO-1").etat == EtatSouscription.ABANDONNEE

    def test_un_devis_ne_s_engage_pas_deux_fois(self, atelier_m):
        """Deux clics sur le bouton de paiement produiraient deux débits."""
        _devis(atelier_m)
        _engager(atelier_m)
        assert atelier_m["devis"].lire("DV-1").etat == EtatDevis.ENGAGE
        with pytest.raises(EngagementRefuse, match="second débit"):
            _engager(atelier_m)

    def test_un_devis_caduc_ne_s_engage_pas(self, atelier_m):
        devis = _devis(atelier_m)
        atelier_m["devis"].enregistrer(
            devis.model_copy(update={"valide_jusqu_au": AUJOURD_HUI - timedelta(days=1)})
        )
        with pytest.raises(EngagementRefuse, match="expiré"):
            _engager(atelier_m)

    def test_un_devis_incomplet_ne_s_engage_pas(self, atelier_m):
        _devis(atelier_m, codes=("CREATION",))
        with pytest.raises(EngagementRefuse, match="non chiffrées"):
            _engager(atelier_m)

    def test_l_adhesion_exige_un_niu(self, atelier_m):
        _devis(atelier_m, prospect=_prospect(niu=None))
        with pytest.raises(EngagementRefuse, match="NIU"):
            _engager(atelier_m)

    def test_une_formation_n_exige_pas_de_niu(self, atelier_m):
        _devis(atelier_m, codes=("FORMATION",), prospect=_prospect(niu=None))
        souscription, _ = _engager(atelier_m)
        assert souscription.niu is None
        assert souscription.montant == Decimal(75_000)

    def test_la_cle_d_idempotence_part_au_prestataire(self, atelier_m):
        _devis(atelier_m)
        fournisseur = _FournisseurFactice()
        _engager(atelier_m, fournisseur)
        assert fournisseur.initiations == ["cle-idempotence-0001"]

    def test_deux_cles_tirees_diffèrent(self):
        assert nouvelle_cle_idempotence() != nouvelle_cle_idempotence()


# ── Le rapprochement ─────────────────────────────────────────────────────────


class TestRapprochement:
    def _paiement(self, **remplace) -> Paiement:
        defauts = dict(
            identifiant="PM-1",
            reference_reglee="SO-1",
            cle_idempotence="cle-idempotence-0001",
            montant=Decimal(12_500),
            telephone="+237699112233",
            initie_le=INSTANT,
        )
        return Paiement(**{**defauts, **remplace})

    def test_premiere_strategie_notre_identifiant(self):
        paiement = self._paiement()
        evenement = EvenementPaiement(
            identifiant_produit="cle-idempotence-0001", reussi=True
        )
        trouve, strategie = rapprocher(evenement, [paiement], INSTANT)
        assert trouve is paiement
        assert strategie == StrategieRapprochement.IDENTIFIANT

    def test_deuxieme_strategie_la_reference_du_prestataire(self):
        """Le champ `productId` revient parfois vide selon le chemin emprunté
        chez l'opérateur."""
        paiement = self._paiement(reference_externe="REF-9911")
        evenement = EvenementPaiement(reference_externe="REF-9911", reussi=True)
        trouve, strategie = rapprocher(evenement, [paiement], INSTANT)
        assert trouve is paiement
        assert strategie == StrategieRapprochement.REFERENCE_EXTERNE

    def test_troisieme_strategie_le_telephone_et_la_recence(self):
        paiement = self._paiement()
        evenement = EvenementPaiement(
            telephone="699112233", montant=Decimal(12_500), reussi=True
        )
        trouve, strategie = rapprocher(evenement, [paiement], INSTANT + timedelta(minutes=10))
        assert trouve is paiement
        assert strategie == StrategieRapprochement.TELEPHONE_ET_RECENCE

    def test_la_troisieme_exige_le_meme_montant(self):
        """Sans cette condition, deux souscriptions engagées depuis le même
        téléphone dans la demi-heure se croiseraient, et la moins chère
        validerait la plus chère."""
        petit = self._paiement(identifiant="PM-1", montant=Decimal(12_500))
        grand = self._paiement(
            identifiant="PM-2", reference_reglee="SO-2", montant=Decimal(180_000)
        )
        evenement = EvenementPaiement(
            telephone="+237699112233", montant=Decimal(180_000), reussi=True
        )
        trouve, _ = rapprocher(evenement, [petit, grand], INSTANT + timedelta(minutes=1))
        assert trouve is grand

    def test_la_troisieme_expire_au_bout_d_une_demi_heure(self):
        paiement = self._paiement()
        evenement = EvenementPaiement(telephone="+237699112233", reussi=True)
        assert rapprocher(evenement, [paiement], INSTANT + timedelta(minutes=31)) is None

    def test_un_encaissement_orphelin_n_est_pas_rattache_au_hasard(self):
        """Le rattacher au hasard serait pire que de ne pas le rattacher."""
        evenement = EvenementPaiement(identifiant_produit="inconnue", reussi=True)
        assert rapprocher(evenement, [self._paiement()], INSTANT) is None

    def test_un_rejeu_retrouve_un_paiement_deja_valide(self):
        """Le lui cacher ferait passer un rejeu ordinaire pour un encaissement
        orphelin, et l'on chercherait à la main un problème qui n'existe pas."""
        valide = self._paiement(statut=StatutPaiement.VALIDE, confirme_le=INSTANT)
        evenement = EvenementPaiement(
            identifiant_produit="cle-idempotence-0001", reussi=True
        )
        trouve, _ = rapprocher(evenement, [valide], INSTANT)
        assert trouve is valide

    def test_un_numero_illisible_du_prestataire_ne_casse_rien(self):
        evenement = EvenementPaiement(telephone="???", reussi=True)
        assert evenement.telephone is None


# ── Le paiement ──────────────────────────────────────────────────────────────


class TestPaiement:
    def _paiement(self) -> Paiement:
        return Paiement(
            identifiant="PM-1",
            reference_reglee="SO-1",
            cle_idempotence="cle-idempotence-0001",
            montant=Decimal(12_500),
            telephone="+237699112233",
            initie_le=INSTANT,
        )

    def test_valider_est_rejouable(self):
        """Le prestataire renvoie plusieurs fois la même notification : son
        mécanisme de reprise l'y pousse."""
        valide = self._paiement().valider(
            INSTANT, strategie=StrategieRapprochement.IDENTIFIANT
        )
        assert valide.valider(
            INSTANT + timedelta(minutes=5), strategie=StrategieRapprochement.IDENTIFIANT
        ) is valide

    def test_un_montant_different_est_rejete_pas_absorbe(self):
        """Accepter un encaissement inférieur activerait douze mois de
        domiciliation pour cent francs."""
        with pytest.raises(MontantIncoherent, match="pas un paiement"):
            self._paiement().valider(
                INSTANT,
                strategie=StrategieRapprochement.IDENTIFIANT,
                montant_constate=Decimal(100),
            )

    def test_un_paiement_rejete_ne_se_valide_plus(self):
        rejete = self._paiement().rejeter(INSTANT, "refusé")
        with pytest.raises(ValueError, match="traité à la main"):
            rejete.valider(INSTANT, strategie=StrategieRapprochement.IDENTIFIANT)

    def test_la_peremption_est_a_vingt_quatre_heures(self):
        paiement = self._paiement()
        assert not paiement.perime(INSTANT + DELAI_EXPIRATION - timedelta(seconds=1))
        assert paiement.perime(INSTANT + DELAI_EXPIRATION)
        expire = paiement.expirer(INSTANT + DELAI_EXPIRATION)
        assert expire.statut == StatutPaiement.EXPIRE

    def test_expirer_un_paiement_valide_ne_fait_rien(self):
        valide = self._paiement().valider(
            INSTANT, strategie=StrategieRapprochement.IDENTIFIANT
        )
        assert valide.expirer(INSTANT + timedelta(days=2)) is valide


# ── L'encaissement de bout en bout ───────────────────────────────────────────


class TestEncaissement:
    def test_la_notification_encaisse_et_n_ouvre_pas_l_acces_sans_verification(self, atelier_m):
        """⚠️ Pas 83 : ce cas s'appelait « la notification ouvre l'accès », et c'était la
        faille. Un paiement ne prouve pas qu'on est l'entreprise dont on a donné le NIU."""
        _devis(atelier_m)
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(
                identifiant_produit="cle-idempotence-0001",
                reference_externe="TARA-77",
                reussi=True,
            ),
        )
        assert resultat.rapproche and not resultat.activee
        assert resultat.statut == StatutPaiement.VALIDE
        assert "vérification" in resultat.message
        souscription = atelier_m["souscriptions"].lire("SO-1")
        assert souscription.etat == EtatSouscription.PAYEE
        assert souscription.a_activer
        assert atelier_m["acces"].appels == 0, "un accès s'est ouvert sans vérification"
        actions = [e.action for e in atelier_m["journal"].lister()]
        assert "souscription.verification_requise" in actions

        _verifier(atelier_m)
        souscription = atelier_m["souscriptions"].lire("SO-1")
        assert souscription.etat == EtatSouscription.ACTIVEE
        assert souscription.compte == "A-001"
        assert souscription.prend_effet_le == AUJOURD_HUI
        activee = [e for e in atelier_m["journal"].lister() if e.action == "souscription.activee"]
        assert activee[-1].apres["verifiee_par"] == "C-002"

    def test_rejouer_la_notification_n_ouvre_pas_un_second_compte(self, atelier_m):
        """La garantie centrale. Deux gardes concourent : le paiement se déclare
        sans effet, et `activee_le` refuse d'être reposée."""
        _devis(atelier_m)
        _engager(atelier_m)
        evenement = EvenementPaiement(
            identifiant_produit="cle-idempotence-0001", reussi=True
        )
        for _ in range(4):
            resultat = _notifier(atelier_m, evenement)
            assert resultat.statut == StatutPaiement.VALIDE
        assert atelier_m["acces"].appels == 0
        _verifier(atelier_m)
        # Rejouée après l'ouverture, la notification ne rouvre rien, et la vérification
        # rejouée non plus.
        _notifier(atelier_m, evenement)
        _verifier(atelier_m)
        assert atelier_m["acces"].appels == 1

    def test_un_refus_abandonne_la_souscription(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(
                identifiant_produit="cle-idempotence-0001",
                reussi=False,
                motif="code saisi trois fois de suite incorrectement",
            ),
        )
        assert resultat.statut == StatutPaiement.REJETE
        assert atelier_m["souscriptions"].lire("SO-1").etat == EtatSouscription.ABANDONNEE
        assert atelier_m["acces"].appels == 0

    def test_un_refus_arrive_apres_l_encaissement_ne_defait_rien(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        reussi = EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=True)
        _notifier(atelier_m, reussi)
        _verifier(atelier_m)
        _notifier(
            atelier_m,
            EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=False),
        )
        assert atelier_m["souscriptions"].lire("SO-1").etat == EtatSouscription.ACTIVEE

    def test_un_montant_incoherent_ne_declenche_aucune_activation(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(
                identifiant_produit="cle-idempotence-0001",
                reussi=True,
                montant=Decimal(100),
            ),
        )
        assert resultat.statut == StatutPaiement.REJETE
        assert atelier_m["acces"].appels == 0

    def test_l_echec_d_activation_laisse_le_paiement_encaisse(self, atelier_m):
        """On ne rejette pas un encaissement réel parce qu'un serveur de courriel
        était indisponible."""
        atelier_m["acces"] = _AccesFactice(casse=True)
        _devis(atelier_m)
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=True),
        )
        assert resultat.statut == StatutPaiement.VALIDE
        assert not resultat.activee
        assert resultat.demande_une_intervention
        # ⚠️ Pas 83 : l'ouverture se tente à la vérification, et c'est là qu'elle casse.
        with pytest.raises(RuntimeError, match="accès non ouvert"):
            _verifier(atelier_m)
        souscription = atelier_m["souscriptions"].lire("SO-1")
        assert souscription.etat == EtatSouscription.PAYEE
        assert souscription.a_activer
        assert atelier_m["souscriptions"].a_activer() == [souscription]

    def test_un_encaissement_non_rapproche_est_signale(self, atelier_m):
        resultat = _notifier(
            atelier_m, EvenementPaiement(identifiant_produit="inconnue", reussi=True)
        )
        assert not resultat.rapproche
        assert resultat.demande_une_intervention
        actions = [e.action for e in atelier_m["journal"].lister()]
        assert "paiement.non_affecte" in actions

    def test_une_notification_illisible_ne_leve_pas(self, atelier_m):
        resultat = traiter_notification(
            {"n_importe_quoi": 1},
            fournisseur=_FournisseurFactice(),
            paiements=atelier_m["paiements"],
            suites=_suites(atelier_m),
            journal=atelier_m["journal"],
            a_l_instant=INSTANT,
        )
        assert not resultat.rapproche

    def test_une_notification_d_un_autre_marchand_est_ecartee(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(
                identifiant_produit="cle-idempotence-0001",
                reussi=True,
                identifiant_marchand="UN-AUTRE",
            ),
        )
        assert not resultat.rapproche
        assert atelier_m["acces"].appels == 0

    def test_une_formation_encaissee_n_ouvre_aucun_dossier(self, atelier_m):
        _devis(atelier_m, codes=("FORMATION",), prospect=_prospect(niu=None))
        _engager(atelier_m)
        resultat = _notifier(
            atelier_m,
            EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=True),
        )
        assert resultat.statut == StatutPaiement.VALIDE
        assert not resultat.activee
        assert atelier_m["acces"].appels == 0

    def test_tout_le_parcours_laisse_une_trace_verifiable(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        _notifier(
            atelier_m,
            EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=True),
        )
        _verifier(atelier_m)
        actions = [e.action for e in atelier_m["journal"].lister()]
        assert "souscription.engagee" in actions
        assert "souscription.verification_requise" in actions
        assert "paiement.valide" in actions
        assert "souscription.activee" in actions
        verifier_chaine(atelier_m["journal"].lister())


# ── Le filet ─────────────────────────────────────────────────────────────────


class TestReconciliation:
    def _rec(self, atelier_m, fournisseur, a_l_instant):
        return reconcilier(
            fournisseur=fournisseur,
            paiements=atelier_m["paiements"],
            suites=_suites(atelier_m),
            journal=atelier_m["journal"],
            a_l_instant=a_l_instant,
        )

    def test_le_filet_repeche_un_encaissement_dont_la_notification_s_est_perdue(
        self, atelier_m
    ):
        """C'est le scénario qui coûte le plus cher : l'argent est parti, le
        service n'est pas ouvert, et le client finit par repayer."""
        _devis(atelier_m)
        _engager(atelier_m)
        fournisseur = _FournisseurFactice(
            statut=EvenementPaiement(
                identifiant_produit="cle-idempotence-0001", reussi=True
            )
        )
        rapport = self._rec(atelier_m, fournisseur, INSTANT + timedelta(minutes=10))
        assert rapport.valides == 1
        assert rapport.repeches == 1
        # Le filet encaisse ; l'accès attend la vérification, comme par la notification.
        assert atelier_m["souscriptions"].lire("SO-1").etat == EtatSouscription.PAYEE

    def test_le_filet_n_interroge_pas_ce_qui_vient_d_etre_engage(self, atelier_m):
        """L'abonné n'a pas encore saisi son code : demander ne rend rien
        d'utile et fait payer un appel réseau pour rien."""
        _devis(atelier_m)
        _engager(atelier_m)
        rapport = self._rec(
            atelier_m, _FournisseurFactice(), INSTANT + timedelta(minutes=1)
        )
        assert rapport.examines == 0

    def test_un_statut_inconnu_ne_conclut_rien(self, atelier_m):
        """Inventer un statut serait pire que d'attendre."""
        _devis(atelier_m)
        _engager(atelier_m)
        rapport = self._rec(
            atelier_m, _FournisseurFactice(statut=None), INSTANT + timedelta(minutes=10)
        )
        assert rapport.indetermines == 1
        assert atelier_m["paiements"].lire("PM-1").statut == StatutPaiement.EN_ATTENTE

    def test_la_peremption_ne_declenche_aucun_appel(self, atelier_m):
        _devis(atelier_m)
        _engager(atelier_m)
        rapport = self._rec(
            atelier_m, _FournisseurFactice(), INSTANT + timedelta(hours=25)
        )
        assert rapport.perimes == 1
        assert rapport.examines == 0
        assert atelier_m["paiements"].lire("PM-1").statut == StatutPaiement.EXPIRE
        assert atelier_m["souscriptions"].lire("SO-1").etat == EtatSouscription.ABANDONNEE

    def test_le_filet_et_la_notification_suivent_le_meme_chemin(self, atelier_m):
        """L'un ne doit pas activer là où l'autre rejette."""
        _devis(atelier_m)
        _engager(atelier_m)
        self._rec(
            atelier_m,
            _FournisseurFactice(
                statut=EvenementPaiement(
                    identifiant_produit="cle-idempotence-0001", reussi=True
                )
            ),
            INSTANT + timedelta(minutes=10),
        )
        # Et la notification qui arrive après ne double rien.
        _notifier(
            atelier_m,
            EvenementPaiement(identifiant_produit="cle-idempotence-0001", reussi=True),
            a_l_instant=INSTANT + timedelta(minutes=12),
        )
        assert atelier_m["acces"].appels == 0
        _verifier(atelier_m, a_l_instant=INSTANT + timedelta(minutes=15))
        assert atelier_m["acces"].appels == 1


# ── Le fournisseur Tara ──────────────────────────────────────────────────────


class TestFournisseurTara:
    def test_sans_identifiants_le_mode_simule_n_appelle_rien(self):
        fournisseur = FournisseurTara()
        assert fournisseur.simule
        initiation = fournisseur.initier(
            montant=Decimal(12_500),
            telephone="+237699112233",
            cle_idempotence="abc",
            libelle="Adhésion",
        )
        assert initiation.accepte
        assert initiation.reference_externe == "SIMULE-abc"

    def test_le_mode_simule_ne_valide_jamais_tout_seul(self):
        """Un mode simulé qui validerait automatiquement finirait un jour en
        production."""
        assert (
            FournisseurTara().interroger_statut(cle_idempotence="abc", reference_externe=None)
            is None
        )

    def test_notre_identifiant_prime_sur_celui_de_tara(self):
        """`collectionId` est l'identifiant interne de Tara, jamais la clé de
        rapprochement."""
        evenement = FournisseurTara().lire_notification(
            {"productId": "le-notre", "collectionId": "99", "status": "SUCCESS"}
        )
        assert evenement is not None
        assert evenement.identifiant_produit == "le-notre"

    def test_le_repli_sur_collection_id_existe_pour_les_anciennes_notifications(self):
        evenement = FournisseurTara().lire_notification(
            {"collectionId": "99", "status": "SUCCESS"}
        )
        assert evenement is not None
        assert evenement.identifiant_produit == "99"

    @pytest.mark.parametrize("brut", ["SUCCESS", "VALIDATED", "COMPLETED", "PAID"])
    def test_le_vocabulaire_de_reussite(self, brut: str):
        evenement = FournisseurTara().lire_notification({"productId": "x", "status": brut})
        assert evenement is not None and evenement.reussi

    @pytest.mark.parametrize("brut", ["FAILED", "REJECTED", "CANCELLED", "ERROR"])
    def test_le_vocabulaire_d_echec(self, brut: str):
        evenement = FournisseurTara().lire_notification({"productId": "x", "status": brut})
        assert evenement is not None and not evenement.reussi

    @pytest.mark.parametrize("brut", ["PENDING", "INITIATED"])
    def test_une_operation_en_cours_ne_conclut_rien(self, brut: str):
        """Ni valider, ni rejeter : le silence est la bonne réponse."""
        assert (
            FournisseurTara().lire_notification({"productId": "x", "status": brut}) is None
        )

    def test_une_charge_utile_inexploitable_rend_none(self):
        assert FournisseurTara().lire_notification({}) is None
        assert FournisseurTara().lire_notification({"status": "N_IMPORTE_QUOI"}) is None

    def test_l_adresse_de_base_est_dklo(self):
        """`dklo.co`, et non `taramoney.com` : c'est le premier qui sert
        l'interface développeur."""
        from app.contextes.souscription.adaptateurs.sortant.fournisseur_tara import (
            ADRESSE_TARA,
        )

        assert ADRESSE_TARA == "https://www.dklo.co"


# ── L'API ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Chaque test repart de dépôts neufs, des deux contextes : en mémoire, ces
    ateliers **sont** la persistance.

    ⚠️ L'horloge est **figée à `INSTANT`** pour la durée du test. Les routes
    lisent `maintenant()` ; sans cela, elles dateraient leurs écritures du jour
    réel tandis que les assertions raisonnent sur `AUJOURD_HUI`. Cet écart n'a
    aucun effet le jour où le fichier est écrit, et fait échouer la suite le
    lendemain à minuit — c'est arrivé.
    """
    reinitialiser_atelier()
    reinitialiser_comptoir()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())


def _verifier_par_le_cabinet(reference: str) -> dict:
    """L'administratrice du cabinet ouvre l'accès après avoir vérifié l'identité (pas 83).

    ⚠️ Un client HTTP à part : le visiteur et le cabinet n'ont pas la même session, et
    réutiliser le client du visiteur remplacerait son cookie sans qu'on le voie.
    """
    cabinet = TestClient(creer_application())
    connexion = cabinet.post(
        "/transverse/session",
        json={"courriel": "s.onana@cga-brcg.cm", "mot_de_passe": "cabinet brcg douala 2026"},
    )
    assert connexion.status_code == 200, connexion.text
    reponse = cabinet.post(
        f"/souscription/souscriptions/{reference}/activation",
        json={"verification": "RCCM et CNI du gérant présentés au cabinet ce jour."},
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


class TestApi:
    def test_le_catalogue_exige_une_date(self, client: TestClient):
        assert client.get("/souscription/services").status_code == 422
        reponse = client.get("/souscription/services", params={"a_la_date": "2026-08-15"})
        assert reponse.status_code == 200
        services = {s["code"]: s for s in reponse.json()}
        assert services["ADHESION"]["souscriptible_en_ligne"] is True
        assert services["CREATION"]["souscriptible_en_ligne"] is False

    def test_l_api_rend_l_historique_des_tarifs_borne(self, client: TestClient):
        """La route rend le barème complet, à charge pour le front de résoudre à
        la date d'un devis. Ce qui doit être vrai est que les deux versions
        soient présentes et **bornées** : un barème dont une période ne serait
        pas couverte laisserait un devis sans prix."""
        services = client.get(
            "/souscription/services", params={"a_la_date": "2026-08-15"}
        ).json()
        adhesion = next(s for s in services if s["code"] == "ADHESION")
        liberatoire = next(f for f in adhesion["formules"] if f["code"] == "LIBERATOIRE")
        tarifs = {t["du"]: (t["montant"], t["au"]) for t in liberatoire["tarifs"]}
        assert tarifs["2024-01-01"] == ("10000", "2026-01-01")
        assert tarifs["2026-01-01"] == ("12500", None)

    def test_le_parcours_complet_du_visiteur_a_l_espace_adherent(
        self, client: TestClient
    ):
        """Devis → engagement → notification → compte créé → lien envoyé →
        mot de passe défini → connexion."""
        devis = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "ATANGANA",
                    "prenom": "Sylvie",
                    "courriel": "s.atangana@boulangerie-nkolbisson.cm",
                    "telephone": "699 11 22 33",
                    "denomination": "BOULANGERIE DE NKOLBISSON",
                    "niu": NIU,
                    "chiffre_affaires_declare": "28000000",
                },
                "lignes": [{"service": "ADHESION"}],
            },
        )
        assert devis.status_code == 201, devis.text
        corps = devis.json()
        assert corps["montant_a_regler"] == "12500"
        assert corps["complet"] is True
        reference = corps["reference"]

        engagement = client.post(
            f"/souscription/devis/{reference}/engagement", json={}
        )
        assert engagement.status_code == 201, engagement.text
        paiement = engagement.json()["paiement"]["identifiant"]
        souscription = engagement.json()["souscription"]["reference"]

        # Le mode simulé n'a rien validé tout seul.
        assert (
            client.get(f"/souscription/souscriptions/{souscription}").json()["etat"]
            == "EN_ATTENTE_PAIEMENT"
        )

        confirmation = client.post(
            f"/souscription/paiements/{paiement}/simulation", json={"reussi": True}
        )
        assert confirmation.status_code == 200, confirmation.text
        # ⚠️ Pas 83 : payer n'ouvre plus l'accès ; aucun lien ne part avant la vérification.
        assert confirmation.json()["activee"] is False
        assert atelier().notifications.derniers("compte.activation") == []
        assert _verifier_par_le_cabinet(souscription)["etat"] == "ACTIVEE"

        # Le lien est parti.
        message = atelier().notifications.derniers("compte.activation")[0]
        assert message.destinataire == "s.atangana@boulangerie-nkolbisson.cm"
        secret = message.contexte["lien"].split("jeton=")[1]

        definition = client.post(
            "/transverse/mot-de-passe/definition",
            json={"secret": secret, "mot_de_passe": "farine levure four matin"},
        )
        assert definition.status_code == 200, definition.text

        connexion = client.post(
            "/transverse/session",
            json={
                "courriel": "s.atangana@boulangerie-nkolbisson.cm",
                "mot_de_passe": "farine levure four matin",
            },
        )
        assert connexion.status_code == 200, connexion.text
        acces = connexion.json()
        assert acces["roles"] == ["ADHERENT"]
        assert acces["dossiers"] == [NIU]
        assert acces["interne"] is False

    def test_une_souscription_ne_fabrique_pas_un_reviseur(self, client: TestClient):
        """L'acte est déclenché par un encaissement, sans humain à autoriser :
        ce qui le rend sûr est qu'il n'a aucun degré de liberté."""
        reference = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "MBIDA",
                    "prenom": "Paul",
                    "courriel": "p.mbida@exemple.cm",
                    "telephone": "677445566",
                    "niu": NIU,
                    "chiffre_affaires_declare": "10000000",
                },
                "lignes": [{"service": "ADHESION"}],
            },
        ).json()["reference"]
        engagement = client.post(f"/souscription/devis/{reference}/engagement", json={}).json()
        paiement = engagement["paiement"]["identifiant"]
        client.post(f"/souscription/paiements/{paiement}/simulation", json={"reussi": True})
        _verifier_par_le_cabinet(engagement["souscription"]["reference"])

        boutique = atelier()
        compte = boutique.comptes.par_courriel("p.mbida@exemple.cm")
        assert compte is not None
        habilitations = boutique.habilitations.pour_compte(compte.identifiant)
        assert roles_au(habilitations, AUJOURD_HUI) == frozenset({Role.ADHERENT})
        assert habilitations[0].portee == frozenset({NIU})

    def test_un_devis_de_creation_n_est_pas_engageable(self, client: TestClient):
        reference = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "NANA",
                    "prenom": "Rose",
                    "courriel": "r.nana@exemple.cm",
                    "telephone": "690112233",
                },
                "lignes": [{"service": "CREATION"}],
            },
        ).json()["reference"]
        reponse = client.post(f"/souscription/devis/{reference}/engagement", json={})
        assert reponse.status_code == 409
        assert "sans frais" in reponse.json()["detail"]

    def test_un_service_inconnu_rend_422(self, client: TestClient):
        reponse = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "X",
                    "prenom": "Y",
                    "courriel": "x@exemple.cm",
                    "telephone": "690112233",
                },
                "lignes": [{"service": "TAPIS_VOLANT"}],
            },
        )
        assert reponse.status_code == 422

    def test_un_numero_invalide_rend_422(self, client: TestClient):
        reponse = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "X",
                    "prenom": "Y",
                    "courriel": "x@exemple.cm",
                    "telephone": "12345",
                },
                "lignes": [{"service": "FORMATION"}],
            },
        )
        assert reponse.status_code == 422

    def test_la_notification_repond_toujours_200(self, client: TestClient):
        """Répondre une erreur ferait renvoyer la notification en boucle."""
        charges = (
            {},
            {"n_importe_quoi": True},
            {"productId": "inconnue", "status": "SUCCESS"},
        )
        for charge in charges:
            reponse = client.post("/souscription/notification/tara", json=charge)
            assert reponse.status_code == 200, charge

    def test_la_surveillance_est_reservee(self, client: TestClient):
        assert client.get("/souscription/a-activer").status_code == 401
        client.post(
            "/transverse/session",
            json={
                "courriel": "l.fotso@cga-brcg.cm",
                "mot_de_passe": "cabinet brcg douala 2026",
            },
        )
        assert client.get("/souscription/a-activer").status_code == 403

    def test_la_direction_voit_les_encaissements_a_activer(self, client: TestClient):
        client.post(
            "/transverse/session",
            json={
                "courriel": "b.mballa@cga-brcg.cm",
                "mot_de_passe": "cabinet brcg douala 2026",
            },
        )
        assert client.get("/souscription/a-activer").status_code == 200
        assert client.post("/souscription/reconciliation").status_code == 200

    def test_le_devis_reste_lisible_apres_engagement(self, client: TestClient):
        reference = client.post(
            "/souscription/devis",
            json={
                "prospect": {
                    "nom": "KAMGA",
                    "prenom": "Alice",
                    "courriel": "a.kamga@exemple.cm",
                    "telephone": "655998877",
                },
                "lignes": [{"service": "DOMICILIATION"}],
            },
        ).json()["reference"]
        client.post(f"/souscription/devis/{reference}/engagement", json={})
        relu = client.get(f"/souscription/devis/{reference}")
        assert relu.status_code == 200
        assert relu.json()["etat"] == "ENGAGE"

    def test_un_devis_inconnu_rend_404(self, client: TestClient):
        assert client.get("/souscription/devis/DV-inexistant").status_code == 404
