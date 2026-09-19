"""La recette : le parcours entier, de la demande déposée au tenant ouvert.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE, ET CE QU'IL A DÉJÀ COÛTÉ DE NE PAS L'AVOIR

Quatre défauts majeurs de ce projet n'étaient visibles **que** par la chaîne entière,
jouée sur une vraie base :

* des politiques de cloisonnement parfaitement écrites qui ne s'appliquaient à
  personne ;
* un gabarit de courriel réclamé sous un nom qui n'existait nulle part ;
* un tenant payé dont la saga rendait « terminée » et dont la table restait vide ;
* une qualification validée, chiffrée, rendue à l'appelant, et jetée.

⚠️ **Aucun test de domaine ne pouvait les atteindre.** Chaque pièce était correcte
prise seule : le défaut vivait dans le câblage. Ils ont tous été trouvés en tapant un
script à la main, ce qui veut dire que le suivant ne le sera pas.

Ce fichier remplace ces scripts. Il ne vérifie aucune règle métier — c'est le travail
des cent autres fichiers — il vérifie que **les pièces sont branchées entre elles**.

IL PASSE PAR HTTP, ET IL LE PEUT DÉSORMAIS ENTIÈREMENT

Les étapes exposées passent par l'API, parce que c'est ce qu'un client fait. Deux ne
le sont pas et le fichier le dit plutôt que de le masquer : **l'émission d'une
proforma** et **l'encaissement de son acceptation** n'ont aucune route. Le domaine et
les cas d'usage existent, éprouvés ; la surface HTTP manque, et c'est un reste nommé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

#: Le compte qui porte DIRECTION dans le jeu de démonstration : il peut affecter,
#: qualifier et lire les prospects. Un compte réel, et non une habilitation forgée :
#: la recette doit prouver que quelqu'un, dans le cabinet, a réellement ces droits.
RESPONSABLE = "b.mballa@cga-brcg.cm"

#: ⚠️ **Un second compte, et c'est une séparation des rôles, pas une commodité.**
#:
#: Confirmer un encaissement **ouvre un tenant** : cela crée un espace, des tables,
#: un sous-domaine. C'est un acte d'administration, et la plateforme le réserve à
#: `GERER_COMPTES` — comme elle le fait déjà pour l'activation d'un accès dans
#: l'autre flux de souscription.
#:
#: Le commercial conclut la vente ; l'administrateur ouvre l'accès. La recette fait
#: donc intervenir deux personnes, comme le cabinet le ferait.
ADMINISTRATEUR = "s.onana@cga-brcg.cm"


@pytest.fixture
def plateforme(plateforme_amorcee):
    """La plateforme partagée, avec le responsable du centre déjà connecté.

    ⚠️ Ce fichier suit **un seul parcours**, du premier contact au dossier payé.
    Ouvrir la session à chaque cas y serait du bruit, alors que la recette de
    production, qui change plusieurs fois d'acteur, l'ouvre explicitement.

    La fixture partagée ne le fait donc pas à la place de tout le monde : elle
    rend une plateforme neuve, et chaque fichier dit qui s'y connecte.
    """
    ouvrir_une_session(plateforme_amorcee, RESPONSABLE)
    return plateforme_amorcee


def _tour():
    """Un tour d'ordonnanceur hors requête, comme la boucle de fond le fait."""
    from app.contextes.transverse.adaptateurs.entrant.routes_orchestration import (
        tour_hors_requete,
    )

    return tour_hors_requete()


def _rendre_tout_du(moteur):
    """Fait comme si la cadence de chaque travail était échue.

    ⚠️ Sans cela, la recette devrait **attendre** cinq secondes entre deux tours, et
    une heure pour le balayage. Un test qui attend finit ignoré ; celui-ci recule les
    horloges des passages, ce qui exerce le même code.
    """
    with moteur.begin() as connexion:
        connexion.execute(
            text("UPDATE passage_ordonnance SET termine_le = termine_le - interval '2 hours'")
        )


class TestLeParcoursEntier:
    """De la demande déposée sur la vitrine au sous-domaine qui répond."""

    def test_un_visiteur_devient_un_tenant(self, plateforme, moteur_test):
        client = plateforme

        # 1 · Le visiteur remplit le formulaire public. Six champs, sans session.
        depot = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Station Bonabéri",
                "telephone": "699112233",
                "courriel": "gerant@station-bonaberi.cm",
                "service_souhaite": "creation-sarl",
                "canal_prefere": "APPEL",
                "message": "je veux créer une SARL",
            },
        )
        assert depot.status_code in (200, 201), depot.text
        assert depot.json()["recue"] is True

        # 2 · Le responsable la trouve dans sa file. ⚠️ La route publique ne rend pas
        # la référence : la livrer au visiteur lui donnerait de quoi deviner celles
        # des autres.
        file = client.get("/acquisition/dossiers")
        assert file.status_code == 200, file.text
        reference = file.json()[0]["reference"]

        # 3 · Il l'affecte sans rien fournir : le serveur monte les candidatures
        # depuis l'annuaire et la charge réelle.
        affectation = client.post(
            f"/acquisition/dossiers/{reference}/affectation", json={}
        )
        assert affectation.status_code == 200, affectation.text
        assert affectation.json()["affecte"] is True
        assert affectation.json()["responsable"]

        # 4 · Il qualifie en deux passages, comme on apprend au fil des échanges.
        premier = self._qualifier(
            client, reference, [("forme_juridique", "SARL"), ("associes", 2)]
        )
        assert premier["avancement"]["repondues"] == 2
        assert premier["complete"] is False

        # ⚠️ **Le second passage complète, et c'est ce qui fait avancer le dossier.**
        # Une qualification partielle ne rend pas le dossier `QUALIFIÉE` : avancer
        # sur la première réponse ferait franchir l'étape à un dossier dont il
        # manque neuf questions, et le chiffrage porterait sur des faits absents.
        second = self._qualifier(
            client,
            reference,
            [
                ("capital_social", "1000000"),
                ("apports_en_nature", False),
                ("chiffre_affaires_prevu", "DE_10M_A_50M"),
                ("salaries_prevus", 3),
                # ⚠️ La région **est** collectée, mais ici et non au formulaire
                # public. L'affectation, qui la précède dans le chemin nominal, ne
                # peut donc pas s'en servir : c'est ce que le pas 21 a établi.
                ("region_siege", "LITTORAL"),
                ("activite", "station-service et boutique"),
            ],
        )
        assert second["avancement"]["repondues"] == 8, (
            "la qualification est repartie de zéro entre deux appels"
        )
        assert second["complete"] is True, second["manquantes"]

        # 5 · Le chiffrage rend un intervalle, jamais un prix unique, **calculé sur la
        # qualification enregistrée**. ⚠️ Pas 65 : ce cas chiffrait en envoyant deux
        # des huit réponses, et le capital comme le chiffre d'affaires restaient hors
        # du prix. Un corps qui envoie des faits est désormais refusé.
        faits_declares = client.post(
            f"/acquisition/dossiers/{reference}/chiffrage",
            json={"faits": {"forme_juridique": "SARL", "associes": 2}, "debours": []},
        )
        assert faits_declares.status_code == 422, faits_declares.text
        chiffrage = client.post(
            f"/acquisition/dossiers/{reference}/chiffrage", json={"debours": []}
        )
        assert chiffrage.status_code == 200, chiffrage.text
        assert {"plancher", "reference", "plafond"} <= set(chiffrage.json())

        # 6 · La proforma est émise **par l'API**, arrêtée par le compte de la
        # session, et son lien d'acceptation n'est rendu qu'ici.
        tarif = {"montant": chiffrage.json()["reference"]}
        # ⚠️ Pas 65 : l'intervalle ne se déclare plus. Un plancher abaissé faisait
        # passer un rabais sans motif ; il est recalculé à l'émission.
        plancher_declare = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={**tarif, "plancher": "1"},
        )
        assert plancher_declare.status_code == 422, plancher_declare.text
        # ⚠️ Pas 63 : qui engage le cabinet ne s'écrit plus dans le corps. Écrire
        # « direction » y simulait la séparation des tâches que le contrôle interne lit.
        usurpation = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={**tarif, "valide_par": "Bernadette MBALLA"},
        )
        assert usurpation.status_code == 422, usurpation.text
        emission = client.post(f"/acquisition/dossiers/{reference}/proforma", json=tarif)
        assert emission.status_code == 201, emission.text
        proforma = emission.json()
        # ⚠️ La vérité sur le contrôle : le même compte a chiffré et engagé le cabinet,
        # et la réponse le dit, au lieu d'afficher une validation déclarée.
        assert proforma["chiffre_par"] == proforma["valide_par"] == "C-001"
        assert proforma["separation_respectee"] is False
        # L'intervalle de la proforma est celui du chiffrage, recalculé sur les mêmes faits.
        for borne in ("plancher", "reference", "plafond"):
            assert proforma[borne] == chiffrage.json()[borne], (borne, proforma, chiffrage.json())
        assert proforma["numero"].startswith("PRO-")
        assert proforma["lien_acceptation"], "aucun lien d'acceptation rendu"

        # 7 · Transmise au client : c'est **cette date qui arme la relance**.
        transmission = client.post(
            f"/acquisition/proformas/{proforma['numero']}/transmission"
        )
        assert transmission.status_code == 200, transmission.text
        assert transmission.json()["etat"] == "TRANSMISE"

        # 8 · ⚠️ **Le client accepte sans compte**, par son lien signé. Une route
        # publique : lui imposer un compte pour accepter un devis ferait perdre la
        # moitié des acceptations, et le sceau **est** l'authentification.
        acceptation = client.post(
            f"/acquisition/proformas/{proforma['numero']}/acceptation",
            json={
                "sceau": proforma["lien_acceptation"],
                "expire_le": proforma["expire_le"],
                "version": proforma["version"],
                "identite_declaree": "Awa NDONGO, gérante",
            },
        )
        assert acceptation.status_code == 200, acceptation.text
        assert acceptation.json()["identite_declaree"] == "Awa NDONGO, gérante"

        # 9 · L'encaissement est **confirmé par un humain habilité**, après
        # rapprochement. Le fournisseur de paiement ne signe pas ses notifications,
        # et ce geste ouvre un tenant : le déclencher sur un appel non signé
        # laisserait un inconnu provisionner de l'infrastructure.
        # ⚠️ **Un autre collaborateur, et c'est le sujet.** Le commercial a conclu ;
        # l'administrateur ouvre l'accès. Les deux gestes sont distincts, et la
        # plateforme le tient par les permissions.
        administrateur = TestClient(client.app_pour_admin)
        session_admin = administrateur.post(
            "/transverse/session",
            json={"courriel": ADMINISTRATEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert session_admin.status_code == 200, session_admin.text

        # ⚠️ Le commercial, lui, n'y a pas droit, et le vérifier ici vaut mieux
        # qu'un test séparé : c'est dans ce contexte que la séparation compte.
        refus = client.post(
            f"/acquisition/proformas/{proforma['numero']}/encaissement",
            json={"slug": "station-bonaberi"},
        )
        assert refus.status_code == 403, refus.text

        encaissement = administrateur.post(
            f"/acquisition/proformas/{proforma['numero']}/encaissement",
            json={"slug": "station-bonaberi", "reference_externe": "MTN-42"},
        )
        assert encaissement.status_code == 200, encaissement.text
        assert encaissement.json()["etat"] == "PAYEE"
        assert encaissement.json()["rejeu"] is False

        # ⚠️ **Rejouable sans dommage.** Deux collaborateurs qui confirment le même
        # encaissement n'ouvrent pas deux tenants.
        rejeu = administrateur.post(
            f"/acquisition/proformas/{proforma['numero']}/encaissement",
            json={"slug": "station-bonaberi", "reference_externe": "MTN-42"},
        )
        assert rejeu.status_code == 200, rejeu.text
        assert rejeu.json()["rejeu"] is True

        # 10 · Un tour d'ordonnanceur publie l'événement, la saga ouvre le tenant.
        rapport = _tour()
        assert rapport.execute
        assert all(r.reussi for r in rapport.resultats), [
            r.compte_rendu for r in rapport.resultats if not r.reussi
        ]

        # 11 · Le tenant existe **en base**, et pas seulement dans une mémoire.
        with moteur_test.connect() as connexion:
            ligne = connexion.execute(
                text("SELECT slug, statut, etape_atteinte FROM tenant")
            ).one()
        assert (ligne.slug, ligne.statut, ligne.etape_atteinte) == (
            "station-bonaberi",
            "ACTIF",
            "PRET",
        )

        # 12 · Et son sous-domaine répond **tout de suite**, sans redéploiement.
        from app.contextes.transverse.adaptateurs.entrant.dependances import (
            repertoire_des_tenants,
        )

        assert repertoire_des_tenants().par_slug("station-bonaberi") is not None

        # 13 · Et un seul tenant, malgré la double confirmation.
        with moteur_test.connect() as connexion:
            assert (
                connexion.execute(text("SELECT count(*) FROM tenant")).scalar_one() == 1
            )

    def _qualifier(self, client, reference, reponses):
        reponse = client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": [{"code": c, "valeur": v} for c, v in reponses]},
        )
        assert reponse.status_code == 200, reponse.text
        return reponse.json()



class TestLaRelanceDUneProformaSansReponse:
    """Le second volet : ce qui se passe quand le client ne répond pas.

    ⚠️ **Ce volet a révélé trois défauts à lui seul** : un rappel moins urgent envoyé
    après un rappel plus urgent, un gabarit de courriel qui n'existait pas, et un
    canal figé au dépôt. Aucun n'était visible autrement que par la chaîne complète.
    """

    def test_une_proforma_oubliee_finit_sur_le_bureau_d_un_humain(
        self, plateforme, moteur_test
    ):
        client = plateforme

        # 1 · Une proforma transmise il y a quinze jours, sans réponse.
        self._proforma_ancienne()

        # 2 · Le balayage la trouve et dépose la relance ; le relais la publie ;
        # l'abonné la remet. Trois tours, parce que le relais passe **avant** le
        # balayage : c'est le chemin vital, celui qui ouvre les tenants payés.
        for _ in range(3):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]

        # 3 · L'événement est publié, et le suivi retient le palier employé.
        with moteur_test.connect() as connexion:
            # ⚠️ Filtré sur le nom, et non « la seule ligne de la boîte ». La
            # veille des dossiers dépose ses propres alertes dans la même boîte,
            # et la proforma de ce scénario dort depuis quinze jours : compter
            # les lignes ferait échouer ce cas pour une raison qui n'a rien à
            # voir avec la relance qu'il vérifie.
            envoi = connexion.execute(
                text("SELECT nom, publie_le FROM boite_d_envoi WHERE nom = 'RelanceDue'")
            ).one()
            suivi = connexion.execute(
                text("SELECT proforma, donnees FROM suivi_de_relance")
            ).one()
        assert envoi.nom == "RelanceDue"
        assert envoi.publie_le is not None, "la relance n'a jamais été remise"
        assert suivi.donnees["rangs_envoyes"], "aucun palier inscrit au suivi"

        # 4 · ⚠️ Le plan du centre met l'**appel au rang 0** : il a choisi
        # d'appeler avant d'écrire. Un rappel atterrit donc sur le bureau du
        # responsable, avec son motif en clair.
        carnet = client.get("/acquisition/rappels")
        assert carnet.status_code == 200, carnet.text
        assert len(carnet.json()) == 1, carnet.json()
        rappel = carnet.json()[0]
        assert "PRO-2026-0002" in rappel["motif"]

        # 5 · Le responsable rappelle et clôt. **Son nom est celui de sa session** :
        # un corps qui prétend le dire est refusé (pas 63), sans quoi n'importe qui
        # écrivait qu'un collègue avait appelé.
        usurpation = client.post(
            f"/acquisition/rappels/{rappel['identifiant']}/fait",
            json={"par": "Serge ONANA"},
        )
        assert usurpation.status_code == 422, usurpation.text
        cloture = client.post(f"/acquisition/rappels/{rappel['identifiant']}/fait", json={})
        assert cloture.status_code == 200, cloture.text
        assert cloture.json()["fait_par"] == "C-001", "le compte de la session, b.mballa"

        # 6 · Et un rappel clos ne se referme pas : deux appels au même client sur
        # le même sujet ne se rattrapent pas.
        assert (
            client.post(
                f"/acquisition/rappels/{rappel['identifiant']}/fait", json={}
            ).status_code
            == 409
        )

        # 7 · Le carnet est vide : la liste se vide quand le travail est fait.
        assert client.get("/acquisition/rappels").json() == []

    def test_un_prospect_qui_prefere_ecrire_recoit_un_courriel(
        self, plateforme, moteur_test
    ):
        """⚠️ **La branche courriel, que le premier volet n'emprunte jamais.**

        ─────────────────────────────────────────────────────────────────────────
        Le plan de contact du centre place l'**appel au rang 0** : le scénario
        précédent descend donc jusqu'au carnet des rappels sans jamais poster de
        courriel, et **le gabarit de relance pourrait ne pas exister** sans que la
        recette s'en aperçoive.

        C'est exactement le défaut du pas 18 : le code réclamait un gabarit sous un
        nom absent du catalogue, l'envoi rendait « faux » sans rien poster, et aucune
        relance ne serait jamais partie par courriel.

        La préférence du client est essayée **avant** l'ordre du plan : un prospect
        qui coche « écrivez-moi » emprunte donc la branche que ce cas vérifie.
        ─────────────────────────────────────────────────────────────────────────
        """
        from app.contextes.souscription.domaine.canaux import Canal
        from app.contextes.transverse.api import service_de_notification

        self._proforma_ancienne(
            reference="dos-recette-3",
            numero="PRO-2026-0003",
            courriel="gerant@boulangerie-wouri.cm",
            prefere=Canal.COURRIEL,
        )

        for _ in range(3):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]

        postes = service_de_notification().derniers()
        assert postes, "aucun courriel posté : la branche courriel n'a pas été prise"
        dernier = postes[-1]
        assert dernier.code == "relance.proforma", (
            f"code de gabarit inattendu : {dernier.code}. Un code absent du "
            "catalogue ferait rendre « faux » à l'envoi, sans rien poster."
        )
        assert dernier.destinataire == "gerant@boulangerie-wouri.cm"
        assert dernier.contexte["proforma"] == "PRO-2026-0003"

        # ⚠️ Et **aucun rappel** : le courriel a suffi, l'appel n'a pas eu lieu.
        assert plateforme.get("/acquisition/rappels").json() == []

    def _proforma_ancienne(
        self,
        *,
        reference: str = "dos-recette-2",
        numero: str = "PRO-2026-0002",
        courriel: str | None = None,
        prefere=None,
    ):
        """Une proforma transmise il y a quinze jours, et son dossier.

        Quinze jours : au delà du dernier palier du plan réel (3, 7, 14 jours), donc
        une relance est due dès le premier balayage.
        """
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
            DepotDossiersSql,
            DepotProformasSql,
        )
        from app.contextes.souscription.api import (
            Canal,
            Consentement,
            DemandeDeContact,
            TarifArrete,
            deposer_une_demande,
            emettre,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        il_y_a_15j = maintenant() - timedelta(days=15)
        demande = DemandeDeContact(
            identifiant=f"dc-{reference}",
            deposee_le=il_y_a_15j,
            nom="Boulangerie du Wouri",
            telephone="+237690112233",
            courriel=courriel,
            service_souhaite="creation-sarl",
            message=None,
            canal_prefere=prefere or Canal.APPEL,
            consentement=Consentement(
                accorde=False, recueilli_le=il_y_a_15j, version_du_texte="v1"
            ),
        )
        tarif = TarifArrete(
            montant=Decimal("250000"),
            plancher=Decimal("200000"),
            reference=Decimal("250000"),
            plafond=Decimal("375000"),
            version_bareme="2026.1",
            chiffre_par="b.mballa",
            valide_par="direction",
            arrete_le=il_y_a_15j,
        )
        proforma = emettre(
            numero=numero,
            dossier=reference,
            service="creation-sarl",
            tarif=tarif,
            contenu=b"%PDF recette",
            modele="creation-sarl",
            version_modele="2026.1",
            a_l_instant=il_y_a_15j,
        ).transmise(il_y_a_15j)

        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            deposer_une_demande(
                demande,
                DepotDossiersSql(boutique.session, "CGA-BRCG"),
                reference=reference,
            )
            DepotProformasSql(boutique.session, "CGA-BRCG").enregistrer(proforma)


class TestLeDossierQuiDort:
    """Le troisième volet : ce qui se passe quand **personne** ne répond, ni le
    prospect ni le cabinet.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE VOLET EXISTE PARCE QUE LE DÉFAUT A ÉTÉ MESURÉ, PAS SUPPOSÉ**

    Trois capacités du domaine étaient écrites, testées, et sans aucun appelant :
    `classer_sans_suite`, `en_souffrance` et `reaffecter_le_dossier`. Le parcours
    savait avancer, il ne savait pas se dégager.

    Sur une base réelle, trois prospects muets pesaient encore trois dossiers huit
    mois plus tard, alors que `charge_par_responsable` promet en commentaire qu'« un
    ancien collaborateur productif ne paraîtra pas surchargé pour toujours ». La
    garde était juste et rien ne la déclenchait : l'affectation, qui choisit le
    moins chargé, punissait indéfiniment celui qui avait reçu les prospects muets.

    Ce cas parcourt la boucle entière : le dossier dort, la veille le dit une fois,
    la console le montre, l'humain classe avec un motif, la charge redescend.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_un_dossier_endormi_est_signale_puis_classe_et_la_charge_redescend(
        self, plateforme, moteur_test
    ):
        client = plateforme

        # 1 · Un dossier affecté il y a quatre jours, que personne n'a travaillé,
        # et **qui a déjà épuisé ses trois reprises**.
        #
        # ⚠️ **C'est cette condition qui donne son sens à l'alerte**, et la recette
        # l'a apprise en tombant : sans elle, le travail de reprise passait la main
        # à 24 heures, remettait l'ancienneté à zéro, et le dossier n'atteignait
        # jamais les 48 heures de la veille. L'événement déposé était
        # `DossierRepris`, pas `DossierEnSouffrance`.
        #
        # Une alerte de veille sur `AFFECTÉE` ne dit donc pas « personne n'a
        # rappelé », ce qui serait du bruit. Elle dit « la machine a essayé de
        # passer la main et n'a pas pu », sur quoi un responsable de pôle peut agir.
        self._dossier_endormi(reprises_epuisees=True)

        avant = self._charge(moteur_test)
        assert avant.get("b.mballa") == 1, avant

        # 2 · La console le montre, avec son retard, avant même qu'un tour ait eu
        # lieu : cette route calcule l'état réel et ne dépend pas de la veille.
        souffrance = client.get("/acquisition/dossiers/en-souffrance")
        assert souffrance.status_code == 200, souffrance.text
        (ligne,) = [x for x in souffrance.json() if x["reference"] == "dos-dormant"]
        assert ligne["etat"] == "AFFECTEE"
        assert ligne["delai_heures"] == 48
        assert ligne["immobile_depuis_heures"] >= 96
        assert ligne["signale_le"] is None

        # 3 · Le tour de fond dépose l'alerte. Deux tours : le relais passe avant.
        for _ in range(2):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]

        with moteur_test.connect() as connexion:
            alertes = list(
                connexion.execute(
                    text(
                        "SELECT nom, cle, donnees FROM boite_d_envoi "
                        "WHERE nom IN ('DossierEnSouffrance', 'DossierRepris') "
                        "ORDER BY nom"
                    )
                )
            )
        # ⚠️ Les deux noms sont interrogés, et non le seul attendu : c'est ce qui
        # rend l'échec lisible le jour où la reprise repasse devant la veille.
        assert [a.nom for a in alertes] == ["DossierEnSouffrance"], [
            (a.nom, a.cle) for a in alertes
        ]
        charge = alertes[0].donnees["charge"]
        assert charge["dossier"] == "dos-dormant"
        assert charge["responsable"] == "b.mballa"
        # ⚠️ Ni le nom du prospect ni son numéro : l'alerte traverse une file, des
        # journaux et des sauvegardes, qui la recopient tous.
        assert "Boulangerie" not in str(charge)
        assert "690112233" not in str(charge)

        # 4 · **Un second tour ne redépose rien.** C'est la propriété qui compte :
        # un dossier oublié six mois, balayé tous les quarts d'heure, produirait
        # dix-sept mille alertes pour un seul fait.
        for _ in range(2):
            _rendre_tout_du(moteur_test)
            _tour()
        with moteur_test.connect() as connexion:
            encore = connexion.execute(
                text(
                    "SELECT count(*) FROM boite_d_envoi "
                    "WHERE nom = 'DossierEnSouffrance'"
                )
            ).scalar_one()
        assert encore == 1, "la veille a redéposé une alerte déjà signalée"

        # 5 · La console dit maintenant **depuis quand quelqu'un est censé savoir**.
        relu = client.get("/acquisition/dossiers/en-souffrance").json()
        (ligne,) = [x for x in relu if x["reference"] == "dos-dormant"]
        assert ligne["signale_le"] is not None

        # 6 · Le responsable a rappelé, en vain : il classe, avec un motif du
        # vocabulaire du centre. C'est un humain qui décide, jamais la machine.
        vocabulaire = client.get("/acquisition/motifs-de-classement").json()
        assert "injoignable" in [m["code"] for m in vocabulaire]
        classement = client.post(
            "/acquisition/dossiers/dos-dormant/sans-suite",
            json={"motif": "injoignable"},
        )
        assert classement.status_code == 200, classement.text
        assert classement.json()["etat"] == "SANS_SUITE"

        # 7 · ⚠️ **Ce que tout ce volet existe pour prouver** : la charge du
        # collaborateur redescend, et il redevient candidat à une affectation.
        assert self._charge(moteur_test) == {}

        # 8 · Et l'alerte s'éteint : la liste ne le montre plus.
        assert client.get("/acquisition/dossiers/en-souffrance").json() == []

    @staticmethod
    def _charge(moteur) -> dict[str, int]:
        """La charge telle que l'affectation la lit : l'agrégat SQL lui-même."""
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
            DepotDossiersSql,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.locataire import etabli

        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            return DepotDossiersSql(
                boutique.session, "CGA-BRCG"
            ).charge_par_responsable()

    @staticmethod
    def _dossier_endormi(
        reference: str = "dos-dormant",
        *,
        reprises_epuisees=False,
        telephone: str = "+237690112233",
    ):
        """Un dossier affecté il y a quatre jours, et jamais retouché.

        Quatre jours : au delà des 48 heures que le référentiel accorde à
        `AFFECTEE`, donc en souffrance dès le premier regard.

        `reprises_epuisees` place le dossier à la limite du domaine, ce qui met la
        reprise automatique hors jeu. Voir le commentaire de l'étape 1 : sans cela,
        la reprise agit à 24 heures et la veille ne voit jamais rien.
        """
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
            DepotDossiersSql,
        )
        from app.contextes.souscription.api import (
            Canal,
            Consentement,
            DemandeDeContact,
            deposer_une_demande,
        )
        from app.contextes.souscription.domaine.dossier_commercial import (
            REAFFECTATIONS_MAXIMALES,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        il_y_a_4j = maintenant() - timedelta(days=4)
        demande = DemandeDeContact(
            identifiant=f"dc-{reference}",
            deposee_le=il_y_a_4j,
            nom="Boulangerie du Wouri",
            # ⚠️ Paramétré, et il le faut : deux demandes du **même numéro** sont
            # un doublon, et la seconde se rattache au premier dossier au lieu
            # d'en ouvrir un. C'est la règle métier qui fait son travail, et un
            # scénario à deux dossiers doit donc employer deux numéros.
            telephone=telephone,
            courriel=None,
            service_souhaite="creation-sarl",
            message=None,
            canal_prefere=Canal.APPEL,
            consentement=Consentement(
                accorde=False, recueilli_le=il_y_a_4j, version_du_texte="v1"
            ),
        )
        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            depot = DepotDossiersSql(boutique.session, "CGA-BRCG")
            deposer_une_demande(demande, depot, reference=reference)
            # ⚠️ Affecté **à la date de la demande**, et non maintenant : c'est
            # `depuis_le` que la veille compare, et l'affecter à l'instant présent
            # remettrait le compteur à zéro. Le dossier ne dormirait plus, et le
            # cas passerait pour une raison qui n'a rien à voir avec ce qu'il
            # vérifie.
            affecte = depot.lire(reference).affecter(
                "b.mballa", il_y_a_4j, motif="recette : tour de rôle"
            )
            if reprises_epuisees:
                # Écrit directement plutôt que joué trois fois : la rotation des
                # reprises a ses propres cas, et la rejouer ici mêlerait deux
                # mécanismes dans un scénario qui n'en vérifie qu'un.
                affecte = affecte.model_copy(
                    update={"reaffectations": REAFFECTATIONS_MAXIMALES}
                )
            depot.enregistrer(affecte)


class TestLaMainQuiPasseToutSeule:
    """Le cinquième volet : le seul geste que la plateforme s'autorise sans
    qu'un humain le demande.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE VOLET GARDE UN GESTE AUTOMATIQUE, DONC IL GARDE PLUS QUE LES AUTRES**

    La reprise réaffecte un dossier commercial sans que personne l'ait demandé.
    Elle tient parce que le silence prouve ici exactement ce qu'on en conclut :
    aucun échange enregistré depuis vingt-quatre heures est un fait, et la
    conséquence qu'on en tire porte sur **l'organisation du cabinet**, pas sur
    l'intention du prospect.

    Ce scénario vérifie donc trois choses ensemble : qu'elle agit, qu'elle écrit
    ce qu'elle a fait, et **qu'elle n'écrit rien au prospect**.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def test_un_dossier_sans_mouvement_change_de_main_tout_seul(
        self, plateforme, moteur_test
    ):
        client = plateforme

        # 1 · Un dossier affecté il y a quatre jours, jamais travaillé, et cette
        # fois **avec ses trois reprises intactes**.
        TestLeDossierQuiDort._dossier_endormi(reference="dos-a-reprendre")
        avant = TestLeDossierQuiDort._charge(moteur_test)
        assert avant.get("b.mballa") == 1, avant

        # 2 · Deux tours suffisent : le relais passe d'abord, la reprise ensuite.
        for _ in range(2):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]

        # 3 · La main a changé, et le dossier dit de qui elle vient.
        dossier = client.get("/acquisition/dossiers/dos-a-reprendre").json()
        assert dossier["responsable"] != "b.mballa", dossier
        assert dossier["reaffectations"] == 1
        assert dossier["responsables_passes"] == ["b.mballa"]
        assert dossier["etat"] == "AFFECTEE"

        # 4 · L'événement est déposé, et il dit de qui la main a été reprise.
        with moteur_test.connect() as connexion:
            (repris,) = list(
                connexion.execute(
                    text(
                        "SELECT cle, donnees FROM boite_d_envoi "
                        "WHERE nom = 'DossierRepris'"
                    )
                )
            )
        charge = repris.donnees["charge"]
        assert charge["precedent"] == "b.mballa"
        assert charge["responsable"] == dossier["responsable"]
        assert charge["reprises_restantes"] == 2
        # ⚠️ Ni le nom du prospect, ni son numéro.
        assert "Boulangerie" not in str(charge)
        assert "690112233" not in str(charge)

        # 5 · ⚠️ **Rien n'est parti au prospect.** La reprise réorganise le
        # cabinet, elle ne communique pas. Lui écrire « votre dossier a changé de
        # mains » l'informerait surtout que personne ne s'en occupait.
        from app.contextes.transverse.api import service_de_notification

        assert service_de_notification().derniers() == []
        assert client.get("/acquisition/rappels").json() == []

        # 6 · L'ancienneté repart de zéro : le nouveau responsable a son délai
        # plein, et la veille ne le signalera pas dans la foulée.
        assert client.get("/acquisition/dossiers/en-souffrance").json() == []

        # 7 · Et la charge a suivi la main, ce qui est ce que l'affectation lit.
        apres = TestLeDossierQuiDort._charge(moteur_test)
        assert apres.get("b.mballa") is None, apres
        assert apres.get(dossier["responsable"]) == 1, apres

    def test_deux_dossiers_repris_dans_le_meme_tour_ne_vont_pas_au_meme(
        self, plateforme, moteur_test
    ):
        """⚠️ **La charge est relue entre deux reprises d'un même passage.**

        ─────────────────────────────────────────────────────────────────────────
        Ce cas a été écrit parce qu'une mutation survivait : remplacer la charge
        réelle par un dictionnaire vide, dans le travail de reprise, ne faisait
        échouer aucun test. Tous ne reprenaient qu'un dossier, et la propriété ne
        se voit qu'à partir de deux.

        Sans relecture, trois dossiers repris dans le même passage iraient au
        même collaborateur, et précisément parce qu'il était le moins chargé au
        début du passage.
        ─────────────────────────────────────────────────────────────────────────
        """
        client = plateforme
        TestLeDossierQuiDort._dossier_endormi(
            reference="dos-repris-1", telephone="+237690112233"
        )
        TestLeDossierQuiDort._dossier_endormi(
            reference="dos-repris-2", telephone="+237690445566"
        )

        for _ in range(2):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]

        preneurs = {
            client.get(f"/acquisition/dossiers/{reference}").json()["responsable"]
            for reference in ("dos-repris-1", "dos-repris-2")
        }
        assert len(preneurs) == 2, (
            f"les deux dossiers sont allés à {preneurs} : la charge n'a pas été "
            "relue entre les deux reprises du même passage"
        )
        assert "b.mballa" not in preneurs


class TestLeReglementNotifieParLOperateur:
    """Le septième volet : **le produit devient vendable**.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE QUE CE SCÉNARIO PROUVE, ET QUI MANQUAIT**

    Le parcours d'acquisition vendait sans encaisser : un administrateur saisissait
    une référence externe à la main, pendant que l'intégration du prestataire —
    éprouvée, commentée incident par incident — tournait à côté pour l'autre flux,
    celui des devis et souscriptions. **Deux parcours d'encaissement cohabitaient
    sans se rejoindre.**

    Ce cas déroule la chaîne sans qu'aucun humain ne confirme le règlement : le
    collaborateur demande le débit, l'opérateur notifie, le tenant s'ouvre.

    ⚠️ La notification empruntée est **exactement** celle que le prestataire
    déclenchera : `POST /souscription/notification/tara`, avec sa charge utile
    réelle. Ce qui est simulé est l'appel du prestataire, rien d'autre.
    ─────────────────────────────────────────────────────────────────────────────
    """

    SLUG = "boulangerie-wouri"

    def test_le_client_regle_par_telephone_et_son_espace_s_ouvre(
        self, plateforme, moteur_test
    ):
        client = plateforme

        # 1 · Un dossier accepté, sa proforma transmise et acceptée.
        reference, numero = self._dossier_accepte(client)

        # 2 · Le collaborateur retient l'adresse et demande le débit. Il ne
        # confirme rien : le client n'a pas encore saisi son code.
        self._en_administrateur(client)
        demande = client.post(
            f"/acquisition/proformas/{numero}/reglement",
            json={"slug": self.SLUG},
        )
        assert demande.status_code == 202, demande.text
        corps = demande.json()
        assert corps["slug"] == self.SLUG
        assert Decimal(corps["montant"]) == Decimal("250000")

        # ⚠️ Rien n'est encaissé : le dossier est toujours à l'acceptation.
        assert (
            client.get(f"/acquisition/dossiers/{reference}").json()["etat"]
            == "ACCEPTEE"
        )

        # 3 · L'opérateur notifie. **Personne ne confirme.**
        with moteur_test.connect() as connexion:
            cle = connexion.execute(
                text(
                    "SELECT donnees->>'cle_idempotence' AS cle FROM paiement "
                    "WHERE identifiant = :p"
                ),
                {"p": corps["paiement"]},
            ).scalar_one()

        notification = client.post(
            "/souscription/notification/tara",
            json={
                "productId": cle,
                "paymentId": "TARA-RECETTE-0001",
                "status": "SUCCESS",
                "amount": "250000",
                "phoneNumber": "237690112233",
            },
        )
        assert notification.status_code == 200, notification.text
        resultat = notification.json()
        assert resultat["rapproche"] is True, resultat
        assert resultat["statut"] == "VALIDE", resultat
        assert resultat["reference_reglee"] == numero, resultat

        # 4 · Le dossier est payé, sans qu'aucun humain n'ait confirmé.
        assert (
            client.get(f"/acquisition/dossiers/{reference}").json()["etat"] == "PAYEE"
        )

        # 5 · L'événement d'ouverture est déposé, avec le slug retenu **avant** le
        # règlement. ⚠️ La machine n'a choisi aucune adresse.
        with moteur_test.connect() as connexion:
            (ouverture,) = list(
                connexion.execute(
                    text(
                        "SELECT cle, donnees FROM boite_d_envoi "
                        "WHERE nom = 'PaiementEncaissé'"
                    )
                )
            )
        assert ouverture.donnees["charge"]["slug"] == self.SLUG

        # 6 · La saga tourne : le tenant existe et son sous-domaine répond.
        for _ in range(3):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]
        with moteur_test.connect() as connexion:
            tenant = connexion.execute(
                text("SELECT slug, statut FROM tenant WHERE slug = :s"),
                {"s": self.SLUG},
            ).one()
        assert tenant.slug == self.SLUG

        # 7 · ⚠️ **La confirmation manuelle n'ouvre pas un second tenant.**
        # Les deux chemins dérivent le même identifiant d'événement du dossier,
        # donc une seule saga.
        rejeu = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"slug": self.SLUG, "reference_externe": "TARA-RECETTE-0001"},
        )
        assert rejeu.status_code == 200, rejeu.text
        assert rejeu.json()["rejeu"] is True, rejeu.json()
        with moteur_test.connect() as connexion:
            assert (
                connexion.execute(text("SELECT count(*) FROM tenant")).scalar_one() == 1
            )

    def test_un_reglement_ne_se_demande_pas_avant_l_acceptation(
        self, plateforme, moteur_test
    ):
        """⚠️ La contre-épreuve : faire sonner le téléphone de quelqu'un qui ne
        s'est engagé à rien est le seul dommage que cette route peut causer."""
        client = plateforme
        reference, numero = self._dossier_accepte(client, accepter=False)

        self._en_administrateur(client)
        reponse = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
        )
        assert reponse.status_code == 409, reponse.text

    def test_une_adresse_retenue_ne_se_remplace_pas(self, plateforme, moteur_test):
        """Une adresse annoncée au client ne se change pas en cours de règlement.

        ⚠️ Rejouable avec la **même** valeur : une demande relancée après un appel
        manqué ne doit pas échouer pour cela.
        """
        client = plateforme
        _, numero = self._dossier_accepte(client)
        self._en_administrateur(client)

        assert (
            client.post(
                f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
            ).status_code
            == 202
        )
        rejeu = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
        )
        assert rejeu.status_code == 202, rejeu.text

        autre = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": "autre-espace"}
        )
        assert autre.status_code == 409, autre.text
        assert "déjà retenu" in autre.json()["detail"]

    def test_l_encaissement_ouvre_l_espace_a_l_adresse_retenue_et_a_aucune_autre(
        self, plateforme, moteur_test
    ):
        """⚠️ **Pas 68 : la confirmation manuelle ne confrontait pas l'adresse retenue.**

        Le règlement demandé par téléphone retient l'adresse et l'annonce au client. S'il
        règle finalement en espèces, la confirmation manuelle ouvrait l'espace à l'adresse
        tapée ce jour-là. Deux chemins, deux espaces possibles pour un engagement.
        """
        client = plateforme
        reference, numero = self._dossier_accepte(client)
        self._en_administrateur(client)
        assert client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
        ).status_code == 202

        autre = client.post(
            f"/acquisition/proformas/{numero}/encaissement", json={"slug": "autre-espace"}
        )
        assert autre.status_code == 409, autre.text
        assert "déjà retenu" in autre.json()["detail"]

        bonne = client.post(
            f"/acquisition/proformas/{numero}/encaissement", json={"slug": self.SLUG}
        )
        assert bonne.status_code == 200, bonne.text
        assert bonne.json()["tenant"] == f"tnt-{self.SLUG}"

    def test_un_encaissement_sans_demande_prealable_retient_son_adresse(
        self, plateforme, moteur_test
    ):
        """Le règlement en espèces sans demande préalable : l'adresse confirmée est retenue,
        et le dossier dit ensuite laquelle a été ouverte."""
        client = plateforme
        reference, numero = self._dossier_accepte(client)
        self._en_administrateur(client)
        reponse = client.post(
            f"/acquisition/proformas/{numero}/encaissement", json={"slug": self.SLUG}
        )
        assert reponse.status_code == 200, reponse.text
        dossier = client.get(f"/acquisition/dossiers/{reference}").json()
        assert dossier["slug_retenu"] == self.SLUG, dossier

    # ── Le décor ─────────────────────────────────────────────────────────────

    @staticmethod
    def _en_administrateur(client):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": ADMINISTRATEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text

    def _dossier_accepte(self, client, *, accepter: bool = True):
        """Un dossier jusqu'à l'acceptation de sa proforma, par les routes réelles."""
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
            DepotDossiersSql,
            DepotProformasSql,
        )
        from app.contextes.souscription.api import (
            Canal,
            Consentement,
            DemandeDeContact,
            TarifArrete,
            deposer_une_demande,
            emettre,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        reference, numero = "dos-reglement", "PRO-2026-0010"
        instant = maintenant()
        demande = DemandeDeContact(
            identifiant=f"dc-{reference}",
            deposee_le=instant,
            nom="Boulangerie du Wouri",
            telephone="+237690112233",
            courriel=None,
            service_souhaite="creation-sarl",
            message=None,
            canal_prefere=Canal.APPEL,
            consentement=Consentement(
                accorde=False, recueilli_le=instant, version_du_texte="v1"
            ),
        )
        tarif = TarifArrete(
            montant=Decimal("250000"),
            plancher=Decimal("200000"),
            reference=Decimal("250000"),
            plafond=Decimal("375000"),
            version_bareme="2026.1",
            chiffre_par="b.mballa",
            valide_par="direction",
            arrete_le=instant,
        )
        proforma = emettre(
            numero=numero,
            dossier=reference,
            service="creation-sarl",
            tarif=tarif,
            contenu=b"%PDF recette",
            modele="creation-sarl",
            version_modele="2026.1",
            a_l_instant=instant,
        ).transmise(instant)

        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            dossiers = DepotDossiersSql(boutique.session, "CGA-BRCG")
            deposer_une_demande(demande, dossiers, reference=reference)
            dossier = dossiers.lire(reference)
            dossier = dossier.affecter("b.mballa", instant, motif="recette")
            dossier = dossier.premier_contact(instant).qualifier(instant)
            dossier = dossier.chiffrer(instant).emettre_la_proforma(instant)
            if accepter:
                dossier = dossier.accepter(instant)
            dossiers.enregistrer(dossier)
            DepotProformasSql(boutique.session, "CGA-BRCG").enregistrer(proforma)
        return reference, numero


class TestLaNotificationQuiNArriveJamais:
    """Le huitième volet : **le scénario qui coûte le plus cher**.

    ─────────────────────────────────────────────────────────────────────────────
    Le module de réconciliation le nomme lui-même :

        L'argent est parti, le service n'est pas ouvert, le client attend. Au bout
        d'un moment il repaie — et c'est là que le double-encaissement apparaît,
        non pas par un défaut de notre code, mais par **notre silence**.

    ⚠️ **Ce filet n'était jamais jeté.** `reconcilier` n'avait qu'un appelant :
    une route qu'un exploitant devait penser à cliquer. Le module écrit contre
    notre silence était lui-même silencieux.

    Tant que l'encaissement du parcours se confirmait à la main, une notification
    perdue se rattrapait par le geste humain qui suivait. Depuis le pas 30, une
    notification perdue est un client qui a payé et dont l'espace ne s'ouvre pas.

    Ce cas ne poste **aucune** notification. Seul l'appel sortant tranche.
    ─────────────────────────────────────────────────────────────────────────────
    """

    SLUG = "wouri-filet"

    def test_le_filet_repeche_un_reglement_et_ouvre_l_espace(
        self, plateforme, moteur_test, monkeypatch
    ):
        client = plateforme
        reference, numero = TestLeReglementNotifieParLOperateur()._dossier_accepte(
            client
        )
        TestLeReglementNotifieParLOperateur._en_administrateur(client)

        demande = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
        )
        assert demande.status_code == 202, demande.text
        identifiant = demande.json()["paiement"]

        # 1 · Le client a payé. **Rien n'arrive.** Aucune notification n'est postée.
        assert (
            client.get(f"/acquisition/dossiers/{reference}").json()["etat"]
            == "ACCEPTEE"
        )

        # 2 · Le paiement est vieilli de dix minutes : le filet n'interroge pas
        # une opération engagée il y a dix secondes, et le test ne doit pas
        # attendre pour autant.
        self._vieillir(identifiant, minutes=10)

        # 3 · Le prestataire, interrogé **par nous**, répond que c'est réglé.
        self._repondre_reussi(monkeypatch)

        comptes_rendus: list[str] = []
        for _ in range(3):
            _rendre_tout_du(moteur_test)
            rapport = _tour()
            assert all(r.reussi for r in rapport.resultats), [
                r.compte_rendu for r in rapport.resultats if not r.reussi
            ]
            comptes_rendus += [
                r.compte_rendu
                for r in rapport.resultats
                if r.travail == "reconciliation-des-paiements"
            ]

        # 4 · ⚠️ **L'espace s'ouvre, sans qu'aucune notification ne soit arrivée.**
        assert (
            client.get(f"/acquisition/dossiers/{reference}").json()["etat"] == "PAYEE"
        )
        with moteur_test.connect() as connexion:
            tenant = connexion.execute(
                text("SELECT slug FROM tenant WHERE slug = :s"), {"s": self.SLUG}
            ).one()
        assert tenant.slug == self.SLUG

        # 5 · Le compte rendu du travail dit ce que le filet a repêché : c'est le
        # seul nombre qui apprend si l'adresse de rappel fonctionne encore, et il
        # doit être lisible sans ouvrir la base.
        assert any("1 repêché" in rendu for rendu in comptes_rendus), comptes_rendus

    def test_sans_le_filet_le_client_attendrait_indefiniment(
        self, plateforme, moteur_test
    ):
        """⚠️ **La contre-épreuve, et elle est le cœur du volet.**

        Le prestataire ne répond pas — c'est ce que rend le mode simulé. Les tours
        passent, et rien ne bouge : c'est exactement l'état qui durait avant, à
        ceci près qu'il durait même quand le prestataire aurait répondu.

        Sans ce cas, un filet qui ouvrirait tous les tenants sans rien vérifier
        passerait le cas précédent sans broncher.
        """
        client = plateforme
        reference, numero = TestLeReglementNotifieParLOperateur()._dossier_accepte(
            client
        )
        TestLeReglementNotifieParLOperateur._en_administrateur(client)
        demande = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"slug": self.SLUG}
        )
        self._vieillir(demande.json()["paiement"], minutes=10)

        for _ in range(3):
            _rendre_tout_du(moteur_test)
            _tour()

        assert (
            client.get(f"/acquisition/dossiers/{reference}").json()["etat"]
            == "ACCEPTEE"
        )
        with moteur_test.connect() as connexion:
            assert (
                connexion.execute(text("SELECT count(*) FROM tenant")).scalar_one() == 0
            )

    # ── Le décor ─────────────────────────────────────────────────────────────

    @staticmethod
    def _vieillir(identifiant: str, *, minutes: int) -> None:
        """Recule la date d'initiation du paiement, dans la base.

        ⚠️ Le document **et** la colonne promue : une colonne qui ne suivrait pas
        le document ferait mentir la requête des paiements en attente, sans que la
        lecture de l'entité ne le montre jamais.
        """
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
            DepotPaiementsSql,
        )
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.horloge import maintenant
        from app.partage.locataire import etabli

        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            depot = DepotPaiementsSql(boutique.session, "CGA-BRCG")
            paiement = depot.lire(identifiant)
            depot.enregistrer(
                paiement.model_copy(
                    update={"initie_le": maintenant() - timedelta(minutes=minutes)}
                )
            )

    @staticmethod
    def _repondre_reussi(monkeypatch) -> None:
        """Le prestataire répond « réglé » à notre appel sortant.

        ⚠️ Ce qui est simulé est **l'appel du prestataire, rien d'autre** : le
        chemin emprunté ensuite est celui de la production, `appliquer_evenement`
        comprise, qui est la même fonction que pour une notification entrante.
        """
        from decimal import Decimal

        from app.contextes.souscription.adaptateurs.sortant.fournisseur_tara import (
            FournisseurTara,
        )
        from app.contextes.souscription.api import EvenementPaiement
        from app.partage.horloge import maintenant

        def repondre(self, *, cle_idempotence, reference_externe):
            return EvenementPaiement(
                identifiant_produit=cle_idempotence,
                reference_externe=reference_externe or "TARA-FILET-0001",
                reussi=True,
                montant=Decimal("250000"),
                telephone="699112233",
                recu_le=maintenant(),
            )

        monkeypatch.setattr(FournisseurTara, "interroger_statut", repondre)
