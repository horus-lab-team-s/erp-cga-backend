"""On ne demande une adresse d'espace que s'il y a un espace à adresser.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER PROTÈGE

Depuis le 27 septembre 2026, seule l'offre de plateforme ouvre un locataire. Une
création d'entreprise ouvre un dossier de formalité, et rien d'autre.

L'événement de paiement le disait. Tout le reste du tunnel disait le contraire :
l'encaissement exigeait une « adresse de l'espace » pour toute vente, la retenait
sur le dossier, et la fiche l'affichait ensuite comme « l'espace » d'une cliente
qui n'en aurait jamais, sous un sablier « l'ouverture n'a pas encore démarré ».

Trouvé le 13 octobre 2026 en filmant le parcours : dix minutes après le
règlement, l'écran attendait toujours une ouverture que la règle exclut.

⚠️ CES CAS SONT ÉPROUVÉS DANS LES DEUX SENS. En remettant la retenue
inconditionnelle de l'adresse dans `encaisser_l_acceptation`, les deux premiers
rougissent ; en retirant le refus d'une plateforme sans adresse, le troisième.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.contextes.souscription.application.encaissement_du_parcours import (
    EncaissementRefuse,
    encaisser_l_acceptation,
    ouvre_un_locataire,
)
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.infrastructure.depots_orchestration import BoiteDEnvoiMemoire
from tests.test_relance import T0, _dossier, _proforma


def _encaisser(*, service: str, slug: str, boite: BoiteDEnvoiMemoire | None = None):
    return encaisser_l_acceptation(
        _dossier(),
        _proforma(service=service),
        boite=boite or BoiteDEnvoiMemoire(),
        slug=slug,
        tenant=f"tnt-{slug}" if slug else "",
        a_l_instant=T0 + timedelta(days=3),
        identifiant_evenement="ev-1",
    )


class TestUnePrestationNOuvrePasDEspace:
    def test_une_creation_se_regle_sans_aucune_adresse(self):
        resultat = _encaisser(service="CREATION", slug="")

        assert resultat.dossier.etat is EtatDossier.PAYEE
        assert resultat.dossier.slug_retenu is None

    def test_une_adresse_recue_pour_une_creation_n_est_pas_retenue(self):
        """⚠️ Ignorée, pas refusée : un client plus ancien de l'API l'envoie encore,
        et son encaissement ne doit pas échouer pour un champ devenu sans objet.
        Mais elle ne doit laisser aucune trace, ni sur le dossier ni dans
        l'événement, sans quoi la fiche afficherait un espace qui n'existe pas."""
        boite = BoiteDEnvoiMemoire()
        resultat = _encaisser(service="CREATION", slug="delices-bonapriso", boite=boite)

        assert resultat.dossier.etat is EtatDossier.PAYEE
        assert resultat.dossier.slug_retenu is None
        (evenement,) = boite.a_publier()
        assert evenement.charge["ouvre_un_locataire"] is False
        assert evenement.charge["slug"] == ""
        assert evenement.charge["tenant"] == ""


class TestUnePlateformeGardeSaRegle:
    """La contre-épreuve : ce qu'on a relâché pour une prestation ne l'est pas
    pour la seule offre qui ouvre réellement un locataire."""

    def test_une_plateforme_sans_adresse_est_refusee(self):
        with pytest.raises(EncaissementRefuse, match="aucune adresse"):
            _encaisser(service="PLATEFORME", slug="")

    def test_une_plateforme_retient_son_adresse(self):
        boite = BoiteDEnvoiMemoire()
        resultat = _encaisser(service="PLATEFORME", slug="station-bonaberi", boite=boite)

        assert resultat.dossier.slug_retenu == "station-bonaberi"
        (evenement,) = boite.a_publier()
        assert evenement.charge["ouvre_un_locataire"] is True
        assert evenement.charge["slug"] == "station-bonaberi"


def test_la_question_n_a_qu_une_reponse():
    assert ouvre_un_locataire("PLATEFORME") is True
    assert ouvre_un_locataire("CREATION") is False
    assert ouvre_un_locataire("ADHESION") is False
