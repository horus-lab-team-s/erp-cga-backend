"""La veille et le classement, adossés à la base.

Ce que les dépôts en mémoire ne peuvent pas dire :

* que **la charge d'un responsable redescend** quand un dossier est classé. C'est
  l'agrégat SQL `charge_par_responsable` qui la calcule, pas une liste comptée en
  Python, et c'est lui qu'il faut éprouver ;
* que le **marqueur de signalement se relit** après un aller-retour en base. Il
  vit dans le document JSON et non dans une colonne promue : une sérialisation
  qui l'oublierait ferait resignaler chaque dossier à chaque passage, sans
  qu'aucun test en mémoire ne le voie ;
* que la **veille et le classement partagent la transaction de l'appelant**.

⚠️ Ces tests emploient `session_sql`, non cloisonnée. Le filtre ambiant se vérifie
ailleurs, dans `test_isolation.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
from app.contextes.souscription.application.veille_des_dossiers import (
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
from tests.conftest import exige_postgresql

pytestmark = exige_postgresql

LE_JOUR = datetime(2026, 1, 5, 9, 0)
CENTRE = "CGA-BRCG"
DELAIS = {EtatDossier.AFFECTEE: timedelta(hours=48)}


def _demande(rang: int) -> DemandeDeContact:
    return DemandeDeContact(
        identifiant=f"D{rang}",
        deposee_le=LE_JOUR,
        nom="Prospect muet",
        telephone=f"6991122{rang:02d}",
        service_souhaite="creation-sarl",
        canal_prefere=Canal.APPEL,
        consentement=Consentement(
            accorde=False,
            recueilli_le=LE_JOUR,
            version_du_texte="consentement-whatsapp-v1",
        ),
    )


@pytest.fixture
def depot(session_sql) -> DepotDossiersSql:
    return DepotDossiersSql(session_sql, locataire=CENTRE)


@pytest.fixture
def trois_muets(depot, session_sql):
    """Trois prospects affectés à « mballa », qui n'ont jamais répondu."""
    for rang in range(3):
        dossier = ouvrir_un_dossier(f"DOS-MUET-{rang}", _demande(rang))
        depot.enregistrer(dossier.affecter("mballa", LE_JOUR, motif="tour de rôle"))
    session_sql.flush()
    return depot


class TestLaChargeQuiNeRedescendaitJamais:
    """⚠️ **Le défaut mesuré avant d'écrire une ligne de ce pas.**

    `charge_par_responsable` porte ce commentaire : « Les états terminaux sont
    exclus. Un dossier payé ou sans suite ne pèse plus sur personne, et les
    compter ferait qu'un ancien collaborateur productif paraîtrait surchargé
    pour toujours. »

    La garde était juste, et rien ne la déclenchait jamais : aucune route, aucun
    travail ne mettait un dossier à `SANS_SUITE`. Une garde que rien ne déclenche
    protège moins qu'une garde absente, parce que l'absente, on la voit.
    """

    def test_huit_mois_plus_tard_ils_pesent_encore(self, trois_muets):
        """L'état des lieux, tel qu'il a été mesuré. Ce cas est le rappel de ce
        qu'on répare : il ne doit pas se mettre à passer autrement."""
        huit_mois = LE_JOUR + timedelta(days=240)
        assert trois_muets.charge_par_responsable()["mballa"] == 3
        souffrants = [
            d
            for d in trois_muets.ouverts(etat=EtatDossier.AFFECTEE)
            if d.en_souffrance(huit_mois, DELAIS)
        ]
        assert len(souffrants) == 3

    def test_classer_les_fait_sortir_de_la_charge(self, trois_muets, session_sql):
        """Et c'est tout l'objet du pas : le geste rend sa capacité au
        collaborateur."""
        for dossier in trois_muets.ouverts(etat=EtatDossier.AFFECTEE):
            trois_muets.enregistrer(
                dossier.classer_sans_suite(
                    LE_JOUR + timedelta(days=240), motif="jamais-rappele"
                )
            )
        session_sql.flush()

        assert trois_muets.charge_par_responsable() == {}
        assert trois_muets.ouverts() == []

    def test_un_seul_classe_n_allege_que_d_un(self, trois_muets, session_sql):
        """⚠️ La contre-épreuve. Sans elle, un dépôt qui viderait tout à chaque
        classement passerait le cas précédent."""
        premier = trois_muets.ouverts(etat=EtatDossier.AFFECTEE)[0]
        trois_muets.enregistrer(premier.classer_sans_suite(LE_JOUR, motif="prix"))
        session_sql.flush()

        assert trois_muets.charge_par_responsable()["mballa"] == 2


class TestLeMarqueurEnBase:
    def test_il_survit_a_l_aller_retour(self, trois_muets, session_sql):
        """⚠️ Il vit dans le document JSON, pas dans une colonne promue.

        Une sérialisation qui l'oublierait ferait resignaler chaque dossier à
        chaque passage : quatre-vingt-seize alertes par jour et par dossier
        oublié, et aucun test en mémoire ne le verrait.
        """
        tard = LE_JOUR + timedelta(hours=49)
        premier = trois_muets.ouverts(etat=EtatDossier.AFFECTEE)[0]
        trois_muets.enregistrer(premier.signaler(tard))
        session_sql.flush()
        session_sql.expunge_all()

        relu = trois_muets.lire(premier.reference)
        assert relu.signale_le == tard
        assert relu.etat is EtatDossier.AFFECTEE
        assert relu.depuis_le == LE_JOUR

    def test_le_second_passage_ne_redepose_rien(self, trois_muets, session_sql):
        """Le balayage complet, deux fois, sur la base réelle."""
        boite = BoiteDEnvoiMemoire()
        tard = LE_JOUR + timedelta(hours=49)

        def passage(quand):
            rapport = veiller_les_dossiers(
                DELAIS,
                dossiers=trois_muets,
                boite=boite,
                a_l_instant=quand,
                identifiant=lambda suffixe: f"veille-{suffixe}",
            )
            session_sql.flush()
            return rapport

        premier = passage(tard)
        second = passage(tard + timedelta(hours=1))

        assert len(premier.signales) == 3
        assert second.signales == ()
        assert second.deja_signales == 3
        assert len(boite.tous()) == 3

    def test_une_transition_efface_le_marqueur_en_base(self, trois_muets, session_sql):
        """Un dossier qui bouge recommence une attente, y compris après relecture."""
        tard = LE_JOUR + timedelta(hours=49)
        premier = trois_muets.ouverts(etat=EtatDossier.AFFECTEE)[0]
        trois_muets.enregistrer(premier.signaler(tard))
        session_sql.flush()

        trois_muets.enregistrer(trois_muets.lire(premier.reference).premier_contact(tard))
        session_sql.flush()
        session_sql.expunge_all()

        relu = trois_muets.lire(premier.reference)
        assert relu.etat is EtatDossier.EN_CONVERSATION
        assert relu.signale_le is None
