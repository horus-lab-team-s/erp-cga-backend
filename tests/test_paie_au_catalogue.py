"""La paie déléguée, de la demande du site jusqu'au prix.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le cabinet vend trois familles de prestations à côté de la création et du suivi :
la paie, la domiciliation, la formation. Seules les deux premières familles
avaient un code au catalogue **et** de quoi aller jusqu'au bout ; la paie était
calculée par le contexte Social depuis longtemps, sans qu'aucun client puisse la
commander.

⚠️ **UN CODE AU CATALOGUE NE SUFFIT PAS.** Un service qui entre par la porte sans
questionnaire produit un dossier **sans issue** : il se dépose, il s'affecte, et
il ne se qualifie ni ne se chiffre jamais. Il ne se voit pas pour autant, il
ressemble à un dossier qui attend. C'est exactement ce que
`_exiger_un_service_du_catalogue` a été écrit pour empêcher, et le lui interdire
à l'entrée ne servirait à rien si le référentiel ne suivait pas derrière.

Ce fichier vérifie donc la chaîne entière sur une vraie base : la porte accepte
le code, le questionnaire existe, la qualification s'enregistre, et le prix sort
du barème de la paie et non d'un autre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

RESPONSABLE = "b.mballa@cga-brcg.cm"

#: Les sept réponses du questionnaire `PAIE`. Toutes les obligatoires y sont :
#: un questionnaire incomplet rend 409 et ne chiffrerait rien.
REPONSES = [
    ("salaries_a_traiter", 12),
    ("numero_employeur_cnps", "0912345678"),
    ("groupe_risque_cnps", "B"),
    ("avantages_en_nature", "LOGEMENT"),
    ("elements_variables", "SYSTEMATIQUES"),
    ("reprise_historique", 0),
    ("premiere_paie_souhaitee", "2026-11-01"),
]


def _dossier_affecte(client, telephone: str) -> str:
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Briqueterie du Wouri",
            "telephone": telephone,
            "service_souhaite": "PAIE",
            "canal_prefere": "APPEL",
            "message": "je voudrais confier la paie de mes douze salariés",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["etat"] == "DEPOSEE"
    ]
    affecte = client.post(f"/acquisition/dossiers/{dossier['reference']}/affectation", json={})
    assert affecte.json()["affecte"] is True, affecte.text
    return dossier["reference"]


class TestLaPaieTraverseToutLeParcours:
    def test_la_porte_accepte_le_code_et_le_dossier_se_chiffre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, RESPONSABLE)

        reference = _dossier_affecte(client, "699221100")

        # Le questionnaire existe : sans lui, la qualification rendrait 404 en
        # nommant le service introuvable. Il se demande par le SERVICE et non par
        # le dossier, parce qu'il est le même pour tous les dossiers d'un service.
        questionnaire = client.get("/acquisition/questionnaires/PAIE")
        assert questionnaire.status_code == 200, questionnaire.text
        codes = {q["code"] for q in questionnaire.json()["questions"]}
        assert "salaries_a_traiter" in codes
        assert "groupe_risque_cnps" in codes

        qualification = client.post(
            f"/acquisition/dossiers/{reference}/qualification",
            json={"reponses": [{"code": c, "valeur": v} for c, v in REPONSES]},
        )
        assert qualification.status_code == 200, qualification.text

        chiffrage = client.post(
            f"/acquisition/dossiers/{reference}/chiffrage", json={"debours": []}
        )
        assert chiffrage.status_code == 200, chiffrage.text
        proposition = chiffrage.json()

        # ⚠️ Le prix vient du barème de la PAIE, pas d'un autre. Sans cette
        # assertion, un barème manquant rabattu sur un service voisin passerait :
        # le dossier se chiffrerait, et au prix de la tenue comptable.
        assert Decimal(str(proposition["reference"])) == Decimal("60000"), proposition
        assert Decimal(str(proposition["plancher"])) == Decimal("45000"), proposition
        assert Decimal(str(proposition["plafond"])) == Decimal("120000"), proposition

    def test_un_service_absent_du_catalogue_est_toujours_refuse(self, plateforme):
        """La contre-épreuve : la porte n'a pas été ouverte à tout le monde.

        ⚠️ Sans ce cas, ajouter la paie en supprimant la garde ferait passer le
        premier cas, et la porte du commerce serait rouverte sans que rien ne le
        dise.
        """
        client = plateforme
        refus = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Visiteur du site",
                "telephone": "699221101",
                # Le site parle d'assistance juridique ; elle n'a aucun code.
                "service_souhaite": "JURIDIQUE",
                "canal_prefere": "APPEL",
                "message": "depuis le site",
            },
        )
        assert refus.status_code == 422, refus.text
        detail = str(refus.json()["detail"])
        assert "JURIDIQUE" in detail, "le refus ne dit pas ce qui a été reçu"
        assert "PAIE" in detail, "le refus ne dit pas que la paie est désormais admise"


class TestLeCatalogueEtLeReferentielNeDiventPasSeparement:
    """⚠️ La garde qui aurait vu le trou, et qui vaut pour les services à venir.

    Domiciliation, formation et prestation ponctuelle sont au catalogue depuis le
    début **sans questionnaire ni barème** : elles se déposent et ne se chiffrent
    pas. Ce cas ne l'interdit pas, parce que c'est l'état voulu aujourd'hui pour
    trois services qui se traitent à la main. Il exige seulement que la liste des
    services chiffrables soit **écrite ici**, pour qu'ajouter un code au
    catalogue oblige à dire dans quel camp il tombe.
    """

    #: Les services qu'un client peut commander et voir chiffrer tout seuls.
    CHIFFRABLES = {"CREATION", "ADHESION", "PAIE"}

    def test_chaque_service_chiffrable_a_son_questionnaire_et_son_bareme(self):
        import yaml

        from app.contextes.souscription.api import CATALOGUE
        from app.infrastructure.config import configuration

        dossier = configuration().dossier_referentiel
        questionnaires = {
            yaml.safe_load(f.read_text(encoding="utf-8"))["service"]
            for f in (dossier / "qualification").glob("*.yaml")
        }
        baremes = {
            b["service"]
            for b in yaml.safe_load(
                (dossier / "tarification" / "baremes.yaml").read_text(encoding="utf-8")
            )["baremes"]
        }

        codes = {s.code for s in CATALOGUE}
        assert self.CHIFFRABLES <= codes, (
            f"services chiffrables absents du catalogue : {sorted(self.CHIFFRABLES - codes)}"
        )
        assert self.CHIFFRABLES <= questionnaires, (
            "un service chiffrable sans questionnaire produit un dossier sans issue : "
            f"{sorted(self.CHIFFRABLES - questionnaires)}"
        )
        assert self.CHIFFRABLES <= baremes, (
            "un service chiffrable sans barème se qualifie puis rend 404 au chiffrage : "
            f"{sorted(self.CHIFFRABLES - baremes)}"
        )

    def test_aucun_questionnaire_ni_bareme_ne_vise_un_service_inconnu(self):
        """L'autre sens : un référentiel qui parle d'un service disparu.

        C'est ce qui s'est produit le 27 septembre, quand les questionnaires
        portaient `creation-sarl` et le catalogue `CREATION` : le chiffrage
        tombait en 500 au lieu de nommer le service introuvable.
        """
        import yaml

        from app.contextes.souscription.api import CATALOGUE
        from app.infrastructure.config import configuration

        dossier = configuration().dossier_referentiel
        codes = {s.code for s in CATALOGUE}
        questionnaires = {
            yaml.safe_load(f.read_text(encoding="utf-8"))["service"]
            for f in (dossier / "qualification").glob("*.yaml")
        }
        baremes = {
            b["service"]
            for b in yaml.safe_load(
                (dossier / "tarification" / "baremes.yaml").read_text(encoding="utf-8")
            )["baremes"]
        }
        assert questionnaires <= codes, (
            f"questionnaires visant un service hors catalogue : {sorted(questionnaires - codes)}"
        )
        assert baremes <= codes, (
            f"barèmes visant un service hors catalogue : {sorted(baremes - codes)}"
        )
