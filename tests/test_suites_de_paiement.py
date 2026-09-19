"""Ce qu'un paiement déclenche, selon ce qu'il règle.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le mécanisme de paiement — rapprochement, idempotence, contrôle de montant,
expiration — est imposé par le prestataire et ne dépend pas de ce qu'on vend. La
**suite** en dépend : une souscription réglée s'active, une proforma réglée ouvre
un tenant.

Tout était écrit pour la souscription seule, et le parcours d'acquisition faisait
donc saisir l'encaissement à la main pendant que l'intégration du prestataire
tournait à côté pour l'autre flux.

Ces cas gardent les deux bords du nouveau mécanisme : **ce qui arrive quand une
nature n'a pas de suite**, et **ce qui arrive quand la suite ne peut pas aboutir**.
Les deux ont été écrits parce qu'une mutation y survivait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    _dossiers,
    _proformas,
    reinitialiser_dossiers,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotPaiementsMemoire,
)
from app.contextes.souscription.api import (
    Canal,
    Consentement,
    DemandeDeContact,
    EvenementPaiement,
    NaturePaiement,
    Paiement,
    StrategieRapprochement,
    TarifArrete,
    appliquer_evenement,
    emettre,
    ouvrir_un_dossier,
    traiter_notification,
)
from app.contextes.souscription.application.encaissement_du_parcours import (
    SuiteDeProforma,
)

T0 = datetime(2026, 9, 11, 9, 0)
NUMERO = "PRO-2026-0099"
REFERENCE = "dos-suite"


class _JournalFactice:
    """Un journal qui retient ce qu'on lui donne. Les actions sont l'objet du test :
    c'est sous ces noms-là qu'un exploitant cherchera."""

    def __init__(self) -> None:
        self.entrees: list[dict] = []

    def ajouter(self, **entree) -> None:
        self.entrees.append(entree)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.entrees]


class _FournisseurFactice:
    def lire_notification(self, charge):
        return charge.get("_evenement")

    def identifiant_marchand(self) -> str:
        return ""


def _suite(journal) -> SuiteDeProforma:
    """La suite, câblée sur les magasins en vigueur.

    ⚠️ Les dépôts sont **injectés**, comme le comptoir le fait : la suite ne va
    rien chercher elle-même, sans quoi la couche application dépendrait des
    adaptateurs. Le test d'architecture a refusé la première version.
    """
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        _boite_du_parcours,
    )

    return SuiteDeProforma(
        dossiers=_dossiers(),
        proformas=_proformas(),
        boite=_boite_du_parcours(),
        journal=journal,
    )


def _paiement(nature=NaturePaiement.PROFORMA, reference=NUMERO) -> Paiement:
    return Paiement(
        identifiant="pay-essai",
        nature=nature,
        reference_reglee=reference,
        cle_idempotence="cle-idempotence-9999",
        montant=Decimal("250000"),
        telephone="699112233",
        initie_le=T0,
    )


def _reussi() -> EvenementPaiement:
    return EvenementPaiement(
        identifiant_produit="cle-idempotence-9999",
        reference_externe="TARA-9999",
        reussi=True,
        montant=Decimal("250000"),
        telephone="699112233",
        recu_le=T0,
    )


@pytest.fixture(autouse=True)
def parcours_neuf():
    reinitialiser_dossiers()
    yield
    reinitialiser_dossiers()


@pytest.fixture
def dossier_accepte():
    """Un dossier jusqu'à l'acceptation, **sans adresse retenue**."""
    demande = DemandeDeContact(
        identifiant="dc-suite",
        deposee_le=T0,
        nom="Boulangerie du Wouri",
        telephone="+237690112233",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(
            accorde=False, recueilli_le=T0, version_du_texte="v1"
        ),
    )
    dossier = ouvrir_un_dossier(REFERENCE, demande)
    dossier = dossier.affecter("mballa", T0, motif="essai")
    dossier = dossier.premier_contact(T0).qualifier(T0)
    dossier = dossier.chiffrer(T0).emettre_la_proforma(T0).accepter(T0)
    _dossiers().enregistrer(dossier)

    tarif = TarifArrete(
        montant=Decimal("250000"),
        plancher=Decimal("200000"),
        reference=Decimal("250000"),
        plafond=Decimal("375000"),
        version_bareme="2026.1",
        chiffre_par="mballa",
        valide_par="direction",
        arrete_le=T0,
    )
    _proformas().enregistrer(
        emettre(
            numero=NUMERO,
            dossier=REFERENCE,
            service="creation-sarl",
            tarif=tarif,
            contenu=b"%PDF",
            modele="creation-sarl",
            version_modele="2026.1",
            a_l_instant=T0,
        ).transmise(T0)
    )
    return dossier


class TestQuandLAdresseNAPasEteRetenue:
    """⚠️ **Le cas que l'automatisation rend possible, et qui n'existait pas avant.**

    Tant qu'un humain confirmait l'encaissement, il fournissait le slug dans le
    même geste : il ne pouvait pas manquer. Dès lors que l'opérateur notifie tout
    seul, un règlement peut arriver sur un dossier dont personne n'a préparé
    l'espace — par exemple si le paiement a été initié par un autre chemin.
    """

    def test_le_tenant_ne_s_ouvre_pas_et_le_dossier_ne_bouge_pas(
        self, dossier_accepte
    ):
        journal = _JournalFactice()
        suite = _suite(journal)

        aboutissement = suite.encaisser(_paiement(), T0)

        assert aboutissement.abouti is False
        assert "aucune adresse retenue" in aboutissement.message
        # ⚠️ Le dossier reste à l'acceptation : rien n'a été encaissé côté métier,
        # et il reste donc visible dans la file des impayées.
        assert _dossiers().lire(REFERENCE).etat.value == "ACCEPTEE"

    def test_l_echec_se_cherche_en_une_requete(self, dossier_accepte):
        """⚠️ L'argent est arrivé et rien ne s'est ouvert : c'est exactement le cas
        qui réclame un humain, et il doit se trouver par son nom d'action."""
        journal = _JournalFactice()
        _suite(journal).encaisser(_paiement(), T0)

        assert journal.actions() == ["paiement.ouverture_impossible"]

    def test_avec_une_adresse_retenue_le_dossier_est_encaisse(self, dossier_accepte):
        """⚠️ La contre-épreuve. Sans elle, une suite qui échouerait toujours
        passerait les deux cas précédents sans broncher."""
        _dossiers().enregistrer(dossier_accepte.retenir_le_slug("boulangerie-wouri"))
        journal = _JournalFactice()

        aboutissement = _suite(journal).encaisser(_paiement(), T0)

        assert aboutissement.abouti is True
        assert _dossiers().lire(REFERENCE).etat.value == "PAYEE"
        assert journal.actions() == []


class TestQuandUneNatureNAPasDeSuite:
    """⚠️ Une erreur de câblage, pas un fait métier : l'argent est arrivé et
    personne ne sait quoi en faire."""

    def test_rien_ne_leve_et_le_rapprochement_est_annonce_manquant(self):
        journal = _JournalFactice()
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement())

        resultat = traiter_notification(
            {"_evenement": _reussi()},
            fournisseur=_FournisseurFactice(),
            paiements=paiements,
            # La table ne connaît que les souscriptions : le paiement est une
            # proforma.
            suites={},
            journal=journal,
            a_l_instant=T0,
        )

        assert resultat.rapproche is False
        assert resultat.demande_une_intervention is True
        assert "aucune suite n'est câblée" in resultat.message
        assert journal.actions() == ["paiement.suite_absente"]

    def test_le_paiement_n_est_pas_valide_a_l_aveugle(self):
        """⚠️ **Il reste en attente**, et c'est ce qui compte : le valider sans que
        sa suite s'exécute effacerait la seule trace disant qu'il reste à traiter.
        La réconciliation le reprendra."""
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement())

        traiter_notification(
            {"_evenement": _reussi()},
            fournisseur=_FournisseurFactice(),
            paiements=paiements,
            suites={},
            journal=_JournalFactice(),
            a_l_instant=T0,
        )

        assert paiements.lire("pay-essai").statut.value == "EN_ATTENTE"


class TestLeRefusDUneProforma:
    def test_le_dossier_reste_a_l_acceptation(self, dossier_accepte):
        """*L'état suit ce que le geste prouve, pas ce qu'il espère.*

        ⚠️ Un client dont le code a expiré recommence : il n'a rien refusé. Classer
        sans suite sur un refus de paiement ferait décider la machine à la place du
        centre, et le dossier doit rester dans la file des impayées.
        """
        journal = _JournalFactice()
        suite = _suite(journal)

        suite.refuser(_paiement(), "code expiré", T0)

        assert _dossiers().lire(REFERENCE).etat.value == "ACCEPTEE"
        assert journal.actions() == ["paiement.proforma_refusee"]

    def test_un_refus_ne_lance_aucune_exception_meme_sans_proforma(self):
        """La suite ne lève jamais : une exception ici ferait renvoyer le
        prestataire en boucle."""
        journal = _JournalFactice()
        _suite(journal).refuser(
            _paiement(reference="PRO-INEXISTANTE"), "refus", T0
        )
        assert journal.actions() == ["paiement.proforma_refusee"]


class TestLesDeuxCheminsSontLeMeme:
    def test_la_reconciliation_emprunte_la_meme_suite_que_la_notification(
        self, dossier_accepte
    ):
        """⚠️ `appliquer_evenement` est partagée par les deux chemins depuis le
        premier jour, précisément pour qu'aucun n'active là où l'autre rejette.

        Ce cas vérifie que le partage tient **avec la suite enfichable** : c'est
        elle qui porte désormais l'effet, et la partager à moitié serait pire que
        pas du tout.
        """
        _dossiers().enregistrer(dossier_accepte.retenir_le_slug("boulangerie-wouri"))
        paiements = DepotPaiementsMemoire()
        paiements.enregistrer(_paiement())
        journal = _JournalFactice()

        resultat = appliquer_evenement(
            paiements.lire("pay-essai"),
            _reussi(),
            strategie=StrategieRapprochement.IDENTIFIANT,
            paiements=paiements,
            suite=_suite(journal),
            journal=journal,
            a_l_instant=T0,
        )

        assert resultat.statut.value == "VALIDE"
        assert resultat.reference_reglee == NUMERO
        assert _dossiers().lire(REFERENCE).etat.value == "PAYEE"
