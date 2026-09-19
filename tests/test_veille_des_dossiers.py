"""La veille des dossiers : ce qui dort est dit **une fois**.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES CAS GARDENT, ET POURQUOI ILS ONT ÉTÉ ÉCRITS

Trois capacités du domaine existaient, testées, sans aucun appelant :
`classer_sans_suite`, `en_souffrance`, `reaffecter_le_dossier`. Le parcours savait
avancer, il ne savait pas se dégager.

⚠️ **La conséquence a été mesurée sur une base réelle avant d'écrire une ligne** :
trois prospects muets pesaient encore trois dossiers huit mois plus tard, parce que
rien ne mettait jamais un dossier à l'état terminal `SANS_SUITE`. Et
`charge_par_responsable` promettait, en commentaire, qu'« un ancien collaborateur
productif ne paraîtrait pas surchargé pour toujours ».

La propriété qui compte ici n'est donc pas « l'alerte part » : c'est **« elle ne
part qu'une fois »**. Un dossier oublié six mois, balayé tous les quarts d'heure,
produirait plus de dix-sept mille alertes pour un seul fait, et la seule chose
qu'on apprendrait est à ne plus les lire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotDossiersMemoire,
)
from app.contextes.souscription.application.veille_des_dossiers import (
    NOM_EN_SOUFFRANCE,
    veiller_les_dossiers,
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
from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire

T0 = datetime(2026, 9, 1, 9, 0)

#: Deux états seulement, pour que les cas montrent qu'un état hors table n'est
#: pas parcouru.
DELAIS = {
    EtatDossier.AFFECTEE: timedelta(hours=48),
    EtatDossier.EN_CONVERSATION: timedelta(hours=168),
}


def _demande(identifiant: str = "D1") -> DemandeDeContact:
    return DemandeDeContact(
        identifiant=identifiant,
        deposee_le=T0,
        nom="Abena Ndzana",
        telephone="699112233",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(
            accorde=False, recueilli_le=T0, version_du_texte="consentement-whatsapp-v1"
        ),
    )


def _affecte(reference: str = "DOS-1", *, a: datetime = T0):
    return ouvrir_un_dossier(reference, _demande(reference)).affecter(
        "mballa", a, motif="tour de rôle"
    )


def _veiller(depot, boite, quand: datetime, delais=None):
    return veiller_les_dossiers(
        delais if delais is not None else DELAIS,
        dossiers=depot,
        boite=boite,
        a_l_instant=quand,
        identifiant=lambda suffixe: f"veille-{suffixe}",
    )


@pytest.fixture
def depot() -> DepotDossiersMemoire:
    return DepotDossiersMemoire()


@pytest.fixture
def boite() -> BoiteDEnvoiMemoire:
    return BoiteDEnvoiMemoire()


class TestCeQuiEstSignale:
    def test_un_dossier_dans_le_delai_ne_produit_rien(self, depot, boite):
        """Le délai est de 48 heures ; à 47, il n'y a rien à dire.

        ⚠️ Ce cas garde la borne dans le bon sens. Une veille qui signale trop tôt
        se fait désactiver la première semaine, et personne ne la rallume.
        """
        depot.enregistrer(_affecte())
        rapport = _veiller(depot, boite, T0 + timedelta(hours=47))

        assert rapport.signales == ()
        assert boite.tous() == []
        # Examiné, et pourtant rien signalé : les deux nombres ne disent pas la
        # même chose, et c'est la raison d'être de `examines`.
        assert rapport.examines == 1

    def test_un_dossier_au_dela_du_delai_est_signale_une_fois(self, depot, boite):
        depot.enregistrer(_affecte())
        rapport = _veiller(depot, boite, T0 + timedelta(hours=49))

        assert rapport.signales == ("DOS-1",)
        (evenement,) = boite.tous()
        assert evenement.nom == NOM_EN_SOUFFRANCE
        # La clé d'ordre est la référence seule : deux alertes du même dossier se
        # suivent, celles de dossiers différents ne s'attendent pas.
        assert evenement.cle == "DOS-1"
        assert evenement.charge["etat"] == EtatDossier.AFFECTEE.value
        assert evenement.charge["responsable"] == "mballa"
        assert evenement.charge["immobile_depuis_heures"] == 49
        assert evenement.charge["delai_heures"] == 48

    def test_l_alerte_ne_transporte_aucune_donnee_de_prospect(self, depot, boite):
        """⚠️ Ni le nom, ni le numéro, ni le message.

        L'alerte est interne mais elle traverse une file, des journaux et des
        sauvegardes, qui la recopient tous. Ce cas tombe le jour où quelqu'un
        ajoute « le nom, ce serait plus pratique ».
        """
        depot.enregistrer(_affecte())
        _veiller(depot, boite, T0 + timedelta(hours=49))

        (evenement,) = boite.tous()
        contenu = str(evenement.charge)
        assert "Abena" not in contenu
        assert "699112233" not in contenu

    def test_un_etat_hors_table_n_est_pas_sous_veille(self, depot, boite):
        """`QUALIFIÉE` n'a pas de délai : le chiffrage suit la qualification en
        quelques secondes, et une alerte sur un état qu'on ne fait que traverser
        serait du bruit pur."""
        dossier = _affecte().premier_contact(T0).qualifier(T0)
        assert dossier.etat is EtatDossier.QUALIFIEE
        depot.enregistrer(dossier)

        rapport = _veiller(depot, boite, T0 + timedelta(days=365))

        assert rapport.examines == 0
        assert boite.tous() == []


class TestCeQuiNEstPasRedit:
    def test_un_second_passage_ne_redepose_rien(self, depot, boite):
        """⚠️ **Le cas central de ce fichier.**

        Sans marqueur, l'ordonnanceur redéposerait à chaque passage. Et la boîte
        d'envoi n'y ferait pas obstacle : `deposer` réécrit la ligne de même
        identifiant et remet `publie_le` à `None`, donc l'alerte **repartirait**
        au lieu d'être ignorée.
        """
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=49)

        premier = _veiller(depot, boite, tard)
        second = _veiller(depot, boite, tard + timedelta(hours=1))

        assert premier.signales == ("DOS-1",)
        assert second.signales == ()
        assert second.deja_signales == 1
        assert len(boite.tous()) == 1

    def test_un_dossier_qui_bouge_puis_redort_est_signale_de_nouveau(
        self, depot, boite
    ):
        """Le marqueur suit l'attente, pas le dossier.

        ⚠️ Un dossier signalé en `AFFECTÉE`, passé en conversation, puis rendormi
        trois semaines, doit être signalé une seconde fois : c'est une seconde
        attente. Garder le marqueur ferait qu'un dossier signalé une fois ne le
        serait plus jamais, quoi qu'il arrive ensuite.
        """
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=49)
        _veiller(depot, boite, tard)

        # Il bouge : le marqueur tombe, l'attente recommence.
        depot.enregistrer(depot.lire("DOS-1").premier_contact(tard))
        rapport = _veiller(depot, boite, tard + timedelta(hours=169))

        assert rapport.signales == ("DOS-1",)
        assert len(boite.tous()) == 2

    def test_les_deux_alertes_ont_des_identifiants_distincts(self, depot, boite):
        """⚠️ Sinon la seconde écraserait la première dans la boîte.

        L'identifiant porte l'état **et** l'instant d'entrée dans cet état. Deux
        attentes du même dossier sont deux faits ; leur donner la même identité en
        perdrait un, silencieusement, puisque le dépôt est une réécriture.
        """
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=49)
        _veiller(depot, boite, tard)
        depot.enregistrer(depot.lire("DOS-1").premier_contact(tard))
        _veiller(depot, boite, tard + timedelta(hours=169))

        identifiants = {e.identifiant for e in boite.tous()}
        assert len(identifiants) == 2

    def test_signaler_ne_fait_pas_avancer_le_dossier(self, depot, boite):
        """Signaler n'est pas agir.

        ⚠️ Faire avancer un dossier parce qu'on a prévenu quelqu'un remettrait
        `depuis_le` à l'instant du signalement, et le dossier cesserait d'être en
        retard **du fait qu'on a dit qu'il l'était**. La veille s'éteindrait
        elle-même.
        """
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=49)
        _veiller(depot, boite, tard)

        apres = depot.lire("DOS-1")
        assert apres.etat is EtatDossier.AFFECTEE
        assert apres.depuis_le == T0
        assert apres.signale_le == tard
        assert apres.en_souffrance(tard, DELAIS) is True


class TestCeQueLaVeilleNeFaitPas:
    def test_elle_ne_classe_jamais_sans_suite(self, depot, boite):
        """*L'état suit ce que le geste prouve, pas ce qu'il espère.*

        ⚠️ Un prospect qui ne répond pas n'a rien refusé. Le silence prouve
        qu'aucun échange n'a eu lieu, pas qu'il n'y en aura jamais. Classer
        sur un silence ferait décider la machine à la place du centre, et sur
        la seule information qu'elle n'a pas.
        """
        depot.enregistrer(_affecte())
        _veiller(depot, boite, T0 + timedelta(days=400))

        assert depot.lire("DOS-1").etat is EtatDossier.AFFECTEE
        assert depot.lire("DOS-1").ouvert is True

    def test_elle_ne_touche_pas_aux_dossiers_fermes(self, depot, boite):
        """Un dossier classé sans suite ne remonte jamais, quel que soit son âge.

        Sans cette garde, tous les dossiers morts de l'année reviendraient dans
        l'alerte le jour où quelqu'un ajoute un délai par erreur.
        """
        depot.enregistrer(_affecte().classer_sans_suite(T0, motif="prix"))

        rapport = _veiller(depot, boite, T0 + timedelta(days=400))

        assert rapport.examines == 0
        assert boite.tous() == []


class TestLeRapport:
    def test_il_distingue_ce_qui_dort_de_ce_qui_n_a_pas_ete_lu(self, depot, boite):
        """Zéro signalé sur zéro examiné et zéro signalé sur quarante examinés
        n'appellent pas la même enquête : dans un cas la file est vide, dans
        l'autre elle est saine."""
        vide = _veiller(depot, boite, T0)
        assert (vide.examines, vide.signales) == (0, ())

        depot.enregistrer(_affecte())
        sain = _veiller(depot, boite, T0 + timedelta(hours=1))
        assert (sain.examines, sain.signales) == (1, ())

    def test_le_resume_compte_les_alertes_non_traitees(self, depot, boite):
        """⚠️ `deja_signales` grossit tant que personne n'agit, et c'est voulu :
        c'est le seul nombre qui dit qu'une alerte n'a pas été traitée."""
        depot.enregistrer(_affecte())
        tard = T0 + timedelta(hours=49)
        _veiller(depot, boite, tard)

        rapport = _veiller(depot, boite, tard + timedelta(days=30))

        assert rapport.deja_signales == 1
        assert "1 déjà signalé" in rapport.resume
