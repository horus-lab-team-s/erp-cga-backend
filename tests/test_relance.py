"""La relance graduée, et le parcours refermé sur la saga.

Deux moitiés dans ce fichier, et elles se rejoignent à la fin :

* **la relance**, dont chaque nombre coûte de l'argent et peut coûter le canal ;
* **l'encaissement**, qui dépose l'événement que la saga d'ouverture attend.

Le dernier test parcourt le chemin entier, de la demande déposée au sous-domaine
qui répond. C'est celui dont l'échec signifierait qu'un client a payé et que rien
ne se passe.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.contextes.souscription.adaptateurs.sortant.plan_de_relance import (
    charger_le_plan_de_relance,
)
from app.contextes.souscription.application.encaissement_du_parcours import (
    EncaissementRefuse,
    encaisser_l_acceptation,
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
from app.contextes.souscription.domaine.proforma import TarifArrete, emettre
from app.contextes.souscription.domaine.relance import (
    IssueDeRelance,
    PalierDeRelance,
    PlanDeRelance,
    SuiviDeRelance,
    impayees_a_reprendre,
    relance_due,
    relances_dues,
)
from app.infrastructure.config import RACINE_DEPOT
from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire

T0 = datetime(2026, 9, 10, 9, 0)
RELANCE = RACINE_DEPOT / "Docs" / "referentiel" / "relance"


def _plan(*jours: int, impaye: int = 30) -> PlanDeRelance:
    return PlanDeRelance(
        paliers=tuple(
            PalierDeRelance(
                rang=rang,
                apres=timedelta(days=j),
                modele="cga_relance_proforma",
                ton=f"ton-{rang}",
            )
            for rang, j in enumerate(jours or (3, 7, 14), start=1)
        ),
        impaye_apres=timedelta(days=impaye),
    )


def _tarif() -> TarifArrete:
    return TarifArrete(
        montant=Decimal("250000"), plancher=Decimal("200000"),
        reference=Decimal("250000"), plafond=Decimal("375000"),
        version_bareme="2026.1", chiffre_par="awono", valide_par="direction",
        arrete_le=T0,
    )


def _proforma(numero="PRO-2026-0001", dossier="dos-1", transmise=True, service="CREATION"):
    p = emettre(
        numero=numero, dossier=dossier, service=service, tarif=_tarif(),
        contenu=b"%PDF", modele="CREATION", version_modele="2026.1",
        a_l_instant=T0,
    )
    return p.transmise(T0) if transmise else p


def _suivi(numero="PRO-2026-0001", *rangs: int) -> SuiviDeRelance:
    suivi = SuiviDeRelance(proforma=numero)
    for rang in rangs:
        suivi = suivi.avec(rang, T0)
    return suivi


# ── Le plan ───────────────────────────────────────────────────────────────────


class TestPlanDeRelance:
    def test_les_rangs_vont_de_un_en_un(self):
        """Le rang est inscrit au suivi de chaque proforma ; un trou rendrait
        indécidable le palier suivant."""
        with pytest.raises(ValueError, match="de 1 en 1"):
            PlanDeRelance(
                paliers=(
                    PalierDeRelance(rang=1, apres=timedelta(days=3), modele="m", ton="t"),
                    PalierDeRelance(rang=3, apres=timedelta(days=7), modele="m", ton="t"),
                ),
                impaye_apres=timedelta(days=30),
            )

    def test_les_delais_croissent_strictement(self):
        """Deux paliers au même délai partiraient le même jour, et un délai qui
        décroît ferait relancer à l'envers."""
        with pytest.raises(ValueError, match="croître"):
            PlanDeRelance(
                paliers=(
                    PalierDeRelance(rang=1, apres=timedelta(days=7), modele="m", ton="t"),
                    PalierDeRelance(rang=2, apres=timedelta(days=3), modele="m", ton="t"),
                ),
                impaye_apres=timedelta(days=30),
            )


# ── Le déclenchement ──────────────────────────────────────────────────────────


class TestQuandRelancer:
    def test_avant_le_premier_delai_rien_ne_part(self):
        issue, relance = relance_due(
            _proforma(), _suivi(), _plan(), T0 + timedelta(days=2)
        )
        assert issue is IssueDeRelance.TROP_TOT
        assert relance is None

    def test_au_premier_delai_le_premier_palier_part(self):
        issue, relance = relance_due(
            _proforma(), _suivi(), _plan(), T0 + timedelta(days=3)
        )
        assert issue is IssueDeRelance.DUE
        assert relance.rang == 1
        assert relance.ton == "ton-1"
        assert relance.derniere is False

    def test_le_delai_court_depuis_la_transmission(self):
        """⚠️ Jamais depuis l'émission. Relancer un client qui n'a rien reçu le
        laisse perplexe et fait passer le cabinet pour désorganisé."""
        tardive = _proforma(transmise=False).transmise(T0 + timedelta(days=10))
        issue, _ = relance_due(tardive, _suivi(), _plan(), T0 + timedelta(days=12))
        assert issue is IssueDeRelance.TROP_TOT

    def test_une_proforma_jamais_transmise_ne_se_relance_pas(self):
        issue, _ = relance_due(
            _proforma(transmise=False), _suivi(), _plan(), T0 + timedelta(days=30)
        )
        assert issue is IssueDeRelance.SANS_OBJET

    def test_un_palier_franchi_ne_se_rejoue_pas(self):
        """Le balayage tourne toutes les heures ; sans cette garde, le client
        recevrait vingt-quatre messages par jour."""
        issue, _ = relance_due(
            _proforma(), _suivi("PRO-2026-0001", 1), _plan(), T0 + timedelta(days=4)
        )
        assert issue is IssueDeRelance.TROP_TOT

    def test_un_seul_palier_par_passage_le_plus_avance(self):
        """⚠️ Si le balayage a été arrêté une semaine, trois paliers sont dus. On
        n'en envoie qu'un. Le client n'y verrait pas un système remis en route,
        il y verrait du harcèlement."""
        issue, relance = relance_due(
            _proforma(), _suivi(), _plan(), T0 + timedelta(days=20)
        )
        assert issue is IssueDeRelance.DUE
        assert relance.rang == 3
        assert relance.derniere is True

    def test_apres_le_dernier_palier_la_proforma_sort_de_la_file(self):
        """Le silence d'un client après trois relances est une information ;
        continuer à écrire n'en est pas une, et détruit la note du numéro."""
        issue, relance = relance_due(
            _proforma(), _suivi("PRO-2026-0001", 1, 2, 3), _plan(),
            T0 + timedelta(days=60),
        )
        assert issue is IssueDeRelance.EPUISEE
        assert relance is None

    @pytest.mark.parametrize(
        "geste", ["acceptee", "annulee"]
    )
    def test_une_proforma_qui_a_trouve_son_issue_ne_se_relance_pas(self, geste):
        proforma = getattr(_proforma(), geste)()
        issue, _ = relance_due(proforma, _suivi(), _plan(), T0 + timedelta(days=30))
        assert issue is IssueDeRelance.SANS_OBJET

    def test_une_proforma_remplacee_ne_se_relance_pas(self):
        """C'est la nouvelle version qui se relance, sur sa propre date de
        transmission. Relancer les deux enverrait deux messages pour un
        engagement."""
        remplacee, _ = _proforma().nouvelle_version(
            _tarif(), numero="PRO-2026-0002", contenu=b"v2", a_l_instant=T0,
        )
        issue, _ = relance_due(remplacee, _suivi(), _plan(), T0 + timedelta(days=30))
        assert issue is IssueDeRelance.SANS_OBJET


class TestSuivi:
    def test_inscrire_deux_fois_le_meme_rang_ne_compte_qu_une(self):
        """Un balayage rejoué doublerait le compteur et ferait croire que le
        client a été relancé deux fois au même rang."""
        suivi = _suivi("PRO-2026-0001", 1)
        assert suivi.avec(1, T0 + timedelta(hours=1)) is suivi
        assert suivi.rangs_envoyes == (1,)

    def test_il_ne_vit_pas_sur_la_proforma(self):
        """⚠️ Celle-ci est figée et vaut contrat. Y inscrire un compteur de
        relances ferait changer un document contractuel pour une raison qui n'a
        rien de contractuel."""
        from app.contextes.souscription.domaine.proforma import Proforma

        champs = set(Proforma.model_fields)
        assert not {"relances", "rangs_envoyes", "derniere_relance"} & champs


class TestLot:
    def test_les_plus_anciennes_transmissions_passent_d_abord(self):
        """Si le lot est borné en aval, c'est le client qui attend depuis le plus
        longtemps qui doit passer d'abord."""
        proformas = [
            _proforma(f"PRO-2026-000{rang}", dossier=f"dos-{rang}", transmise=False)
            .transmise(T0 + timedelta(days=jour))
            for rang, jour in ((1, 5), (2, 0), (3, 2))
        ]
        lot = relances_dues(proformas, {}, _plan(), T0 + timedelta(days=20))
        assert [r.proforma for r in lot] == [
            "PRO-2026-0002",
            "PRO-2026-0003",
            "PRO-2026-0001",
        ]

    def test_un_lot_vide_quand_rien_n_est_du(self):
        assert relances_dues([_proforma()], {}, _plan(), T0) == []


class TestImpayees:
    def test_une_acceptee_non_payee_revient_a_l_humain(self):
        """Ce n'est pas une relance de plus : le client s'est engagé. Continuer à
        lui envoyer des modèles ne produit rien qu'une facture de messagerie."""
        acceptee = _proforma().acceptee()
        assert impayees_a_reprendre([acceptee], _plan(), T0 + timedelta(days=31)) == [
            acceptee
        ]

    def test_avant_le_delai_elle_reste_tranquille(self):
        acceptee = _proforma().acceptee()
        assert impayees_a_reprendre([acceptee], _plan(), T0 + timedelta(days=29)) == []

    def test_une_transmise_non_acceptee_n_est_pas_une_impayee(self):
        """Elle relève de la relance, pas de la reprise : le client ne s'est
        engagé à rien."""
        assert impayees_a_reprendre([_proforma()], _plan(), T0 + timedelta(days=60)) == []


# ── Le plan réel ──────────────────────────────────────────────────────────────


class TestPlanDuReferentiel:
    @pytest.fixture(scope="class")
    def plan(self):
        return charger_le_plan_de_relance(RELANCE)

    def test_il_se_charge_avec_trois_paliers(self, plan):
        assert [p.apres.days for p in plan.paliers] == [3, 7, 14]
        assert plan.impaye_apres.days == 30

    def test_les_trois_paliers_emploient_le_meme_modele(self, plan):
        """⚠️ Le ton est une variable, pas un modèle. Trois modèles quasi
        identiques se font approuver trois fois, se corrigent trois fois, et
        divergent au premier oubli."""
        assert len({p.modele for p in plan.paliers}) == 1

    def test_le_modele_existe_au_catalogue(self, plan):
        """Un plan qui nomme un modèle inexistant ferait échouer chaque relance
        au moment de l'envoi, et l'échec ne se verrait qu'à la première."""
        from app.contextes.souscription.adaptateurs.sortant.catalogue_modeles import (
            CatalogueDeModeles,
        )

        catalogue = CatalogueDeModeles.depuis(
            RACINE_DEPOT / "Docs" / "referentiel" / "messagerie" / "modeles"
        )
        for palier in plan.paliers:
            assert catalogue.prendre(palier.modele) is not None

    def test_chaque_palier_a_son_propre_ton(self, plan):
        assert len({p.ton for p in plan.paliers}) == len(plan.paliers)


# ── L'encaissement ────────────────────────────────────────────────────────────


def _dossier(reference="dos-1"):
    demande = DemandeDeContact(
        identifiant="dc-1", deposee_le=T0, nom="Abena Ndzana",
        telephone="699112233", service_souhaite="CREATION",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(accorde=False, recueilli_le=T0, version_du_texte="v1"),
    )
    dossier = ouvrir_un_dossier(reference, demande)
    for geste in (
        lambda d: d.affecter("awono", T0, motif="proximité"),
        lambda d: d.premier_contact(T0),
        lambda d: d.qualifier(T0),
        lambda d: d.chiffrer(T0),
        lambda d: d.emettre_la_proforma(T0),
        lambda d: d.accepter(T0),
    ):
        dossier = geste(dossier)
    return dossier


class TestEncaissement:
    def _encaisser(self, dossier=None, proforma=None, boite=None, **surcharges):
        defauts = {
            "boite": boite or BoiteDEnvoiMemoire(),
            "slug": "station-bonaberi",
            "tenant": "tnt-station",
            "a_l_instant": T0 + timedelta(days=3),
            "identifiant_evenement": "ev-1",
        }
        return encaisser_l_acceptation(
            dossier or _dossier(), proforma or _proforma(),
            **{**defauts, **surcharges},
        )

    def test_le_dossier_passe_a_paye_et_l_evenement_est_depose(self):
        boite = BoiteDEnvoiMemoire()
        resultat = self._encaisser(boite=boite)

        assert resultat.dossier.etat is EtatDossier.PAYEE
        assert resultat.rejeu is False
        assert [e.nom for e in boite.a_publier()] == ["PaiementEncaissé"]

    def test_la_cle_de_l_evenement_est_la_reference_du_dossier(self):
        """⚠️ Pas le numéro de la proforma. C'est cette clé que la saga emploie
        pour retrouver son exécution : prendre le numéro casserait le jour où une
        v2 est acceptée, en faisant repartir la saga de zéro sur un tenant à
        moitié ouvert."""
        boite = BoiteDEnvoiMemoire()
        self._encaisser(boite=boite)
        assert boite.a_publier()[0].cle == "dos-1"

    def test_la_charge_porte_ce_que_l_ouverture_demande_et_rien_de_plus(self):
        """Un événement qui transporte des données dont personne n'a besoin finit
        par en transporter qu'on ne voulait pas voir circuler : les journaux, les
        files et les sauvegardes le recopient tous."""
        boite = BoiteDEnvoiMemoire()
        self._encaisser(boite=boite)
        charge = boite.a_publier()[0].charge
        assert set(charge) == {
            "tenant", "slug", "proforma", "version_proforma", "reference_externe",
            # Pas 95 : la septième étape de l'ouverture crée le compte
            # administrateur du client et lui envoie son lien. Sans son adresse,
            # elle ne peut faire ni l'un ni l'autre — le tenant s'ouvrait et le
            # client restait dehors. Le nom l'accompagne parce qu'un courriel qui
            # commence par « Bonjour jp.nkoa » se lit comme une machine.
            "courriel_titulaire", "nom_titulaire",
            # ─────────────────────────────────────────────────────────────────
            # ⚠️ AJOUTS DU 27 SEPTEMBRE, ET CHACUN A DÛ SE JUSTIFIER.
            #
            # L'encaissement ne déclenchait qu'une chose : l'ouverture d'un
            # espace. Il en déclenche deux depuis qu'une CRÉATION payée ouvre
            # aussi son dossier de formalité — le client a payé pour que sa
            # société soit immatriculée, pas pour recevoir un accès.
            #
            # Le contexte qui ouvre ce dossier n'a pas le droit de lire la
            # Souscription, et c'est voulu : le graphe l'interdit. L'événement
            # est le mécanisme prévu pour franchir cette frontière, et il doit
            # donc porter de quoi agir.
            #
            #   · `service` : sans lui, l'abonné ne sait pas s'il doit agir.
            #   · `faits_fondateurs` : CINQ faits sur la douzaine recueillie —
            #     dénomination, forme, capital, activité, siège. Les tranches de
            #     chiffre d'affaires ou le nombre de salariés ont servi à
            #     CHIFFRER : ils restent sur la proforma, qui les conserve.
            #   · `telephone_titulaire` : il était écarté avec ce motif, « il ne
            #     sert pas à créer un compte ». C'était juste. Le fondateur d'un
            #     dossier de formalité porte un téléphone — c'est par là que le
            #     greffe et le cabinet le joignent pendant l'immatriculation. Le
            #     motif d'hier ne tient plus ; la règle, elle, n'a pas bougé.
            # ─────────────────────────────────────────────────────────────────
            "service", "telephone_titulaire",
            # ⚠️ Ajouté le 27 septembre : toute vente n'ouvre pas un locataire.
            # Dit explicitement plutôt que déduit d'un slug absent — un champ
            # manquant est un défaut, pas une consigne.
            "ouvre_un_locataire",
        }
        # Une CRÉATION se vend à une adhérente : elle n'ouvre aucun locataire.
        assert charge["ouvre_un_locataire"] is False
        # ⚠️ `faits_fondateurs` N'EST PAS LÀ, ET C'EST LA BONNE RÉPONSE.
        #
        # Cette proforma ne porte aucun fait fondateur — elle ne vend pas une
        # création. La clé n'est donc pas posée : un événement ne transporte pas
        # un dictionnaire vide pour la forme. Le cas d'à côté,
        # `test_une_creation_emporte_ses_faits_fondateurs`, tient l'autre bord.
        assert "faits_fondateurs" not in charge
        # ⚠️ CE QUI RESTE INTERDIT, et c'est le sens de ce cas : ni le montant,
        # ni le détail de la proforma. Un événement est recopié dans les
        # journaux, les files et les sauvegardes ; ce qu'on y met y reste
        # longtemps.
        for absent in ("montant", "lignes", "debours"):
            assert absent not in charge, f"« {absent} » n'a rien à faire dans l'événement"


    def test_une_creation_emporte_ses_faits_fondateurs(self):
        """L'autre bord : quand la proforma porte des faits, l'événement les porte.

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ SANS EUX, LE DOSSIER DE FORMALITÉ S'OUVRIRAIT VIDE.

        Le contexte qui ouvre ce dossier n'a pas le droit de lire la
        Souscription : l'événement est son seul renseignement. Un client qui
        vient de payer sa création et à qui l'on redemanderait le nom de sa
        société aurait raison de trouver cela étrange.

        ⚠️ Et **cinq faits seulement**. Recopier toute la qualification serait
        plus simple, et ferait circuler le chiffre d'affaires prévisionnel du
        client dans chaque journal et chaque sauvegarde.
        ─────────────────────────────────────────────────────────────────────────
        """
        qualifiee = emettre(
            numero="PRO-2026-0002", dossier="dos-1", service="CREATION",
            tarif=_tarif(), contenu=b"%PDF", modele="CREATION",
            version_modele="2026.1", a_l_instant=T0,
            faits={
                "denomination_souhaitee": "STATION BONABÉRI SARL",
                "forme_juridique": "SARL",
                "capital_social": "1000000",
                "activite": "station-service",
                "siege": "Bonabéri, Douala",
                # Ceux-ci ont servi à CHIFFRER, et restent sur la proforma.
                "chiffre_affaires_prevu": "DE_10M_A_50M",
                "salaries_prevus": "3",
            },
        ).transmise(T0)
        boite = BoiteDEnvoiMemoire()
        self._encaisser(proforma=qualifiee, boite=boite)

        charge = boite.a_publier()[0].charge
        assert charge["service"] == "CREATION"
        assert set(charge["faits_fondateurs"]) == {
            "denomination_souhaitee", "forme_juridique",
            "capital_social", "activite", "siege",
        }
        assert charge["faits_fondateurs"]["denomination_souhaitee"] == (
            "STATION BONABÉRI SARL"
        )
        # ⚠️ Ce qui a servi au prix ne voyage pas.
        assert "chiffre_affaires_prevu" not in charge["faits_fondateurs"]
        assert "salaries_prevus" not in charge["faits_fondateurs"]

    def test_un_rejeu_ne_depose_rien_de_plus(self):
        """Le prestataire rejoue ses rappels, parfois plusieurs jours après."""
        boite = BoiteDEnvoiMemoire()
        premier = self._encaisser(boite=boite)
        second = self._encaisser(dossier=premier.dossier, boite=boite,
                                 identifiant_evenement="ev-2")

        assert second.rejeu is True
        assert len(boite.a_publier()) == 1

    def test_une_proforma_d_un_autre_dossier_est_refusee(self):
        """Rapprocher un paiement du mauvais engagement ouvrirait le tenant d'un
        autre client."""
        with pytest.raises(EncaissementRefuse, match="autre client"):
            self._encaisser(proforma=_proforma(dossier="dos-9"))


# ── La chaîne entière ─────────────────────────────────────────────────────────


class TestParcoursComplet:
    """⚠️ **Le test qui parcourt le chemin annoncé par le document, de bout en
    bout.**

    Si un jour il échoue, c'est que le lien entre l'acceptation et l'ouverture
    s'est rompu, ce qui est l'incident que personne ne voit : le client a payé, et
    rien ne se passe.
    """

    def test_de_l_acceptation_au_sous_domaine_qui_repond(self):
        from app.contextes.tenants.adaptateurs.sortant.provisionneur_local import (
            ProvisionneurLocal,
        )
        from app.contextes.tenants.adaptateurs.sortant.repertoire_memoire import (
            RegistreEnMemoire,
        )
        from app.contextes.tenants.application.ouverture_sur_paiement import (
            abonner_l_ouverture,
        )
        from app.contextes.tenants.domaine.tenant import StatutTenant
        from app.infrastructure.depots_orchestration import DepotExecutionsMemoire
        from app.orchestration.relais import Abonnements, publier_un_lot

        boite = BoiteDEnvoiMemoire()
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        abonnements = Abonnements()
        abonner_l_ouverture(
            abonnements,
            provisionneur=ProvisionneurLocal(registre, a_la_date=date(2026, 9, 13)),
            executions=executions,
            horloge=lambda: T0 + timedelta(days=3),
        )

        # 1 · Le client accepte, puis paie. Le rappel de l'opérateur arrive.
        # ⚠️ `PLATEFORME` : la seule offre qui ouvre un locataire. Une prestation
        # vendue à une adhérente n'en ouvre pas — voir
        # `SERVICES_OUVRANT_UN_LOCATAIRE`. Ces deux cas éprouvent le mécanisme du
        # locataire, pas la décision commerciale.
        resultat = encaisser_l_acceptation(
            _dossier(), _proforma(service="PLATEFORME"),
            boite=boite, slug="station-bonaberi", tenant="tnt-station",
            a_l_instant=T0 + timedelta(days=3), identifiant_evenement="ev-1",
            reference_externe="MTN-88213",
        )
        assert resultat.dossier.etat is EtatDossier.PAYEE

        # 2 · Le relais publie. La saga s'en saisit.
        rapport = publier_un_lot(boite, abonnements, T0 + timedelta(days=3))
        assert rapport.publies == 1
        assert rapport.echecs == 0

        # 3 · Le sous-domaine répond.
        tenant = registre.par_slug("station-bonaberi")
        assert tenant is not None
        assert tenant.statut is StatutTenant.ACTIF

    def test_le_rappel_rejoue_n_ouvre_pas_un_second_tenant(self):
        """**L'incident qui coûte le plus cher.** Il est fermé des deux côtés :
        le dossier ne s'encaisse qu'une fois, et la saga ne s'exécute qu'une
        fois."""
        from app.contextes.tenants.adaptateurs.sortant.provisionneur_local import (
            ProvisionneurLocal,
        )
        from app.contextes.tenants.adaptateurs.sortant.repertoire_memoire import (
            RegistreEnMemoire,
        )
        from app.contextes.tenants.application.ouverture import NOM_SAGA
        from app.contextes.tenants.application.ouverture_sur_paiement import (
            abonner_l_ouverture,
        )
        from app.infrastructure.depots_orchestration import DepotExecutionsMemoire
        from app.orchestration.relais import Abonnements, publier_un_lot

        boite = BoiteDEnvoiMemoire()
        registre, executions = RegistreEnMemoire(), DepotExecutionsMemoire()
        appels: list[str] = []

        def stockage(contexte):
            appels.append("stockage")
            return contexte

        abonnements = Abonnements()
        abonner_l_ouverture(
            abonnements,
            provisionneur=ProvisionneurLocal(
                registre, a_la_date=date(2026, 9, 13), stockage=stockage
            ),
            executions=executions,
            horloge=lambda: T0 + timedelta(days=3),
        )

        premier = encaisser_l_acceptation(
            _dossier(), _proforma(service="PLATEFORME"), boite=boite, slug="station-bonaberi",
            tenant="tnt-station", a_l_instant=T0 + timedelta(days=3),
            identifiant_evenement="ev-1",
        )
        publier_un_lot(boite, abonnements, T0 + timedelta(days=3))

        # Le prestataire renvoie sa notification, trois jours plus tard.
        rejeu = encaisser_l_acceptation(
            premier.dossier, _proforma(service="PLATEFORME"), boite=boite, slug="station-bonaberi",
            tenant="tnt-station", a_l_instant=T0 + timedelta(days=6),
            identifiant_evenement="ev-2",
        )
        publier_un_lot(boite, abonnements, T0 + timedelta(days=6))

        assert rejeu.rejeu is True
        assert appels.count("stockage") == 1, "un second préfixe de stockage a été ouvert"
        assert executions.trouver(NOM_SAGA, "dos-1") is not None


class TestUneVenteNOuvrePasToujoursUnLocataire:
    """⚠️ LA DÉCISION DU 27 SEPTEMBRE, TENUE À L'ENDROIT OÙ ELLE SE PREND.

    Toute proforma payée ouvrait un locataire — `tnt-{slug}` — quel que soit le
    service vendu. Une entrepreneuse qui achetait la création de sa SARL
    recevait donc **son propre espace de plateforme**, isolé du cabinet, pendant
    que son entreprise entrait au portefeuille du cabinet. Deux locataires pour
    une seule cliente, et un espace vide.

    Une prestation se vend à une adhérente ; un locataire se vend à un autre
    centre de gestion.
    """

    def test_une_prestation_vendue_n_ouvre_pas_de_locataire(self):
        boite = BoiteDEnvoiMemoire()
        encaisser_l_acceptation(
            _dossier(), _proforma(service="CREATION"),
            boite=boite, slug="couture-douala", tenant="tnt-couture",
            a_l_instant=T0 + timedelta(days=3), identifiant_evenement="ev-1",
            reference_externe="MTN-1",
        )
        assert boite.a_publier()[0].charge["ouvre_un_locataire"] is False

    def test_la_plateforme_vendue_en_ouvre_un(self):
        boite = BoiteDEnvoiMemoire()
        encaisser_l_acceptation(
            _dossier(), _proforma(service="PLATEFORME"),
            boite=boite, slug="cabinet-voisin", tenant="tnt-voisin",
            a_l_instant=T0 + timedelta(days=3), identifiant_evenement="ev-2",
            reference_externe="MTN-2",
        )
        assert boite.a_publier()[0].charge["ouvre_un_locataire"] is True

    def test_l_abonne_ne_touche_a_rien_quand_il_n_y_a_rien_a_ouvrir(self):
        """⚠️ Il ne LÈVE pas non plus : « cette vente n'ouvre pas de locataire »
        est une consigne, pas une charge incomplète."""
        from app.contextes.tenants.application.ouverture_sur_paiement import (
            ouvrir_sur_paiement,
        )
        from app.infrastructure.depots_orchestration import DepotExecutionsMemoire
        from app.orchestration.boite_d_envoi import EvenementSortant

        executions = DepotExecutionsMemoire()
        resultat = ouvrir_sur_paiement(
            EvenementSortant(
                identifiant="ev-3", nom="PaiementEncaissé", cle="dos-1",
                charge={"ouvre_un_locataire": False, "service": "CREATION"},
            ),
            provisionneur=object(),
            executions=executions,
            a_l_instant=T0,
        )
        assert resultat.sans_objet is True
        assert resultat.a_progresse is False
        assert resultat.tenant_utilisable is False
        assert resultat.etapes_substituees == ()

    def test_un_evenement_d_avant_la_distinction_ouvre_toujours_son_locataire(self):
        """⚠️ ABSENT VAUT VRAI. Il en dort dans la boîte d'envoi, déposés avant
        que cette clé n'existe : les ignorer laisserait un cabinet payé sans
        espace, et personne ne saurait pourquoi."""
        from app.contextes.tenants.application.ouverture_sur_paiement import (
            ChargeIncomplete,
            ouvrir_sur_paiement,
        )
        from app.infrastructure.depots_orchestration import DepotExecutionsMemoire
        from app.orchestration.boite_d_envoi import EvenementSortant

        with pytest.raises(ChargeIncomplete):
            ouvrir_sur_paiement(
                EvenementSortant(
                    identifiant="ev-4", nom="PaiementEncaissé", cle="dos-1", charge={}
                ),
                provisionneur=object(),
                executions=DepotExecutionsMemoire(),
                a_l_instant=T0,
            )

