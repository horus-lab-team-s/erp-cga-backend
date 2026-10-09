"""Le prix se calcule sur ce que le dossier sait, et sur rien d'autre.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 65)

Le chiffrage recevait les faits dans la requête, « tant que la qualification n'est
pas persistée ». Elle l'était depuis longtemps. La recette qualifiait huit réponses et
chiffrait avec deux : le capital et le chiffre d'affaires, qui font le prix, n'entraient
pas dans le calcul. Et l'émission de la proforma recevait le plancher, la référence et
le plafond du barème dans la requête, alors que le motif n'est exigé qu'en dehors de cet
intervalle : un plancher abaissé faisait passer un rabais sans motif.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

RESPONSABLE = "b.mballa@cga-brcg.cm"
REPONSES = [
    ("forme_juridique", "SARL"),
    ("associes", 2),
    ("capital_social", "1000000"),
    ("apports_en_nature", False),
    ("chiffre_affaires_prevu", "DE_10M_A_50M"),
    ("salaries_prevus", 3),
    ("region_siege", "LITTORAL"),
    ("activite", "station-service et boutique"),
    # ⚠️ Ajoutés le 27 septembre, en version 2 du questionnaire : le dossier de
    # formalité s'ouvre désormais tout seul à l'encaissement, et il lui faut un
    # nom et un siège. Les demander à la qualification est le seul moment où
    # quelqu'un parle au client.
    ("denomination_souhaitee", "STATION BONABÉRI SARL"),
    ("siege", "Bonabéri, Douala"),
]


def _dossier_affecte(client, telephone):
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Station Bonabéri", "telephone": telephone, "service_souhaite": "CREATION",
            "canal_prefere": "APPEL", "message": "je veux créer une SARL",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    (dossier,) = [d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"]
    affecte = client.post(f"/acquisition/dossiers/{dossier['reference']}/affectation", json={})
    assert affecte.json()["affecte"] is True, affecte.text
    return dossier["reference"]


def _qualifier(client, reference, reponses):
    reponse = client.post(
        f"/acquisition/dossiers/{reference}/qualification",
        json={"reponses": [{"code": c, "valeur": v} for c, v in reponses]},
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _chiffrer(client, reference):
    return client.post(f"/acquisition/dossiers/{reference}/chiffrage", json={"debours": []})


def test_un_dossier_a_moitie_qualifie_ne_se_chiffre_pas(plateforme):
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, "699112233")
    _qualifier(client, reference, REPONSES[:2])
    reponse = _chiffrer(client, reference)
    assert reponse.status_code == 409, reponse.text
    assert "capital_social" in reponse.text, "le refus nomme ce qui manque"


def test_le_prix_suit_la_reponse_enregistree(plateforme):
    """⚠️ Le cas qui aurait vu le défaut : changer le capital enregistré change le prix.

    Au-delà de cinq millions de capital, la règle `TAR-CAP-001` du référentiel majore la
    prestation. Avec les faits envoyés par la requête, le capital enregistré ne changeait
    rien au prix.
    """
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, "699112244")
    _qualifier(client, reference, REPONSES)
    avant = _chiffrer(client, reference)
    assert avant.status_code == 200, avant.text

    _qualifier(client, reference, [("capital_social", "50000000")])
    apres = _chiffrer(client, reference)
    assert apres.status_code == 200, apres.text
    assert Decimal(apres.json()["reference"]) > Decimal(avant.json()["reference"]), (
        avant.json()["reference"], apres.json()["reference"],
    )


def test_la_proforma_porte_l_intervalle_recalcule_et_ignore_celui_qu_on_declare(plateforme):
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, "699112255")
    _qualifier(client, reference, REPONSES)
    chiffrage = _chiffrer(client, reference).json()

    sous_le_plancher = str(Decimal(chiffrage["plancher"]) - 1)
    # Sans motif, un montant sous le plancher recalculé est refusé : le rabais doit se
    # justifier, et aucun plancher déclaré ne peut plus le faire passer.
    refuse = client.post(
        f"/acquisition/dossiers/{reference}/proforma", json={"montant": sous_le_plancher}
    )
    assert refuse.status_code == 422, refuse.text

    emise = client.post(
        f"/acquisition/dossiers/{reference}/proforma",
        json={"montant": sous_le_plancher, "motif": "Rabais accordé pour un second dossier."},
    )
    assert emise.status_code == 201, emise.text
    assert emise.json()["plancher"] == chiffrage["plancher"]
    # La proforma garde les faits de la qualification, et le score déclaré à côté :
    # un score minoré pour baisser le prix se lirait sur le document.
    assert emise.json()["faits"]["capital_social"] == "1000000", emise.json()["faits"]
    assert emise.json()["faits"]["score_charge"] == "0"


def test_la_remise_des_statuts_ne_s_accorde_qu_a_qui_a_repondu_oui(plateforme):
    """⚠️ Pas 66, sur le parcours réel : `statuts_apportes` est facultative, et une question
    sans réponse accordait la remise de 50 000 FCFA."""
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, "699112266")
    _qualifier(client, reference, REPONSES)
    sans_reponse = _chiffrer(client, reference).json()
    assert "TAR-STA-001" not in [ligne["code"] for ligne in sans_reponse["lignes"]], sans_reponse
    assert any("statuts_apportes" in e for e in sans_reponse["echecs"]), sans_reponse["echecs"]

    _qualifier(client, reference, [("statuts_apportes", True)])
    apportes = _chiffrer(client, reference).json()
    assert "TAR-STA-001" in [ligne["code"] for ligne in apportes["lignes"]], apportes
    assert Decimal(apportes["reference"]) < Decimal(sans_reponse["reference"])


def test_un_code_inconnu_du_catalogue_est_refuse_a_la_porte(plateforme):
    """⚠️ L'ÉCART DE VOCABULAIRE EST MAINTENANT REFERMÉ, ET IL L'EST À L'ENTRÉE.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUE CE CAS VÉRIFIAIT AVANT, ET POURQUOI IL A CHANGÉ

    Le formulaire du site transmettait `creation`, le catalogue publie
    `CREATION` : trois vocabulaires pour une prestation. Un visiteur ouvrait un
    dossier qu'aucun questionnaire ne décrivait, et ce cas se contentait de
    vérifier que le serveur **nommait** ce qu'il ne connaissait pas au lieu de
    tomber. Son commentaire le disait : « ce cas ne referme PAS l'écart ».

    Il est refermé. Le champ est contrôlé contre le catalogue au dépôt : un code
    inconnu ne crée plus de dossier sans issue, il est refusé avec la liste de
    ce qui est admis.

    ⚠️ Le refus nomme **les deux** : le code reçu et les codes admis. Un refus
    qui dit seulement « code invalide » oblige à ouvrir le code pour comprendre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    client = plateforme
    refus = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Visiteur du site",
            "telephone": "699887744",
            # La minuscule d'autrefois, et un service que le site annonce sans
            # qu'il existe au catalogue.
            "service_souhaite": "creation",
            "canal_prefere": "APPEL",
            "message": "depuis le site",
        },
    )
    assert refus.status_code == 422, refus.text
    detail = str(refus.json()["detail"])
    assert "creation" in detail, "le refus ne dit pas ce qui a été reçu"
    assert "CREATION" in detail, "le refus ne dit pas ce qui est admis"

    inexistant = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Visiteur du site",
            "telephone": "699887745",
            # Annoncé sur le site sous « assistance juridique », absent du catalogue.
            "service_souhaite": "JURIDIQUE",
            "canal_prefere": "APPEL",
        },
    )
    assert inexistant.status_code == 422, inexistant.text


def test_un_service_sans_questionnaire_est_nomme_et_non_planté(plateforme):
    """Un service qualifiable nulle part rend 404, jamais 500.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE CAS EST RÉEL, ET IL LE RESTE APRÈS LA FERMETURE DE LA PORTE.

    `FORMATION` est au catalogue, donc admis au dépôt, et n'a **pas** de
    questionnaire : son dossier ne peut pas être qualifié. C'est exactement la
    condition qu'éprouvait ce cas, atteinte cette fois par la porte légitime
    plutôt que par un code fautif.

    Les routes du questionnaire et de la qualification le disaient proprement,
    en 404. Le chiffrage, lui, laissait l'exception s'échapper : le cabinet
    recevait « Internal Server Error » sur la seule route qui pouvait lui
    apprendre quoi corriger.
    ─────────────────────────────────────────────────────────────────────────────
    """
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Visiteur du site",
            "telephone": "699887744",
            "service_souhaite": "FORMATION",
            "canal_prefere": "APPEL",
            "message": "depuis le site",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"
    ]
    reference = dossier["reference"]
    client.post(f"/acquisition/dossiers/{reference}/affectation", json={})

    reponse = _chiffrer(client, reference)
    assert reponse.status_code == 404, reponse.text
    detail = reponse.json()["detail"]
    # Le message doit nommer le service demandé ET ce qui est qualifiable :
    # sans les deux, celui qui corrige ne sait pas quoi écrire.
    assert "FORMATION" in detail
    assert "CREATION" in detail


def test_la_qualification_d_un_service_sans_questionnaire_se_nomme(plateforme):
    """L'état de qualification rend 404 nommé, et non 500.

    ⚠️ C'ÉTAIT LE DERNIER DES QUATRE APPELS À `prendre()` SANS REPRISE.

    Conséquence mesurée le 27 septembre sur la pile de démonstration : la fiche
    d'un dossier dont le service n'a pas de questionnaire — `FORMATION`, ou un
    dossier déposé sous un ancien code — **ne s'ouvrait plus du tout**. Le
    collaborateur voyait une page d'erreur à la place du dossier de son client,
    et n'apprenait rien.

    Les trois autres appels rendaient déjà 404 avec un message qui nomme le
    service demandé et les services qualifiables ; celui-ci laissait l'exception
    remonter.
    """
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Atelier sans questionnaire", "telephone": "699887755",
            "service_souhaite": "FORMATION",
            "canal_prefere": "APPEL", "message": "une session pour mes salariés",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    (dossier,) = [d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"]

    etat = client.get(f"/acquisition/dossiers/{dossier['reference']}/qualification")
    assert etat.status_code == 404, etat.text
    detail = etat.json()["detail"]
    assert "FORMATION" in detail
    assert "CREATION" in detail, "le message doit nommer ce qui EST qualifiable"



class TestUnPrixArreteParLeGerantSeFacture:
    """Trois services du catalogue mouraient dans le tunnel.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUI SE PASSAIT, PAS À PAS, SUR LA PILE

        1. une visiteuse demande une FORMATION depuis le site   → 201
        2. le dossier entre dans la file du cabinet             → DEPOSEE
        3. le gérant arrête le prix à 75 000 F, motif à l'appui → 201
        4. le chargé de clientèle émet la proforma              → 404

            « aucun questionnaire pour le service FORMATION »

    Le cabinet pouvait **décider un prix et ne pas pouvoir le facturer**.
    `FORMATION`, `DOMICILIATION` et `PONCTUEL` étaient proposés sur le site
    public, mobilisaient un responsable, et s'arrêtaient là.

    ⚠️ La qualification existe pour **calculer** un prix que personne ne connaît
    d'avance. L'exiger d'un prix déjà arrêté était une règle du sur-étude
    appliquée à tout.

    ⚠️ **ET LA GARDE QUI COMPTE** : un tarif `A_VALIDER` — ceux de la maquette de
    la vitrine, que personne au cabinet n'a fixés — ne passe PAS par ce chemin.
    C'est ce qui empêche d'encaisser un prix que personne n'a décidé, et le
    dernier cas de cette classe le vérifie.
    ─────────────────────────────────────────────────────────────────────────────
    """

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ LA DATE DU JOUR, ET NON UNE DATE ÉCRITE EN DUR.
    #
    # Ces cas portaient « 2026-09-29 ». Ils ont passé ce jour-là et sont tombés
    # le lendemain : la route refuse un prix fixé dans le passé — « un devis déjà
    # émis changerait de prix » —, la fixation rendait donc 422, FORMATION
    # retrouvait son absence de prix ferme, et le chiffrage repartait sur la
    # qualification pour finir en 404.
    #
    # Un cas qui expire à minuit ne mesure rien le surlendemain, et son message
    # d'échec parle d'autre chose que de sa cause.
    # ─────────────────────────────────────────────────────────────────────────
    JOUR = date.today().isoformat()

    def _dossier_de_formation(self, client, telephone):
        depot = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Atelier Bonanjo",
                "telephone": telephone,
                "service_souhaite": "FORMATION",
                "canal_prefere": "APPEL",
            },
        )
        assert depot.status_code in (200, 201), depot.text
        (dossier,) = [
            d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"
        ]
        client.post(f"/acquisition/dossiers/{dossier['reference']}/affectation", json={})
        return dossier["reference"]

    def _appeler_le_client(self, client, reference):
        """Le responsable joint le client et le déclare.

        ⚠️ C'est le SEUL geste qui engage l'échange pour ces services : sans
        questionnaire, la qualification — qui le faisait jusqu'ici en passant —
        est impossible.
        """
        fait = client.post(
            f"/acquisition/dossiers/{reference}/premier-contact",
            json={"commentaire": "Client joint, session de novembre retenue."},
        )
        assert fait.status_code == 200, fait.text
        assert fait.json()["etat"] == "EN_CONVERSATION"
        assert fait.json()["premier_echange_le"], (
            "sans cette date, la veille réaffecte un responsable qui vient d'appeler"
        )

    def _chiffrer_au_prix_du_gerant(self, client, reference):
        """Le même geste que pour une création : on chiffre, puis on émet.

        ⚠️ Pour un prix arrêté, le chiffrage ne calcule rien — il constate. Mais il
        reste le geste qui fait passer le dossier à `CHIFFRÉE`, et le sauter ferait
        émettre une proforma sur un dossier que personne n'a ouvert.
        """
        chiffrage = client.post(f"/acquisition/dossiers/{reference}/chiffrage", json={})
        assert chiffrage.status_code == 200, chiffrage.text
        return chiffrage.json()

    def _fixer(self, client, service, montant):
        """⚠️ **ELLE VÉRIFIE SON PROPRE SUCCÈS**, et c'est la seconde leçon du
        même incident.

        Deux des trois cas appelaient cette aide sans regarder ce qu'elle rendait.
        Quand la fixation a commencé à échouer en 422, l'échec est ressorti trois
        pas plus loin, sous la forme « aucun questionnaire pour FORMATION » — un
        message qui envoie chercher exactement là où il n'y a rien.
        """
        reponse = self._fixation(client, service, montant)
        assert reponse.status_code == 201, reponse.text
        return reponse

    def _fixation(self, client, service, montant):
        return client.post(
            "/souscription/tarifs",
            json={
                "service": service,
                "montant": str(montant),
                "a_partir_du": self.JOUR,
                "motif": "Prix arrêté pour la session interentreprises.",
            },
        )

    def test_sans_decision_du_gerant_la_formation_reste_invendable(self, plateforme):
        """⚠️ LA CONTRE-ÉPREUVE, ET ELLE PASSE EN PREMIER.

        Sans elle, le cas suivant prouverait seulement qu'une proforma sort — pas
        que c'est la **décision du gérant** qui l'a permise.
        """
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)
        reference = self._dossier_de_formation(client, "699551100")

        refus = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": "75000"}
        )
        assert refus.status_code == 404, refus.text
        assert "questionnaire" in refus.json()["detail"]

    def test_le_prix_arrete_rend_la_formation_facturable(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)
        reference = self._dossier_de_formation(client, "699551111")
        self._appeler_le_client(client, reference)

        self._fixer(client, "FORMATION", 75_000)
        self._chiffrer_au_prix_du_gerant(client, reference)

        emise = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": "75000"}
        )
        assert emise.status_code == 201, emise.text
        proforma = emise.json()
        assert Decimal(proforma["montant"]) == Decimal(75_000)
        # ⚠️ L'intervalle est réduit à un point : il n'y a rien à négocier sur un
        # prix que le gérant a arrêté, et le responsable doit le voir.
        assert Decimal(proforma["plancher"]) == Decimal(75_000)
        assert Decimal(proforma["plafond"]) == Decimal(75_000)

    def test_le_document_dit_que_le_prix_vient_du_gerant(self, plateforme):
        """Six mois plus tard, on doit pouvoir réexpliquer ce montant."""
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)
        reference = self._dossier_de_formation(client, "699551122")
        self._appeler_le_client(client, reference)
        self._fixer(client, "FORMATION", 75_000)
        self._chiffrer_au_prix_du_gerant(client, reference)

        proforma = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": "75000"}
        ).json()
        assert proforma["faits"]["prix_arrete_par_le_gerant"] == "75000"

    def test_s_ecarter_du_prix_arrete_exige_toujours_un_motif(self, plateforme):
        """⚠️ Ce chemin n'affaiblit AUCUNE garde de l'autre.

        `TarifArrete` exige un motif hors de l'intervalle, et l'intervalle est ici
        un point. Vendre une formation 50 000 F sans rien écrire resterait un
        rabais sans trace, ce que le contrôle interne cherche précisément.
        """
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)
        reference = self._dossier_de_formation(client, "699551133")
        self._appeler_le_client(client, reference)
        self._fixer(client, "FORMATION", 75_000)
        self._chiffrer_au_prix_du_gerant(client, reference)

        sans_motif = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": "50000"}
        )
        assert sans_motif.status_code == 422, sans_motif.text

        avec_motif = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={"montant": "50000", "motif": "Groupe de huit personnes, remise consentie."},
        )
        assert avec_motif.status_code == 201, avec_motif.text

    def test_la_creation_continue_de_passer_par_la_qualification(self, plateforme):
        """⚠️ Le chemin du sur-étude n'a pas bougé, et c'est l'essentiel.

        `CREATION` n'a pas de prix ferme : son montant dépend de la forme, du
        capital et du greffe. Il doit continuer d'exiger la qualification, sans
        quoi cette correction aurait ouvert une porte au lieu d'en ouvrir une.
        """
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)
        reference = _dossier_affecte(client, "699551144")

        refus = client.post(
            f"/acquisition/dossiers/{reference}/proforma", json={"montant": "250000"}
        )
        assert refus.status_code == 409, refus.text
        assert "qualification incomplète" in refus.json()["detail"]
