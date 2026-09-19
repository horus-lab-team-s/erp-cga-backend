"""Les échéances d'abonnement : appel, relance, arrêt du service.

Le manque comblé ici était le plus important du contexte M : la première
mensualité était encaissée, et **plus rien ne se passait**. Un abonnement dont la
deuxième échéance n'est jamais appelée n'est pas un abonnement.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contextes.souscription.api import (
    DepotPaiementsMemoire,
    DepotSouscriptionsMemoire,
    EtatSouscription,
    NatureService,
    Paiement,
    Periodicite,
    Prospect,
    Souscription,
    StatutPaiement,
    StrategieRapprochement,
)
from app.contextes.souscription.application.prelevement import (
    appeler_les_echeances,
    relances_du_jour,
    services_a_suspendre,
)
from app.contextes.souscription.domaine.abonnements import (
    DELAI_APPEL,
    DELAI_GRACE,
    JALONS_RELANCE,
    EtatEcheance,
    echeancier,
    mois_apres,
)
from app.contextes.transverse.api import JournalAuditMemoire, verifier_chaine

EFFET = date(2026, 3, 17)
NIU = "M081234567890P"


def _souscription(**remplace) -> Souscription:
    defauts = dict(
        reference="SO-1",
        devis="DV-1",
        prospect=Prospect(
            nom="ATANGANA",
            prenom="Sylvie",
            courriel="s.atangana@exemple.cm",
            telephone="699112233",
            niu=NIU,
        ),
        service="ADHESION",
        libelle="Suivi comptable — Impôt libératoire",
        nature=NatureService.ABONNEMENT,
        periodicite=Periodicite.MENSUELLE,
        montant=Decimal(12_500),
        abonnement_mensuel=Decimal(12_500),
        etat=EtatSouscription.ACTIVEE,
        engagee_le=datetime(2026, 3, 17, 9, 0),
        payee_le=datetime(2026, 3, 17, 9, 5),
        activee_le=datetime(2026, 3, 17, 9, 5),
        prend_effet_le=EFFET,
        niu=NIU,
        compte="A-001",
    )
    return Souscription(**{**defauts, **remplace})


def _paiement(echeance: str | None, statut=StatutPaiement.VALIDE, numero=1) -> Paiement:
    paiement = Paiement(
        identifiant=f"PM-{numero}",
        reference_reglee="SO-1",
        cle_idempotence=f"cle-idempotence-{numero:04d}",
        montant=Decimal(12_500),
        telephone="+237699112233",
        echeance=echeance,
        initie_le=datetime(2026, 3, 17, 9, 0),
    )
    if statut is StatutPaiement.VALIDE:
        return paiement.valider(
            datetime(2026, 3, 17, 9, 5), strategie=StrategieRapprochement.IDENTIFIANT
        )
    return paiement


class _Fournisseur:
    def __init__(self, accepte: bool = True) -> None:
        self.accepte = accepte
        self.appels: list[str] = []

    def initier(self, *, montant, telephone, cle_idempotence, libelle):
        from app.contextes.souscription.api import Initiation

        self.appels.append(cle_idempotence)
        return Initiation(
            accepte=self.accepte,
            reference_externe=f"REF-{cle_idempotence}" if self.accepte else None,
            message=None if self.accepte else "numéro inconnu du prestataire",
        )

    def interroger_statut(self, *, cle_idempotence, reference_externe):
        return None

    def lire_notification(self, charge_utile):
        return None

    def identifiant_marchand(self):
        return "CGA-TEST"


# ── L'arithmétique des périodes ──────────────────────────────────────────────


class TestPeriodes:
    def test_les_periodes_suivent_la_date_d_effet(self):
        """Une souscription du 17 produit des périodes du 17 au 16, pas du 1er au
        31. C'est ce qui évite le prorata — et le prorata est ce qui rend une
        première facture incompréhensible."""
        assert mois_apres(date(2026, 3, 17), 1) == date(2026, 4, 17)

    def test_le_31_est_ramene_puis_revient(self):
        """Sans calcul depuis la date d'effet, un abonnement souscrit le 31
        glisserait au 28 pour toujours après un seul février."""
        assert mois_apres(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert mois_apres(date(2026, 1, 31), 2) == date(2026, 3, 31)

    def test_l_annee_bissextile(self):
        assert mois_apres(date(2024, 1, 31), 1) == date(2024, 2, 29)

    def test_le_passage_d_annee(self):
        assert mois_apres(date(2026, 12, 17), 1) == date(2027, 1, 17)


# ── L'échéancier ─────────────────────────────────────────────────────────────


class TestEcheancier:
    def test_la_premiere_echeance_est_reglee_par_le_paiement_de_mise_en_route(self):
        """Il ne porte pas de clé d'échéance : il a été engagé avant qu'elles
        n'existent."""
        echeances = echeancier(
            _souscription(), [_paiement(None)], jusqu_au=date(2026, 3, 20)
        )
        assert len(echeances) == 1
        assert echeances[0].etat is EtatEcheance.REGLEE
        assert echeances[0].periode_debut == EFFET
        assert echeances[0].periode_fin == date(2026, 4, 16)

    def test_la_deuxieme_echeance_apparait_a_l_appel(self):
        """Cinq jours avant la période — c'est tout le manque que ce lot
        comble."""
        veille = date(2026, 4, 17) - DELAI_APPEL - timedelta(days=1)
        assert (
            echeancier(_souscription(), [_paiement(None)], jusqu_au=veille)[-1].numero == 1
        )
        au_jour = date(2026, 4, 17) - DELAI_APPEL
        deuxieme = echeancier(_souscription(), [_paiement(None)], jusqu_au=au_jour)[-1]
        assert deuxieme.numero == 2
        assert deuxieme.etat is EtatEcheance.A_APPELER

    def test_une_echeance_exigible_et_non_reglee_est_impayee(self):
        echeances = echeancier(
            _souscription(), [_paiement(None)], jusqu_au=date(2026, 4, 20)
        )
        assert echeances[-1].etat is EtatEcheance.IMPAYEE
        assert echeances[-1].jours_de_retard(date(2026, 4, 20)) == 3

    def test_au_dela_de_la_grace_l_echeance_est_en_defaut(self):
        """La distinction porte tout : impayée, on relance ; en défaut, le
        service s'arrête. Les confondre couperait au premier échec."""
        avant = date(2026, 4, 17) + DELAI_GRACE - timedelta(days=1)
        apres = date(2026, 4, 17) + DELAI_GRACE
        assert (
            echeancier(_souscription(), [_paiement(None)], jusqu_au=avant)[-1].etat
            is EtatEcheance.IMPAYEE
        )
        assert (
            echeancier(_souscription(), [_paiement(None)], jusqu_au=apres)[-1].etat
            is EtatEcheance.EN_DEFAUT
        )

    def test_un_paiement_valide_l_emporte_meme_en_retard(self):
        """Faire apparaître impayée une échéance réglée en retard ferait relancer
        un adhérent à jour."""
        regle = _paiement("SO-1/20260417", numero=2)
        echeances = echeancier(
            _souscription(), [_paiement(None), regle], jusqu_au=date(2026, 5, 10)
        )
        avril = next(e for e in echeances if e.periode_debut == date(2026, 4, 17))
        assert avril.etat is EtatEcheance.REGLEE

    def test_un_prelevement_en_cours_n_est_pas_un_impaye(self):
        en_cours = _paiement("SO-1/20260417", StatutPaiement.EN_ATTENTE, numero=2)
        echeances = echeancier(
            _souscription(), [_paiement(None), en_cours], jusqu_au=date(2026, 4, 20)
        )
        assert echeances[-1].etat is EtatEcheance.EN_COURS

    def test_la_cle_identifie_la_periode_pas_le_numero(self):
        """Renuméroter après une résiliation partielle changerait la clé
        d'échéances déjà réglées."""
        echeances = echeancier(
            _souscription(), [_paiement(None)], jusqu_au=date(2026, 5, 20)
        )
        assert echeances[1].cle == "SO-1/20260417"

    def test_une_prestation_ponctuelle_ne_produit_aucune_echeance(self):
        ponctuelle = _souscription(
            nature=NatureService.PONCTUEL,
            periodicite=Periodicite.UNIQUE,
            abonnement_mensuel=Decimal(0),
        )
        assert echeancier(ponctuelle, [], jusqu_au=date(2027, 1, 1)) == []

    def test_une_souscription_jamais_activee_ne_produit_rien(self):
        attente = _souscription(
            etat=EtatSouscription.EN_ATTENTE_PAIEMENT,
            payee_le=None,
            activee_le=None,
            prend_effet_le=None,
        )
        assert echeancier(attente, [], jusqu_au=date(2027, 1, 1)) == []

    def test_la_resiliation_laisse_courir_le_mois_paye_d_avance(self):
        """Le mois entamé a été payé d'avance : il est dû jusqu'au bout, et
        l'échéancier ne s'arrête pas au jour de la résiliation."""
        resiliee = _souscription(
            etat=EtatSouscription.RESILIEE,
            close_le=datetime(2026, 5, 2, 10, 0),
        )
        echeances = echeancier(
            resiliee, [_paiement(None)], jusqu_au=date(2026, 8, 1)
        )
        assert [e.periode_debut for e in echeances] == [
            date(2026, 3, 17),
            date(2026, 4, 17),
        ]


# ── L'appel ──────────────────────────────────────────────────────────────────


class TestAppel:
    def _atelier(self, jusqu_au: date):
        souscriptions = DepotSouscriptionsMemoire()
        souscriptions.enregistrer(_souscription())
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement(None))
        return souscriptions, paiements, JournalAuditMemoire()

    def _appeler(self, a_la_date: date, fournisseur=None, **etat):
        souscriptions, paiements, journal = etat.get("atelier") or self._atelier(a_la_date)
        rapport = appeler_les_echeances(
            souscriptions=souscriptions,
            paiements=paiements,
            fournisseur=fournisseur or _Fournisseur(),
            journal=journal,
            a_l_instant=datetime.combine(a_la_date, datetime.min.time()),
            identifiant="PM-AUTO",
            cle_idempotence="cle-auto-0001",
        )
        return rapport, souscriptions, paiements, journal

    def test_l_echeance_du_jour_est_appelee(self):
        jour = date(2026, 4, 17) - DELAI_APPEL
        rapport, _, paiements, _ = self._appeler(jour)
        assert rapport.appelees == 1
        assert rapport.montant_appele == Decimal(12_500)
        engage = paiements.lire("PM-AUTO-002")
        assert engage.echeance == "SO-1/20260417"
        assert engage.statut is StatutPaiement.EN_ATTENTE

    def test_rien_n_est_appele_trop_tot(self):
        jour = date(2026, 4, 17) - DELAI_APPEL - timedelta(days=1)
        rapport, _, _, _ = self._appeler(jour)
        assert rapport.examinees == 0

    def test_rejouer_la_tache_ne_produit_pas_un_second_debit(self):
        """Première garde : la machine à états. Une échéance dont un prélèvement
        est en cours n'est plus `A_APPELER`, donc n'est plus examinée.

        C'est le cas courant — l'ordonnanceur rejoue sa tâche dans la journée.
        """
        jour = date(2026, 4, 17) - DELAI_APPEL
        atelier = self._atelier(jour)
        fournisseur = _Fournisseur()
        for _ in range(3):
            _, _, paiements, _ = self._appeler(jour, fournisseur, atelier=atelier)
        assert len(fournisseur.appels) == 1
        assert len(paiements.par_souscription("SO-1")) == 2

    def test_un_prelevement_refuse_n_est_pas_rappele_automatiquement(self):
        """Seconde garde : la clé d'échéance portée par le paiement.

        Décision, et non conséquence : rappeler chaque jour un prélèvement
        refusé produirait un menu de paiement quotidien sur le téléphone de
        l'adhérent — ce qu'il vit comme du harcèlement, et ce que chaque
        tentative peut lui facturer. Le rattrapage passe par la relance.
        """
        jour = date(2026, 4, 17) - DELAI_APPEL
        atelier = self._atelier(jour)
        refusant = _Fournisseur(accepte=False)
        premier, _, _, _ = self._appeler(jour, refusant, atelier=atelier)
        assert premier.refusees == 1

        acceptant = _Fournisseur()
        second, _, paiements, _ = self._appeler(jour, acceptant, atelier=atelier)
        assert second.deja_appelees == 1
        assert second.appelees == 0
        assert acceptant.appels == []
        assert len(paiements.par_souscription("SO-1")) == 2

    def test_un_refus_a_l_initiation_est_signale(self):
        jour = date(2026, 4, 17) - DELAI_APPEL
        rapport, _, paiements, _ = self._appeler(jour, _Fournisseur(accepte=False))
        assert rapport.refusees == 1
        assert rapport.demande_une_intervention
        assert paiements.lire("PM-AUTO-002").statut is StatutPaiement.REJETE

    def test_le_paiement_est_ecrit_avant_l_appel(self):
        """Entre un enregistrement de trop et un encaissement perdu, on choisit
        l'enregistrement de trop — même règle qu'à l'engagement."""
        jour = date(2026, 4, 17) - DELAI_APPEL
        _, _, paiements, _ = self._appeler(jour, _Fournisseur(accepte=False))
        assert paiements.lire("PM-AUTO-002") is not None

    def test_l_appel_laisse_une_trace_verifiable(self):
        jour = date(2026, 4, 17) - DELAI_APPEL
        _, _, _, journal = self._appeler(jour)
        assert [e.action for e in journal.lister()] == ["abonnement.echeance_appelee"]
        verifier_chaine(journal.lister())


# ── Les relances ─────────────────────────────────────────────────────────────


class TestRelances:
    def _relances(self, a_la_date: date):
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement(None))
        return relances_du_jour([_souscription()], paiements, a_la_date)

    @pytest.mark.parametrize("jalon", JALONS_RELANCE)
    def test_une_relance_par_jalon(self, jalon: int):
        rappels = self._relances(date(2026, 4, 17) + timedelta(days=jalon))
        assert len(rappels) == 1
        assert rappels[0].jours_de_retard == jalon
        assert rappels[0].montant == Decimal(12_500)

    def test_aucune_relance_entre_deux_jalons(self):
        """Sans le « exactement », un impayé de vingt jours déclencherait les
        trois relances à chaque passage, et l'adhérent recevrait un message par
        jour."""
        assert self._relances(date(2026, 4, 17) + timedelta(days=3)) == []

    def test_le_dernier_jalon_se_distingue(self):
        """Les deux premières relances informent, la dernière annonce une
        conséquence."""
        premier = self._relances(date(2026, 4, 17) + timedelta(days=JALONS_RELANCE[0]))
        dernier = self._relances(date(2026, 4, 17) + timedelta(days=JALONS_RELANCE[-1]))
        assert not premier[0].derniere
        assert dernier[0].derniere

    def test_aucune_relance_sur_une_echeance_reglee(self):
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement(None))
        paiements.enregistrer(_paiement("SO-1/20260417", numero=2))
        rappels = relances_du_jour(
            [_souscription()], paiements, date(2026, 4, 18)
        )
        assert rappels == []


# ── L'arrêt du service ───────────────────────────────────────────────────────


class TestArretDuService:
    def _decisions(self, a_la_date: date):
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement(None))
        return services_a_suspendre([_souscription()], paiements, a_la_date)

    def test_rien_avant_la_fin_de_la_grace(self):
        assert self._decisions(date(2026, 4, 17) + DELAI_GRACE - timedelta(days=1)) == []

    def test_la_decision_porte_le_montant_du(self):
        decisions = self._decisions(date(2026, 4, 17) + DELAI_GRACE)
        assert len(decisions) == 1
        assert decisions[0].montant_du == Decimal(12_500)
        assert decisions[0].echeances_en_defaut == ["SO-1/20260417"]

    def test_la_consigne_interdit_de_suspendre_le_compte(self):
        """Suspendre n'est pas séquestrer : les documents comptables de
        l'adhérent lui appartiennent, et il est tenu de les conserver dix ans."""
        consigne = self._decisions(date(2026, 4, 17) + DELAI_GRACE)[0].consigne
        assert "Ne pas suspendre le compte" in consigne
        assert "lecture" in consigne

    def test_les_impayes_s_accumulent(self):
        decisions = self._decisions(date(2026, 7, 1))
        assert len(decisions[0].echeances_en_defaut) >= 2
        assert decisions[0].montant_du >= Decimal(25_000)
