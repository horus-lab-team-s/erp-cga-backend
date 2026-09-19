"""Le dossier commercial, adossé à la base.

Ce que le dépôt en mémoire ne peut pas dire :

* que **les colonnes promues suivent le document**. Elles sont l'objet même de
  la promotion, et une colonne qui diverge fait mentir toutes les requêtes qui
  s'appuient dessus sans que la lecture d'une entité ne le montre jamais ;
* que le cloisonnement tient sur ces requêtes-là, y compris celle du doublon,
  qui est la seule à interroger un numéro de téléphone à travers toute la table ;
* que le document se relit à l'identique, demandes rattachées comprises.

⚠️ Ces tests emploient `session_sql`, non cloisonnée, pour pouvoir écrire au nom
de deux cabinets et constater qu'aucun ne voit l'autre. Le filtre ambiant se
vérifie ailleurs, dans `test_isolation.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DossierIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
from app.contextes.souscription.adaptateurs.sortant.tables import (
    TableDossierCommercial,
)
from app.contextes.souscription.application.acquisition import deposer_une_demande
from app.contextes.souscription.domaine.demande_de_contact import (
    Canal,
    Consentement,
    DemandeDeContact,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    EtatDossier,
    ouvrir_un_dossier,
)
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LE_JOUR = datetime(2026, 9, 9, 10, 0)
CENTRE = "CGA-BRCG"
AUTRE = "CGA-AUTRE"


def _demande(identifiant: str, **surcharges) -> DemandeDeContact:
    defauts = {
        "identifiant": identifiant,
        "deposee_le": LE_JOUR,
        "nom": "Abena Ndzana",
        "telephone": "699112233",
        "service_souhaite": "creation-sarl",
        "canal_prefere": Canal.WHATSAPP,
        "consentement": Consentement(
            accorde=True,
            recueilli_le=LE_JOUR,
            version_du_texte="consentement-whatsapp-v1",
        ),
    }
    return DemandeDeContact(**{**defauts, **surcharges})


@pytest.fixture
def depot(session_sql) -> DepotDossiersSql:
    return DepotDossiersSql(session_sql, CENTRE)


class TestAllerRetour:
    def test_un_dossier_ecrit_se_relit_a_l_identique(self, depot):
        origine = ouvrir_un_dossier("dos-1", _demande("dc-1"))
        depot.enregistrer(origine)
        assert depot.lire("dos-1") == origine

    def test_les_demandes_rattachees_survivent_a_l_aller_retour(self, depot):
        """Le champ libre du second dépôt est souvent la précision que le
        visiteur revenait ajouter. Le perdre en base le perdrait pour de bon."""
        dossier = ouvrir_un_dossier("dos-1", _demande("dc-1")).rattacher(
            _demande(
                "dc-2",
                deposee_le=LE_JOUR + timedelta(hours=2),
                message="j'ai déjà un NIU",
            )
        )
        depot.enregistrer(dossier)

        relu = depot.lire("dos-1")
        assert [d.identifiant for d in relu.toutes_les_demandes] == ["dc-1", "dc-2"]
        assert relu.derniere_demande.message == "j'ai déjà un NIU"

    def test_le_consentement_revoque_se_relit_revoque(self, depot):
        """La preuve qu'il avait été donné, et la date à laquelle il a été
        retiré. Les deux sont ce qu'on demanderait au cabinet de produire."""
        demande = _demande("dc-1").sans_consentement(
            LE_JOUR + timedelta(days=3), "STOP reçu"
        )
        depot.enregistrer(ouvrir_un_dossier("dos-1", demande))

        relue = depot.lire("dos-1").demande
        assert relue.consentement.accorde is True
        assert relue.consentement.revoque_le == LE_JOUR + timedelta(days=3)
        assert relue.consentement.vaut_maintenant is False
        assert relue.canal_prefere is Canal.APPEL

    def test_un_dossier_inconnu_leve(self, depot):
        with pytest.raises(DossierIntrouvable):
            depot.lire("dos-jamais-vu")


class TestLesColonnesSuiventLeDocument:
    """Elles sont réécrites depuis le document à chaque enregistrement, jamais
    saisies à part : c'est ce qui les empêche de diverger de la vérité qu'elles
    résument."""

    def _ligne(self, session_sql, reference: str) -> TableDossierCommercial:
        return session_sql.scalars(
            select(TableDossierCommercial).where(
                TableDossierCommercial.reference == reference
            )
        ).one()

    def test_l_etat_et_la_date_suivent_les_transitions(self, depot, session_sql):
        dossier = ouvrir_un_dossier("dos-1", _demande("dc-1"))
        depot.enregistrer(dossier)
        ligne = self._ligne(session_sql, "dos-1")
        assert ligne.etat == "DEPOSEE"
        assert ligne.depuis_le == LE_JOUR

        plus_tard = LE_JOUR + timedelta(hours=3)
        depot.enregistrer(dossier.affecter("resp-1", plus_tard, motif="proximité"))
        session_sql.expire_all()
        ligne = self._ligne(session_sql, "dos-1")
        assert ligne.etat == "AFFECTEE"
        assert ligne.depuis_le == plus_tard
        assert ligne.responsable == "resp-1"

    def test_le_telephone_promu_est_la_forme_canonique(self, depot, session_sql):
        """Et non ce que le visiteur a tapé : la requête du doublon compare des
        chaînes, et six écritures du même numéro produiraient six clients."""
        depot.enregistrer(
            ouvrir_un_dossier("dos-1", _demande("dc-1", telephone="00237 699 11 22 33"))
        )
        assert self._ligne(session_sql, "dos-1").telephone == "+237699112233"

    def test_deposee_le_porte_la_derniere_arrivee(self, depot, session_sql):
        """C'est elle qui borne la fenêtre anti-doublon pour le dépôt suivant.
        Y laisser la première ferait sortir de la fenêtre un fil pourtant vivant
        au troisième envoi d'un visiteur insistant."""
        tardive = LE_JOUR + timedelta(hours=20)
        depot.enregistrer(
            ouvrir_un_dossier("dos-1", _demande("dc-1")).rattacher(
                _demande("dc-2", deposee_le=tardive)
            )
        )
        assert self._ligne(session_sql, "dos-1").deposee_le == tardive


class TestLesRequetes:
    def test_par_telephone_borne_bien_sa_fenetre(self, depot):
        depot.enregistrer(ouvrir_un_dossier("dos-vieux", _demande("dc-1")))
        depot.enregistrer(
            ouvrir_un_dossier(
                "dos-recent",
                _demande("dc-2", deposee_le=LE_JOUR + timedelta(days=2)),
            )
        )
        trouves = depot.par_telephone(
            "+237699112233", depuis=LE_JOUR + timedelta(days=1)
        )
        assert [d.reference for d in trouves] == ["dos-recent"]

    def test_par_telephone_compare_la_forme_canonique(self, depot):
        depot.enregistrer(ouvrir_un_dossier("dos-1", _demande("dc-1")))
        assert depot.par_telephone("+237699112233", depuis=LE_JOUR) != []
        assert depot.par_telephone("699112233", depuis=LE_JOUR) == []

    def test_ouverts_ecarte_les_dossiers_fermes(self, depot):
        depot.enregistrer(ouvrir_un_dossier("dos-vivant", _demande("dc-1")))
        depot.enregistrer(
            ouvrir_un_dossier(
                "dos-classe", _demande("dc-2", telephone="677889900")
            ).classer_sans_suite(LE_JOUR, motif="hors périmètre")
        )
        assert [d.reference for d in depot.ouverts()] == ["dos-vivant"]

    def test_ouverts_se_filtre_par_etat_et_s_ordonne_du_plus_ancien(self, depot):
        for rang, heures in ((1, 0), (2, 5), (3, 2)):
            depot.enregistrer(
                ouvrir_un_dossier(
                    f"dos-{rang}",
                    _demande(
                        f"dc-{rang}",
                        telephone=f"69911223{rang}",
                        deposee_le=LE_JOUR + timedelta(hours=heures),
                    ),
                )
            )
        assert [d.reference for d in depot.ouverts(etat=EtatDossier.DEPOSEE)] == [
            "dos-1",
            "dos-3",
            "dos-2",
        ]
        assert depot.ouverts(etat=EtatDossier.CHIFFREE) == []


class TestCloisonnement:
    """La requête du doublon interroge un numéro à travers toute la table. C'est
    la seule du contexte qui le fasse, et donc celle où une fuite se verrait le
    moins : deux cabinets qui démarchent la même PME ne doivent pas se découvrir
    l'un l'autre par un rattachement inattendu."""

    def test_un_cabinet_ne_voit_pas_les_dossiers_d_un_autre(self, session_sql):
        chez_nous = DepotDossiersSql(session_sql, CENTRE)
        chez_eux = DepotDossiersSql(session_sql, AUTRE)

        chez_nous.enregistrer(ouvrir_un_dossier("dos-nous", _demande("dc-1")))
        chez_eux.enregistrer(ouvrir_un_dossier("dos-eux", _demande("dc-2")))

        assert [d.reference for d in chez_nous.ouverts()] == ["dos-nous"]
        assert [d.reference for d in chez_eux.ouverts()] == ["dos-eux"]

    def test_le_doublon_ne_traverse_pas_la_cloison(self, session_sql):
        """Même numéro, même fenêtre, deux cabinets. Sans le filtre, le second
        dépôt se rattacherait au dossier du premier cabinet, qui verrait
        apparaître une demande qu'il n'a jamais reçue."""
        chez_eux = DepotDossiersSql(session_sql, AUTRE)
        chez_eux.enregistrer(ouvrir_un_dossier("dos-eux", _demande("dc-1")))

        chez_nous = DepotDossiersSql(session_sql, CENTRE)
        resultat = deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=1)),
            chez_nous,
            reference="dos-nous",
        )
        assert resultat.rattachee is False
        assert resultat.dossier.reference == "dos-nous"

    def test_une_lecture_ne_trouve_pas_le_dossier_du_voisin(self, session_sql):
        DepotDossiersSql(session_sql, AUTRE).enregistrer(
            ouvrir_un_dossier("dos-eux", _demande("dc-1"))
        )
        with pytest.raises(DossierIntrouvable):
            DepotDossiersSql(session_sql, CENTRE).lire("dos-eux")

    def test_ecrire_sur_la_ligne_d_un_autre_est_refuse(self, session_sql):
        """Le filtre de session protège les lectures ; les écritures se
        contrôlent à l'entrée du dépôt."""
        DepotDossiersSql(session_sql, AUTRE).enregistrer(
            ouvrir_un_dossier("dos-eux", _demande("dc-1"))
        )
        with pytest.raises(ValueError, match="appartient à un autre locataire"):
            DepotDossiersSql(session_sql, CENTRE).enregistrer(
                ouvrir_un_dossier("dos-eux", _demande("dc-2"))
            )


class TestLeCasDUsageSurLaBase:
    def test_le_parcours_complet_du_depot_au_rattachement(self, depot):
        """Le même scénario que les tests en mémoire, mais sur la vraie table :
        c'est ici qu'un défaut de colonne promue se verrait."""
        premier = deposer_une_demande(_demande("dc-1"), depot, reference="dos-1")
        assert premier.rattachee is False

        second = deposer_une_demande(
            _demande("dc-2", deposee_le=LE_JOUR + timedelta(hours=2)),
            depot,
            reference="dos-2",
        )
        assert second.rattachee is True
        assert second.dossier.reference == "dos-1"

        with pytest.raises(DossierIntrouvable):
            depot.lire("dos-2")
        assert len(depot.lire("dos-1").toutes_les_demandes) == 2
