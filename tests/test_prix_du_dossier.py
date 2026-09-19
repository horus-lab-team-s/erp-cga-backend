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
]


def _dossier_affecte(client, telephone):
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Station Bonabéri", "telephone": telephone, "service_souhaite": "creation-sarl",
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
