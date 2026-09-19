"""Le parcours d'acquisition, par HTTP.

Jusqu'ici, six pas de domaine et d'application n'exposaient **aucune route** :
tout était construit et rien n'était joignable. Ces tests parcourent le chemin
réel, du formulaire public à la qualification.

Ils vérifient trois choses que les tests de domaine ne peuvent pas dire :

* **ce qui est public l'est**, et ce qui ne l'est pas est refusé ;
* **les permissions commerciales sont distinctes de celles du portefeuille** —
  un comptable ne voit pas la file des prospects, un commercial ne voit pas la
  comptabilité ;
* **le parcours fonctionne avec le référentiel réel**, messagerie éteinte
  comprise.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    recharger_le_referentiel,
    reinitialiser_dossiers,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO
from app.main import app


@pytest.fixture(autouse=True)
def parcours_neuf():
    reinitialiser_dossiers()
    recharger_le_referentiel()
    yield
    reinitialiser_dossiers()
    recharger_le_referentiel()


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def _demande(**surcharges) -> dict:
    defauts = {
        "nom": "Abena Ndzana",
        "telephone": "699112233",
        "service_souhaite": "creation-sarl",
        "canal_prefere": "APPEL",
    }
    return {**defauts, **surcharges}


# ── Le formulaire public ─────────────────────────────────────────────────────


class TestFormulairePublic:
    def test_il_ne_demande_aucune_session(self, client):
        """Exiger une session avant de laisser quelqu'un demander un rappel
        reviendrait à demander à un visiteur de s'inscrire pour poser une
        question."""
        reponse = client.post("/acquisition/demandes", json=_demande())
        assert reponse.status_code == 201
        assert reponse.json()["recue"] is True

    def test_le_canal_annonce_est_celui_du_repli_reel(self, client):
        """Le visiteur coche la messagerie, elle n'est pas ouverte : on lui
        annonce l'appel, et c'est ce qui se produira. Lui promettre un canal
        qu'on n'exploite pas serait la manière la plus simple de le décevoir au
        premier contact."""
        reponse = client.post(
            "/acquisition/demandes",
            json=_demande(canal_prefere="WHATSAPP", consentement_whatsapp=True),
        )
        assert reponse.status_code == 201
        assert reponse.json()["canal_de_rappel"] == "APPEL"

    def test_un_numero_inexploitable_est_refuse_franchement(self, client):
        """Un numéro qu'on n'a pas su normaliser est un numéro sur lequel
        personne ne rappellera."""
        reponse = client.post("/acquisition/demandes", json=_demande(telephone="123"))
        assert reponse.status_code == 422

    def test_le_consentement_ne_se_donne_pas_par_omission(self, client):
        """Le champ absent vaut refus. L'inverse ferait consentir par
        omission, ce que ni la plateforme ni le droit n'admettent."""
        reponse = client.post(
            "/acquisition/demandes", json=_demande(canal_prefere="WHATSAPP")
        )
        assert reponse.status_code == 422

    def test_la_reponse_ne_dit_pas_s_il_y_a_eu_rattachement(self, client):
        """Annoncer « vous avez déjà écrit » n'apporte rien au visiteur et laisse
        croire à un refus. La réponse est la même dans les deux cas."""
        premiere = client.post("/acquisition/demandes", json=_demande())
        seconde = client.post("/acquisition/demandes", json=_demande())
        assert premiere.json() == seconde.json()

    def test_la_reference_du_dossier_ne_circule_pas(self, client):
        """C'est une donnée interne. La faire circuler dans une réponse publique
        en ferait un identifiant devinable."""
        corps = client.post("/acquisition/demandes", json=_demande()).json()
        assert "dossier" not in corps
        assert "reference" not in corps

    def test_les_canaux_exploites_sont_publics(self, client):
        """La vitrine ne doit proposer que ce que le centre exploite
        réellement."""
        corps = client.get("/acquisition/canaux").json()
        assert corps["actifs"] == ["APPEL", "COURRIEL"]
        assert corps["messagerie_prete"] is False
        ferme = next(d for d in corps["detail"] if d["canal"] == "WHATSAPP")
        assert ferme["actif"] is False
        assert ferme["motif"]


# ── La console ───────────────────────────────────────────────────────────────


class TestConsoleProtegee:
    @pytest.mark.parametrize(
        "chemin",
        [
            "/acquisition/dossiers",
            "/acquisition/dossiers/dos-1",
            "/acquisition/dossiers/en-souffrance",
            "/acquisition/motifs-de-classement",
            "/acquisition/questionnaires/creation-sarl",
        ],
    )
    def test_la_lecture_des_prospects_demande_une_session(self, client, chemin):
        assert client.get(chemin).status_code in (401, 403)

    @pytest.mark.parametrize(
        "chemin",
        [
            "/acquisition/dossiers/dos-1/affectation",
            "/acquisition/dossiers/dos-1/qualification",
            "/acquisition/dossiers/dos-1/sans-suite",
            "/acquisition/proformas/PRO-2026-0001/reglement",
        ],
    )
    def test_l_ecriture_demande_une_session(self, client, chemin):
        assert client.post(chemin, json={}).status_code in (401, 403, 422)


class TestPermissionsCommerciales:
    """⚠️ Elles sont **distinctes** de celles du portefeuille, et le test le
    vérifie sur les rôles réels.

    Les réutiliser donnerait au commercial l'accès à la comptabilité de tous les
    adhérents, et priverait à l'inverse un chargé de clientèle de la file des
    demandes. La séparation coûte deux membres d'énumération ; la confusion
    coûterait une fuite dont personne ne verrait la cause.
    """

    def test_le_comptable_ne_voit_pas_les_prospects(self):
        from app.contextes.transverse.domaine.roles import (
            PERMISSIONS_PAR_ROLE,
            Permission,
            Role,
        )

        assert Permission.LIRE_PROSPECT not in PERMISSIONS_PAR_ROLE[Role.COMPTABLE]

    def test_le_charge_de_formalites_les_voit_et_les_qualifie(self):
        """C'est lui qui reçoit les demandes de création."""
        from app.contextes.transverse.domaine.roles import (
            PERMISSIONS_PAR_ROLE,
            Permission,
            Role,
        )

        droits = PERMISSIONS_PAR_ROLE[Role.CHARGE_FORMALITES]
        assert Permission.LIRE_PROSPECT in droits
        assert Permission.QUALIFIER_PROSPECT in droits
        assert Permission.LIRE_COMPTABILITE not in droits

    def test_l_administrateur_affecte_mais_ne_qualifie_pas(self):
        """Remplir un questionnaire suppose d'avoir parlé au client."""
        from app.contextes.transverse.domaine.roles import (
            PERMISSIONS_PAR_ROLE,
            Permission,
            Role,
        )

        droits = PERMISSIONS_PAR_ROLE[Role.ADMINISTRATEUR]
        assert Permission.LIRE_PROSPECT in droits
        assert Permission.QUALIFIER_PROSPECT not in droits


# ── Le questionnaire ─────────────────────────────────────────────────────────


class TestQuestionnaireExpose:
    def test_il_n_est_pas_public(self, client):
        assert client.get("/acquisition/questionnaires/creation-sarl").status_code in (
            401,
            403,
        )


# ── Le montage ───────────────────────────────────────────────────────────────


class TestChiffrage:
    def test_il_n_est_pas_public(self, client):
        """Un prix se calcule pour un dossier, pas pour un visiteur."""
        assert client.post(
            "/acquisition/dossiers/dos-1/chiffrage", json={}
        ).status_code in (401, 403)


class TestMontage:
    def test_les_routes_du_parcours_sont_montees(self, client):
        """Six pas de domaine n'exposaient rien. Ce test tombe le jour où le
        routeur cesse d'être monté, ce qui rendrait tout le parcours
        injoignable sans qu'aucun test de domaine ne bouge.

        ⚠️ La liste est **close**, et c'est ce qui a de la valeur : ajouter une route
        sous ce préfixe fait échouer ce cas, et oblige à décider si elle doit être
        publique comme le dépôt de demande ou protégée comme les autres.
        """
        chemins = client.get("/openapi.json").json()["paths"]
        acquisition = sorted(c for c in chemins if c.startswith("/acquisition"))
        assert acquisition == [
            "/acquisition/canaux",
            "/acquisition/demandes",
            "/acquisition/dossiers",
            "/acquisition/dossiers/en-souffrance",
            "/acquisition/dossiers/{reference}",
            "/acquisition/dossiers/{reference}/affectation",
            "/acquisition/dossiers/{reference}/chiffrage",
            "/acquisition/dossiers/{reference}/proforma",
            "/acquisition/dossiers/{reference}/qualification",
            "/acquisition/dossiers/{reference}/sans-suite",
            "/acquisition/motifs-de-classement",
            "/acquisition/proformas/{numero}/acceptation",
            "/acquisition/proformas/{numero}/consultation",
            "/acquisition/proformas/{numero}/encaissement",
            "/acquisition/proformas/{numero}/reglement",
            "/acquisition/proformas/{numero}/transmission",
            "/acquisition/questionnaires/{service}",
            "/acquisition/rappels",
            "/acquisition/rappels/{identifiant}/fait",
            # Pas 93 : protégée par LIRE_PROSPECT, source de la recherche globale.
            "/acquisition/recherche",
        ]

    def test_le_referentiel_se_charge_au_premier_appel(self, client):
        """Quatre paquets de configuration lus sur disque. Un fichier absent ou
        mal rédigé doit se voir au premier appel, pas à la première vente."""
        assert client.get("/acquisition/canaux").status_code == 200



class TestLaQualificationSeConserve:
    """⚠️ **La route jetait son travail.**

    Elle construisait la qualification, validait chaque réponse au type de sa
    question, rendait l'avancement et les faits, puis n'en gardait rien. Un
    responsable qui répondait à cinq questions sur douze et revenait le lendemain
    recommençait à zéro, sans qu'aucune erreur ne se produise.
    """

    #: `b.mballa` porte le rôle DIRECTION, qui donne `QUALIFIER_PROSPECT`. Le
    #: choix d'un compte réel du jeu de démonstration est délibéré : forger une
    #: habilitation pour le test vérifierait la route sans vérifier que quelqu'un,
    #: dans le cabinet, a réellement le droit de qualifier.
    QUALIFICATEUR = "b.mballa@cga-brcg.cm"

    @pytest.fixture
    def qualificateur(self, client):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": self.QUALIFICATEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture
    def dossier(self, qualificateur):
        """Un dossier déposé par la route publique, puis retrouvé dans la liste.

        ⚠️ **La route publique ne rend pas la référence**, et c'est délibéré : la
        livrer au visiteur lui donnerait de quoi deviner celles des autres. Le
        responsable la découvre dans sa file, ce qui est aussi le geste réel.
        """
        depot = qualificateur.post("/acquisition/demandes", json=_demande())
        assert depot.status_code in (200, 201), depot.text

        file = qualificateur.get("/acquisition/dossiers")
        assert file.status_code == 200, file.text
        return file.json()[0]["reference"]

    def _repondre(self, client, reference, reponses):
        return client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": [{"code": c, "valeur": v} for c, v in reponses]},
        )

    def test_deux_passages_s_additionnent(self, qualificateur, dossier):
        """⚠️ **Le défaut d'origine, figé.**

        La qualification se complète au fil des échanges avec le client : on
        apprend rarement tout d'un coup.
        """
        premier = self._repondre(
            qualificateur, dossier, [("forme_juridique", "SARL"), ("associes", 2)]
        )
        assert premier.status_code == 200, premier.text
        assert premier.json()["avancement"]["repondues"] == 2

        second = self._repondre(qualificateur, dossier, [("capital_social", "1000000")])
        assert second.status_code == 200, second.text
        assert second.json()["avancement"]["repondues"] == 3, (
            "le second passage est reparti de zéro : la qualification n'est pas "
            "conservée entre deux appels"
        )

    def test_la_lecture_rend_ce_qui_a_ete_appris(self, qualificateur, dossier):
        """⚠️ Sans cette route, la qualification conservée serait invisible.

        Le responsable qui reprend un dossier ne pourrait découvrir son avancement
        qu'en renvoyant des réponses, c'est-à-dire en écrivant pour lire.
        """
        self._repondre(qualificateur, dossier, [("forme_juridique", "SARL")])

        lu = qualificateur.get(f"/acquisition/dossiers/{dossier}/qualification")
        assert lu.status_code == 200, lu.text
        assert lu.json()["commencee"] is True
        assert lu.json()["faits"]["forme_juridique"] == "SARL"

    def test_un_dossier_jamais_qualifie_le_dit_sans_404(self, qualificateur, dossier):
        """« Pas encore commencée » est l'état de tout dossier neuf. Un 404
        laisserait croire que le dossier n'existe pas, alors qu'il attend seulement
        d'être qualifié."""
        lu = qualificateur.get(f"/acquisition/dossiers/{dossier}/qualification")
        assert lu.status_code == 200, lu.text
        assert lu.json()["commencee"] is False
        assert lu.json()["avancement"]["repondues"] == 0

    def test_un_lot_fautif_ne_laisse_rien_derriere_lui(self, qualificateur, dossier):
        """⚠️ Les réponses sont appliquées à un objet figé, et l'écriture n'a lieu
        qu'après la dernière.

        Un lot dont une réponse est fautive ne doit pas laisser les précédentes en
        base : le responsable corrige et renvoie son lot entier, sans avoir à se
        demander ce qui est déjà passé.
        """
        refus = self._repondre(
            qualificateur,
            dossier,
            [("forme_juridique", "SARL"), ("associes", "pas un nombre")],
        )
        assert refus.status_code == 422, refus.text

        lu = qualificateur.get(f"/acquisition/dossiers/{dossier}/qualification")
        assert lu.json()["commencee"] is False, (
            "une réponse d'un lot refusé a été conservée"
        )


class TestLAffectationSeSuffitAElleMeme:
    """⚠️ **La route exigeait des candidats que personne ne pouvait fournir.**

    L'appelant devait donner, pour chaque collaborateur, son nombre de dossiers
    ouverts et sa charge pondérée. Aucune console ne sait cela sans refaire côté
    client le travail du serveur : la route était utilisable en test et inutilisable
    en service.
    """

    AFFECTEUR = "b.mballa@cga-brcg.cm"

    @pytest.fixture
    def affecteur(self, client):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": self.AFFECTEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture
    def dossier_avec_message(self, affecteur):
        """Un dossier dont le prospect a écrit un message mentionnant un lieu.

        ⚠️ Le message est là **exprès** : la route l'employait comme région, et un
        texte mentionnant un lieu aurait pu faire correspondre une agence par
        coïncidence de mots.
        """
        depot = affecteur.post(
            "/acquisition/demandes",
            json=_demande(message="je suis à Bonabéri, près du marché"),
        )
        assert depot.status_code in (200, 201), depot.text
        return affecteur.get("/acquisition/dossiers").json()[0]["reference"]

    def test_un_corps_vide_suffit(self, affecteur, dossier_avec_message):
        """Le serveur monte les candidatures depuis l'annuaire et la charge réelle."""
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier_avec_message}/affectation", json={}
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["affecte"] is True
        assert reponse.json()["responsable"]

    def test_le_motif_dit_parmi_combien_de_candidats(
        self, affecteur, dossier_avec_message
    ):
        """Un choix sans son assiette n'est pas motivé : « retenu » ne dit rien si
        l'on ignore s'il y avait deux candidats ou vingt."""
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier_avec_message}/affectation", json={}
        )
        assert "candidats" in reponse.json()["motif"]

    def test_la_liste_nomme_le_responsable(self, affecteur, dossier_avec_message):
        """Pas 64 : la console affiche une personne, pas un identifiant de compte."""
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier_avec_message}/affectation", json={}
        )
        responsable = reponse.json()["responsable"]
        (ligne,) = [
            d for d in affecteur.get("/acquisition/dossiers").json()
            if d["reference"] == dossier_avec_message
        ]
        assert ligne["responsable"] == responsable
        assert ligne["responsable_nom"] and ligne["responsable_nom"] != responsable, ligne

    def test_le_message_du_prospect_n_est_pas_sa_region(
        self, affecteur, dossier_avec_message
    ):
        """⚠️ **Le défaut corrigé, figé.**

        Le message mentionne un lieu. Si la route l'employait encore comme région,
        le critère de proximité le comparerait aux agences.

        ⚠️ **L'observation s'est inversée au pas 64.** Une région non déclarée
        pénalisait tout le monde, et ce cas exigeait la réserve d'éloignement comme
        preuve que la région était vide. Depuis que le critère se tait sans région
        déclarée, c'est **l'absence** de réserve qui le prouve : un message employé
        comme région est un texte non vide, différent de toute agence, et la réserve
        reparaîtrait.
        """
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier_avec_message}/affectation", json={}
        )
        assert "Agence différente" not in reponse.json()["motif"], (
            "une réserve de proximité est apparue alors que le site ne collecte aucune "
            "région : le message du prospect a probablement été employé comme région"
        )

    def test_des_candidats_fournis_restent_employes(
        self, affecteur, dossier_avec_message
    ):
        """⚠️ Simuler une affectation avec un effectif hypothétique est une question
        que la direction pose — « si je recrute un chargé de formalités de plus, qui
        prendrait ce dossier ? » — et à laquelle l'annuaire réel ne peut pas
        répondre."""
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier_avec_message}/affectation",
            json={
                "candidats": [
                    {
                        "responsable": "recrue-hypothetique",
                        "agence": "",
                        "competences": [],
                        "competence_requise": "",
                        "dossiers_ouverts": 0,
                        "charge_ponderee": "0",
                        "disponible": True,
                    }
                ]
            },
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["responsable"] == "recrue-hypothetique"


class TestLAcceptationEstPubliqueEtScellee:
    """⚠️ **Publique, et elle doit l'être.**

    Un client n'a pas de compte sur la plateforme, et lui en imposer un pour accepter
    un devis ferait perdre la moitié des acceptations. Le lien signé **est**
    l'authentification : scellé sur le numéro, la version et l'échéance.
    """

    def test_elle_ne_demande_aucune_session(self, client):
        """Elle refuse pour un motif de **sceau**, jamais d'habilitation.

        Un `401` ici enverrait le client créer un compte pour signer un devis.
        """
        reponse = client.post(
            "/acquisition/proformas/PRO-2026-0001/acceptation",
            json={
                "sceau": "x" * 32,
                "expire_le": "2026-12-31T00:00:00",
                "version": 1,
                "identite_declaree": "Awa NDONGO",
            },
        )
        assert reponse.status_code not in (401, 403), (
            "l'acceptation demande une session : un client sans compte ne pourrait "
            "plus signer son devis"
        )

    def test_un_sceau_forge_ne_passe_pas(self, client):
        """⚠️ Le sceau est vérifié **avant** la proforma.

        Une vérification qui lirait d'abord le document permettrait de sonder
        l'existence d'un numéro sans posséder de lien, ce qui est précisément ce
        qu'on refuse ailleurs par des `404`.
        """
        reponse = client.post(
            "/acquisition/proformas/PRO-2026-0001/acceptation",
            json={
                "sceau": "sceau-manifestement-invente",
                "expire_le": "2026-12-31T00:00:00",
                "version": 1,
                "identite_declaree": "Awa NDONGO",
            },
        )
        # ⚠️ Pas 67 : ce cas acceptait `404` **ou** `422`, alors que son docstring dit la
        # règle. Un `404` sur un numéro sans lien est exactement l'énumération refusée ;
        # la tolérance laissait passer le défaut qu'il décrivait.
        assert reponse.status_code == 422, reponse.text


# ── Ce qui dort, et ce qu'on en fait ─────────────────────────────────────────


class TestCeQuiDort:
    """Les trois routes qui manquaient, et le piège de routage qu'elles ont révélé.

    ⚠️ **Le premier cas de cette classe garde un ordre de déclaration.** Écrite
    après `/dossiers/{reference}`, la route `en-souffrance` était injoignable :
    FastAPI essaie les routes dans l'ordre, le paramètre capturait le segment
    littéral, et la réponse était « aucun dossier commercial en-souffrance ». Un
    404 assez crédible pour qu'on conclue que rien ne dort.

    Une route masquée ne lève aucune erreur et ne manque à aucun test qui ne
    l'appelle pas. Seul l'appel la trouve.
    """

    #: `b.mballa` porte DIRECTION, donc `QUALIFIER_PROSPECT` : c'est un compte
    #: réel du jeu de démonstration, pas une habilitation forgée pour le test.
    QUALIFICATEUR = "b.mballa@cga-brcg.cm"

    @pytest.fixture
    def qualificateur(self, client):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": self.QUALIFICATEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture
    def dossier(self, qualificateur):
        depot = qualificateur.post("/acquisition/demandes", json=_demande())
        assert depot.status_code in (200, 201), depot.text
        file = qualificateur.get("/acquisition/dossiers")
        assert file.status_code == 200, file.text
        return file.json()[0]["reference"]

    def test_la_route_en_souffrance_n_est_pas_masquee_par_le_parametre(
        self, qualificateur
    ):
        """Elle répond une liste, et non « dossier en-souffrance introuvable »."""
        reponse = qualificateur.get("/acquisition/dossiers/en-souffrance")
        assert reponse.status_code == 200, reponse.text
        assert isinstance(reponse.json(), list)

    def test_un_dossier_frais_ne_dort_pas(self, qualificateur, dossier):
        """Il vient d'être déposé : rien ne doit remonter.

        ⚠️ Ce cas est ce qui rend le suivant probant. Sans lui, une route qui
        rendrait toujours la liste vide passerait pour correcte.
        """
        reponse = qualificateur.get("/acquisition/dossiers/en-souffrance")
        assert reponse.status_code == 200
        assert [ligne["reference"] for ligne in reponse.json()] == []

    def test_le_vocabulaire_vient_du_referentiel(self, qualificateur):
        """⚠️ Et non d'une énumération du code.

        Ajouter un motif ne doit demander aucun déploiement : c'est le sens de
        l'approche configuration. Ce cas vérifie que la route sert bien ce que le
        fichier contient, y compris l'exigence de précision.
        """
        reponse = qualificateur.get("/acquisition/motifs-de-classement")
        assert reponse.status_code == 200, reponse.text
        motifs = {m["code"]: m for m in reponse.json()}
        assert "jamais-rappele" in motifs
        assert motifs["autre"]["precision_requise"] is True
        assert motifs["prix"]["precision_requise"] is False

    def test_un_motif_hors_vocabulaire_est_refuse_en_nommant_les_bons(
        self, qualificateur, dossier
    ):
        """Le refus liste les motifs valides.

        Un 422 qui dit seulement « motif invalide » oblige à chercher le fichier ;
        celui-ci porte la réponse.
        """
        reponse = qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite",
            json={"motif": "pas-interesse"},
        )
        assert reponse.status_code == 422, reponse.text
        assert "jamais-rappele" in reponse.json()["detail"]

    def test_un_motif_qui_ne_dit_rien_seul_exige_sa_precision(
        self, qualificateur, dossier
    ):
        """⚠️ « autre » sans texte deviendrait la rubrique majoritaire en six mois,
        et le vocabulaire ne servirait plus à rien."""
        sans = qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite", json={"motif": "autre"}
        )
        assert sans.status_code == 422, sans.text

        avec = qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite",
            json={"motif": "autre", "precision": "demande de stage, pas un client"},
        )
        assert avec.status_code == 200, avec.text

    def test_le_classement_ferme_le_dossier_et_garde_le_motif(
        self, qualificateur, dossier
    ):
        reponse = qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite",
            json={"motif": "prix"},
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["etat"] == "SANS_SUITE"

        complet = qualificateur.get(f"/acquisition/dossiers/{dossier}")
        assert complet.status_code == 200
        assert complet.json()["motif_cloture"] == "prix"
        assert complet.json()["clos_le"] is not None

    def test_un_dossier_classe_sort_de_la_file(self, qualificateur, dossier):
        """⚠️ **C'est la propriété qui justifie tout ce pas.**

        `ouverts` exclut les états terminaux, et `charge_par_responsable` avec.
        Tant que rien ne classait, un prospect muet pesait pour toujours sur le
        collaborateur qui l'avait reçu, et l'affectation, qui choisit le moins
        chargé, le punissait indéfiniment.
        """
        avant = qualificateur.get("/acquisition/dossiers").json()
        assert dossier in [d["reference"] for d in avant]

        qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite", json={"motif": "prix"}
        )

        apres = qualificateur.get("/acquisition/dossiers").json()
        assert dossier not in [d["reference"] for d in apres]

    def test_un_dossier_classe_ne_se_reclasse_pas_en_boucle(
        self, qualificateur, dossier
    ):
        """Rejouable, et le motif d'origine est conservé.

        ⚠️ Un classement rejoué par un lot ne doit pas réécrire la raison pour
        laquelle un humain avait classé.
        """
        qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite", json={"motif": "prix"}
        )
        second = qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite",
            json={"motif": "concurrent"},
        )
        assert second.status_code == 200, second.text

        complet = qualificateur.get(f"/acquisition/dossiers/{dossier}").json()
        assert complet["motif_cloture"] == "prix"

    def test_un_dossier_inconnu_rend_404_et_non_500(self, qualificateur):
        reponse = qualificateur.post(
            "/acquisition/dossiers/DOS-INEXISTANT/sans-suite",
            json={"motif": "prix"},
        )
        assert reponse.status_code == 404, reponse.text

    def test_un_dossier_endormi_remonte_avec_son_retard(self, qualificateur, dossier):
        """⚠️ **La contre-épreuve de `test_un_dossier_frais_ne_dort_pas`.**

        Un cas ne peut mesurer un garde que s'il existe une situation où le garde
        change quelque chose. Sans celui-ci, une route qui rendrait toujours une
        liste vide passerait les deux autres cas sans broncher.

        Le dossier est vieilli **dans le magasin**, et non par une horloge
        déplacée : c'est l'ancienneté du dossier qu'on veut éprouver, pas la
        capacité du test à changer l'heure du monde.
        """
        from datetime import timedelta

        from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
            dossiers_memoire,
        )
        from app.partage.horloge import maintenant

        depot = dossiers_memoire()
        vieux = depot.lire(dossier).model_copy(
            update={"depuis_le": maintenant() - timedelta(hours=30)}
        )
        depot.enregistrer(vieux)

        reponse = qualificateur.get("/acquisition/dossiers/en-souffrance")
        assert reponse.status_code == 200, reponse.text
        (ligne,) = [x for x in reponse.json() if x["reference"] == dossier]
        assert ligne["etat"] == "DEPOSEE"
        # 24 heures au référentiel, 30 d'immobilité : six heures de retard.
        assert ligne["delai_heures"] == 24
        assert ligne["immobile_depuis_heures"] == 30
        # ⚠️ Jamais signalé : la route calcule l'état réel, elle ne lit pas les
        # alertes déposées par la veille. Un ordonnanceur arrêté ne rend pas
        # cette liste fausse.
        assert ligne["signale_le"] is None

    def test_un_dossier_endormi_puis_classe_disparait_de_la_liste(
        self, qualificateur, dossier
    ):
        """Le geste qui répond à l'alerte la fait taire. C'est la boucle entière."""
        from datetime import timedelta

        from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
            dossiers_memoire,
        )
        from app.partage.horloge import maintenant

        depot = dossiers_memoire()
        depot.enregistrer(
            depot.lire(dossier).model_copy(
                update={"depuis_le": maintenant() - timedelta(hours=30)}
            )
        )
        assert qualificateur.get("/acquisition/dossiers/en-souffrance").json()

        qualificateur.post(
            f"/acquisition/dossiers/{dossier}/sans-suite",
            json={"motif": "jamais-rappele"},
        )

        assert qualificateur.get("/acquisition/dossiers/en-souffrance").json() == []


class TestPasserLaMainParLApi:
    """La même route désigne et **redésigne**, et c'est délibéré.

    ─────────────────────────────────────────────────────────────────────────────
    Un dossier `DÉPOSÉE` est affecté, un dossier `AFFECTÉE` est réaffecté.
    L'appelant demande la même chose dans les deux cas — « désigne qui doit
    s'occuper de ça » — et lui faire choisir la route selon un état qu'il devrait
    lire d'abord serait lui demander de connaître le domaine à notre place.

    ⚠️ Avant ce pas, la route levait un **409** sur un dossier déjà affecté :
    `affecter` n'est valide que depuis `DÉPOSÉE`. Le passage de main par un
    responsable de pôle, que l'en-tête du dossier commercial annonce depuis le
    premier jour, n'était joignable par aucune route.
    ─────────────────────────────────────────────────────────────────────────────
    """

    #: `s.onana` porte ADMINISTRATEUR, donc `AFFECTER_DOSSIER`. Un compte réel :
    #: la route doit prouver que quelqu'un, dans le cabinet, a réellement ce droit.
    AFFECTEUR = "s.onana@cga-brcg.cm"

    @pytest.fixture
    def affecteur(self, client):
        reponse = client.post(
            "/transverse/session",
            json={"courriel": self.AFFECTEUR, "mot_de_passe": MOT_DE_PASSE_DEMO},
        )
        assert reponse.status_code == 200, reponse.text
        return client

    @pytest.fixture
    def dossier(self, affecteur):
        assert affecteur.post(
            "/acquisition/demandes", json=_demande()
        ).status_code in (200, 201)
        return affecteur.get("/acquisition/dossiers").json()[0]["reference"]

    def test_la_premiere_designation_ne_consomme_aucune_reprise(
        self, affecteur, dossier
    ):
        """⚠️ La contre-épreuve de la suivante : sans elle, une route qui
        réaffecterait toujours passerait pour correcte."""
        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier}/affectation", json={}
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["affecte"] is True

        complet = affecteur.get(f"/acquisition/dossiers/{dossier}").json()
        assert complet["reaffectations"] == 0
        assert complet["responsables_passes"] == []

    def test_une_seconde_designation_passe_la_main_a_quelqu_un_d_autre(
        self, affecteur, dossier
    ):
        affecteur.post(f"/acquisition/dossiers/{dossier}/affectation", json={})
        premier = affecteur.get(f"/acquisition/dossiers/{dossier}").json()["responsable"]

        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier}/affectation", json={}
        )
        assert reponse.status_code == 200, reponse.text

        apres = affecteur.get(f"/acquisition/dossiers/{dossier}").json()
        assert apres["responsable"] != premier
        assert apres["reaffectations"] == 1
        assert apres["responsables_passes"] == [premier]

    def test_la_main_ne_revient_jamais_a_quelqu_un_qui_l_a_deja_eue(
        self, affecteur, dossier
    ):
        """⚠️ **Le défaut mesuré, et corrigé au domaine.**

        `reaffecter` n'écartait que le titulaire du moment, et la grille choisit le
        moins chargé : celui qui vient de rendre le dossier redevient aussitôt le
        moins chargé. Sur cinq collaborateurs équivalents, les trois reprises
        allaient à deux d'entre eux.

        Le jeu de démonstration n'en compte que deux pour ce service : la
        deuxième reprise doit donc échouer proprement plutôt que revenir au
        premier, en laissant deux tours intacts pour un arbitrage humain.
        """
        affecteur.post(f"/acquisition/dossiers/{dossier}/affectation", json={})
        affecteur.post(f"/acquisition/dossiers/{dossier}/affectation", json={})
        vus = set(affecteur.get(f"/acquisition/dossiers/{dossier}").json()[
            "responsables_passes"
        ])

        troisieme = affecteur.post(
            f"/acquisition/dossiers/{dossier}/affectation", json={}
        )
        assert troisieme.status_code == 200, troisieme.text
        # Aucun repreneur n'est une réponse, pas une erreur : 200 avec les
        # empêchements, et le dossier reste où il est.
        assert troisieme.json()["affecte"] is False
        assert troisieme.json()["empechements"] == []

        apres = affecteur.get(f"/acquisition/dossiers/{dossier}").json()
        assert apres["reaffectations"] == 1, (
            "un tour a été consommé pour rendre le dossier à quelqu'un qui "
            "l'avait déjà eu"
        )
        # Le dossier n'a pas bougé : ni de main, ni de mémoire.
        assert apres["responsable"] not in vus
        assert set(apres["responsables_passes"]) == vus

    def test_un_dossier_en_conversation_refuse_le_passage_de_main(
        self, affecteur, dossier
    ):
        """⚠️ La bascule porte sur l'**état**, et non sur la présence d'un
        responsable.

        Un dossier en conversation a un responsable et ne se réaffecte pas : la
        transition n'existe pas au graphe. Tester `responsable is not None` aurait
        laissé passer ce cas-là.

        L'état est posé directement dans le magasin, et non atteint en jouant une
        qualification : ce cas vérifie la bascule de la route, pas le parcours,
        et y mêler la qualification le ferait échouer un jour pour une raison
        étrangère à ce qu'il garde.
        """
        from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
            dossiers_memoire,
        )
        from app.partage.horloge import maintenant

        affecteur.post(f"/acquisition/dossiers/{dossier}/affectation", json={})
        depot = dossiers_memoire()
        depot.enregistrer(depot.lire(dossier).premier_contact(maintenant()))
        assert (
            affecteur.get(f"/acquisition/dossiers/{dossier}").json()["etat"]
            == "EN_CONVERSATION"
        )

        reponse = affecteur.post(
            f"/acquisition/dossiers/{dossier}/affectation", json={}
        )
        assert reponse.status_code == 409, reponse.text
        assert "EN_CONVERSATION" in reponse.json()["detail"]
