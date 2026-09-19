"""Le repli de canal, et la garantie qui va avec.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER DOIT PROUVER

**Le parcours entier fonctionne sans aucune plateforme extérieure.**

Ce n'est pas une préférence de conception, c'est une exigence : le centre
travaille depuis des années sans messagerie instantanée, et le système doit
pouvoir en faire autant, du dépôt de la demande jusqu'au paiement.

La classe `TestLeParcoursEntierSansMessagerie` est celle qui compte. Elle déroule
le parcours de bout en bout avec la messagerie désactivée au référentiel, et
échouerait si une seule étape en dépendait.

Le reste éprouve la règle de repli elle-même.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotDossiersMemoire,
)
from app.contextes.souscription.adaptateurs.sortant.plan_de_contact import (
    charger_le_plan_de_contact,
)
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.souscription.application.acquisition import deposer_une_demande
from app.contextes.souscription.application.affectation import affecter_le_dossier
from app.contextes.souscription.application.conversation import enregistrer_un_appel
from app.contextes.souscription.domaine.affectation import Candidature
from app.contextes.souscription.domaine.canaux import (
    CANAL_PLANCHER,
    AucunCanalJoignable,
    EtatCanal,
    PlanDeContact,
    canal_a_employer,
    plan_depuis,
)
from app.contextes.souscription.domaine.conversation import (
    AppelJournalise,
    CanalMessage,
    IssueAppel,
    ouvrir_un_fil,
)
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.infrastructure.config import RACINE_DEPOT

T0 = datetime(2026, 9, 9, 10, 0)
LE_JOUR = date(2026, 9, 9)
MESSAGERIE = RACINE_DEPOT / "Docs" / "referentiel" / "messagerie"
AFFECTATION = RACINE_DEPOT / "Docs" / "referentiel" / "affectation"


def _plan(**etats) -> PlanDeContact:
    """Un plan d'essai. Par défaut : les trois canaux actifs."""
    defauts = {"appel": True, "courriel": True, "whatsapp": True}
    defauts.update(etats)
    return plan_depuis(
        [
            EtatCanal(
                canal=canal,
                actif=defauts[nom],
                rang=rang,
                motif="" if defauts[nom] else "essai",
            )
            for rang, (nom, canal) in enumerate(
                (
                    ("appel", Canal.APPEL),
                    ("courriel", Canal.COURRIEL),
                    ("whatsapp", Canal.WHATSAPP),
                )
            )
        ]
    )


def _demande(identifiant="dc-1", **surcharges) -> DemandeDeContact:
    defauts = {
        "identifiant": identifiant,
        "deposee_le": T0,
        "nom": "Abena Ndzana",
        "telephone": "699112233",
        "service_souhaite": "creation-sarl",
        "canal_prefere": Canal.APPEL,
        "consentement": Consentement(
            accorde=False, recueilli_le=T0, version_du_texte="v1"
        ),
    }
    return DemandeDeContact(**{**defauts, **surcharges})


# ── La garantie ───────────────────────────────────────────────────────────────


class TestLeParcoursEntierSansMessagerie:
    """Le parcours, de bout en bout, la messagerie désactivée au référentiel.

    ⚠️ **Si un jour ce test échoue, c'est qu'une dépendance dure à une plateforme
    tierce s'est réintroduite.** Ne pas le contourner en activant la messagerie :
    chercher l'étape qui en dépend, et la rendre indifférente.
    """

    @pytest.fixture
    def plan(self):
        """Le plan **réel** du référentiel, pas un plan d'essai.

        C'est le point : aujourd'hui, en production, la messagerie est inactive.
        Le test doit donc porter sur ce que le centre a réellement arrêté.
        """
        return charger_le_plan_de_contact(MESSAGERIE)

    def test_la_messagerie_est_bien_inactive_aujourd_hui(self, plan):
        assert Canal.WHATSAPP not in plan.actifs()
        assert CANAL_PLANCHER in plan.actifs()

    def test_du_depot_a_la_conversation_sans_toucher_a_la_messagerie(self, plan):
        depot = DepotDossiersMemoire()
        grille = charger_la_grille_d_affectation(AFFECTATION)

        # 1 · Le visiteur dépose, sans consentir à la messagerie.
        resultat = deposer_une_demande(_demande(), depot, reference="dos-1")
        assert resultat.dossier.etat is EtatDossier.DEPOSEE

        # 2 · Le système affecte. L'affectation n'a jamais rien su des canaux.
        affectation = affecter_le_dossier(
            resultat.dossier,
            [
                Candidature(
                    responsable="awono",
                    service="creation-sarl",
                    region_demande="Douala",
                    agence_responsable="Douala",
                    competences=("creation",),
                    competence_requise="creation",
                )
            ],
            grille,
            T0,
            a_la_date=LE_JOUR,
        )
        assert affectation.dossier.etat is EtatDossier.AFFECTEE

        # 3 · Le responsable choisit son canal. La règle rend l'appel.
        repli = canal_a_employer(
            _demande().canal_prefere,
            plan,
            consentement_vaut=False,
            a_un_courriel=False,
            messagerie_prete=False,
        )
        assert repli.canal is Canal.APPEL
        assert repli.replie is False

        # 4 · Il appelle. Le dossier entre en conversation.
        echange = enregistrer_un_appel(
            affectation.dossier,
            ouvrir_un_fil("dos-1", "+237699112233"),
            AppelJournalise(
                identifiant="a-1",
                a_l_instant=T0 + timedelta(hours=1),
                duree_secondes=240,
                issue=IssueAppel.REPONDU,
            ),
            T0 + timedelta(hours=1),
        )
        assert echange.dossier.etat is EtatDossier.EN_CONVERSATION

        # 5 · La suite du parcours ne dépend d'aucun canal.
        dossier = echange.dossier
        for geste in (
            lambda d: d.qualifier(T0 + timedelta(days=1)),
            lambda d: d.chiffrer(T0 + timedelta(days=1)),
            lambda d: d.emettre_la_proforma(T0 + timedelta(days=2)),
            lambda d: d.accepter(T0 + timedelta(days=3)),
            lambda d: d.encaisser(T0 + timedelta(days=3)),
        ):
            dossier = geste(dossier)
        assert dossier.etat is EtatDossier.PAYEE

    def test_le_courriel_suffit_aussi(self, plan):
        """Un prospect qui a laissé une adresse et pas de consentement se sert
        par écrit, sans que la messagerie n'existe."""
        repli = canal_a_employer(
            Canal.COURRIEL,
            plan,
            consentement_vaut=False,
            a_un_courriel=True,
            messagerie_prete=False,
        )
        assert repli.canal is Canal.COURRIEL

        fil = ouvrir_un_fil("dos-1", "+237699112233")
        envoi = fil.preparer_message_libre(
            "m-1",
            "Bonjour, je reviens vers vous au sujet de votre demande.",
            T0,
            canal=CanalMessage.COURRIEL,
            consentement_vaut=False,
        )
        assert envoi.canal is CanalMessage.COURRIEL

    def test_une_preference_pour_la_messagerie_se_replie_sans_bloquer(self, plan):
        """Le cas le plus important : le visiteur a **coché** la messagerie, et
        elle n'existe pas encore. Sa demande ne se perd pas, elle se sert
        autrement."""
        repli = canal_a_employer(
            Canal.WHATSAPP,
            plan,
            consentement_vaut=True,
            a_un_courriel=False,
            messagerie_prete=False,
        )
        assert repli.canal is Canal.APPEL
        assert repli.replie is True
        assert "désactivé" in repli.ecartes[0]

    def test_la_preference_du_client_n_est_pas_reecrite(self, plan):
        """Le repli est une décision d'exécution. Réécrire la préférence
        effacerait ce que le client avait demandé, et l'on ne saurait plus, en
        rétablissant le canal, qui rebasculer."""
        demande = _demande(
            canal_prefere=Canal.WHATSAPP,
            consentement=Consentement(
                accorde=True, recueilli_le=T0, version_du_texte="v1"
            ),
        )
        canal_a_employer(
            demande.canal_prefere,
            plan,
            consentement_vaut=True,
            a_un_courriel=False,
            messagerie_prete=False,
        )
        assert demande.canal_prefere is Canal.WHATSAPP


# ── La règle de repli ─────────────────────────────────────────────────────────


class TestOrdreDEssai:
    def test_la_preference_du_client_passe_avant_le_plan(self):
        """Même si le plan classe l'appel en premier : c'est lui qui a demandé."""
        repli = canal_a_employer(
            Canal.COURRIEL, _plan(), consentement_vaut=True, a_un_courriel=True
        )
        assert repli.canal is Canal.COURRIEL
        assert repli.replie is False

    def test_le_repli_suit_le_rang_du_plan(self):
        plan = plan_depuis(
            [
                EtatCanal(canal=Canal.COURRIEL, rang=0),
                EtatCanal(canal=Canal.APPEL, rang=1),
            ]
        )
        repli = canal_a_employer(
            Canal.WHATSAPP, plan, consentement_vaut=True, a_un_courriel=True
        )
        assert repli.canal is Canal.COURRIEL

    def test_un_canal_absent_du_plan_n_est_pas_employe(self):
        """Ne pas le déclarer, c'est ne pas l'exploiter. L'essayer quand même
        contournerait une décision du centre."""
        plan = plan_depuis([EtatCanal(canal=Canal.APPEL, rang=0)])
        repli = canal_a_employer(
            Canal.COURRIEL, plan, consentement_vaut=True, a_un_courriel=True
        )
        assert repli.canal is Canal.APPEL
        assert "pas déclaré" in repli.ecartes[0]

    def test_les_motifs_d_ecartement_sont_conserves_dans_l_ordre(self):
        """Sans cela, on ne saura pas pourquoi un client qui avait coché la
        messagerie a reçu un appel."""
        repli = canal_a_employer(
            Canal.WHATSAPP,
            _plan(whatsapp=False),
            consentement_vaut=True,
            a_un_courriel=False,
        )
        assert repli.canal is Canal.APPEL
        assert len(repli.ecartes) == 1
        assert "WHATSAPP" in repli.ecartes[0]


class TestConditionsParCanal:
    def test_la_messagerie_exige_le_consentement(self):
        repli = canal_a_employer(
            Canal.WHATSAPP, _plan(), consentement_vaut=False, a_un_courriel=False
        )
        assert repli.canal is Canal.APPEL
        assert "sans consentement" in repli.ecartes[0]

    def test_la_messagerie_exige_d_etre_prete(self):
        """La condition que le pas 5 avait oublié de rendre facultative."""
        repli = canal_a_employer(
            Canal.WHATSAPP,
            _plan(),
            consentement_vaut=True,
            a_un_courriel=False,
            messagerie_prete=False,
        )
        assert repli.canal is Canal.APPEL
        assert "n'est pas prête" in repli.ecartes[0]

    def test_le_courriel_exige_une_adresse(self):
        """Elle est facultative au dépôt, donc souvent absente."""
        repli = canal_a_employer(
            Canal.COURRIEL, _plan(), consentement_vaut=True, a_un_courriel=False
        )
        assert repli.canal is Canal.APPEL
        assert "sans adresse" in repli.ecartes[0]

    def test_l_appel_n_exige_rien(self):
        """C'est ce qui en fait le plancher : un téléphone n'a pas d'API."""
        repli = canal_a_employer(
            Canal.APPEL,
            _plan(courriel=False, whatsapp=False),
            consentement_vaut=False,
            a_un_courriel=False,
            messagerie_prete=False,
        )
        assert repli.canal is CANAL_PLANCHER

    def test_la_messagerie_prete_et_consentie_est_employee(self):
        """Le jour où la plateforme s'ouvre, un `actif: true` suffit."""
        repli = canal_a_employer(
            Canal.WHATSAPP,
            _plan(),
            consentement_vaut=True,
            a_un_courriel=False,
            messagerie_prete=True,
        )
        assert repli.canal is Canal.WHATSAPP
        assert repli.replie is False


class TestPlusAucunCanal:
    def test_desactiver_l_appel_prive_le_centre_de_son_plancher(self):
        """Rare, et cela doit le rester. Lever plutôt que de rendre `None` :
        rendre `None` obligerait chaque appelant à traiter un cas qui ne devrait
        jamais arriver, et l'un d'eux le traiterait en ne faisant rien."""
        plan = _plan(appel=False, courriel=False, whatsapp=False)
        with pytest.raises(AucunCanalJoignable) as echec:
            canal_a_employer(
                Canal.APPEL, plan, consentement_vaut=False, a_un_courriel=False
            )
        assert "APPEL" in str(echec.value)
        assert "sans dépendance extérieure" in str(echec.value)


class TestPlanDeContact:
    def test_un_canal_inactif_doit_dire_pourquoi(self):
        """« La messagerie n'est pas encore ouverte » se comprend ; « canal
        indisponible » fait ouvrir un incident pour une décision volontaire."""
        with pytest.raises(ValueError, match="sans motif"):
            EtatCanal(canal=Canal.WHATSAPP, actif=False, rang=2)

    def test_un_canal_declare_deux_fois_est_refuse(self):
        """Lequel des deux états fait foi dépendrait de l'ordre de lecture du
        fichier."""
        with pytest.raises(ValueError, match="deux fois"):
            plan_depuis(
                [
                    EtatCanal(canal=Canal.APPEL, rang=0),
                    EtatCanal(canal=Canal.APPEL, rang=1),
                ]
            )

    def test_les_actifs_sortent_dans_l_ordre_du_rang(self):
        """Sert aussi à la vitrine : proposer un canal que le centre n'exploite
        pas produirait une préférence impossible à honorer."""
        plan = plan_depuis(
            [
                EtatCanal(canal=Canal.COURRIEL, rang=5),
                EtatCanal(canal=Canal.APPEL, rang=1),
                EtatCanal(canal=Canal.WHATSAPP, rang=3, actif=False, motif="fermé"),
            ]
        )
        assert plan.actifs() == (Canal.APPEL, Canal.COURRIEL)


class TestPlanDuReferentiel:
    @pytest.fixture(scope="class")
    def plan(self):
        return charger_le_plan_de_contact(MESSAGERIE)

    def test_il_se_charge(self, plan):
        assert len(plan.canaux) == 3

    def test_l_appel_est_actif(self, plan):
        """⚠️ Le désactiver priverait le centre de son seul canal sans dépendance
        extérieure, et ferait dépendre le parcours entier d'une plateforme."""
        assert plan.est_actif(CANAL_PLANCHER)

    def test_la_messagerie_declare_pourquoi_elle_est_fermee(self, plan):
        etat = plan.etat(Canal.WHATSAPP)
        assert etat.actif is False
        assert "modèle" in etat.motif

    def test_un_fichier_absent_ne_se_replie_pas_sur_un_plan_invente(self, tmp_path):
        """Démarrer avec un plan inventé ferait joindre les clients par un canal
        que personne n'a décidé d'exploiter."""
        with pytest.raises(FileNotFoundError):
            charger_le_plan_de_contact(tmp_path)
