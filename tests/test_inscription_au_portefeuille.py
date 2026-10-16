"""L'inscription au portefeuille d'une entreprise existante, depuis son dossier payé
(16 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le dossier commercial préremplit l'inscription : nom, téléphone, groupe CNPS et numéro
  employeur répondus au questionnaire, sans seconde saisie ;
* seul un dossier payé s'inscrit, une seule fois, et jamais sous un NIU déjà présent ;
* le dossier créé est complet : régime et centre depuis la création, exercice en cours,
  groupe CNPS repris par la paie ;
* une création datée d'après aujourd'hui ou une clôture passée sont refusées ;
* seule `INSCRIRE_STATUT` inscrit (réviseur, direction).
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_paie_au_catalogue import REPONSES, RESPONSABLE, _dossier_affecte

pytestmark = exige_postgresql

REVISEUR = "a.bouba@cga-brcg.cm"
NIU = "M099988877766Q"
DEMANDE = {
    "niu": NIU,
    "denomination": "BRIQUETERIE DU WOURI SARL",
    "forme_juridique": "SARL",
    "date_creation": "2019-05-06",
    "regime": "REEL",
    "centre": "CIME",
    "cloture_exercice_en_cours": "2026-12-31",
    "rccm": "RC/DLA/2019/B/1234",
    "groupe_risque_cnps": "B",
    "groupe_risque_notifie_le": "2024-02-01",
    "numero_cnps_employeur": "0912345678",
    "justification": "Carte de contribuable, RCCM et avis d'imposition au réel vus le 16/10.",
}


def _dossier_paye(client, telephone: str) -> str:
    """Un dossier PAIE qualifié par l'API, puis marqué payé en base : la proforma et le
    règlement ont leurs propres tests, ici seul compte l'état atteint."""
    from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
    from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, telephone)
    qualification = client.post(
        f"/acquisition/dossiers/{reference}/qualification",
        json={"reponses": [{"code": c, "valeur": v} for c, v in REPONSES]},
    )
    assert qualification.status_code == 200, qualification.text
    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
        depot = DepotDossiersSql(boutique.session, "CGA-BRCG")
        depot.enregistrer(depot.lire(reference).model_copy(update={"etat": EtatDossier.PAYEE}))
    client.cookies.clear()
    return reference


def test_le_dossier_paye_preremplit_puis_s_inscrit_et_la_paie_reprend_le_groupe(plateforme):
    client = plateforme
    reference = _dossier_paye(client, "699331100")
    ouvrir_une_session(client, REVISEUR)

    attente = client.get("/acquisition/inscriptions/en-attente")
    assert attente.status_code == 200, attente.text
    assert reference in {e["reference"] for e in attente.json()}
    etat = client.get(f"/acquisition/dossiers/{reference}/inscription").json()
    assert etat["inscriptible"] is True
    assert etat["proposition"]["denomination"] == "Briqueterie du Wouri"
    assert etat["proposition"]["groupe_risque_cnps"] == "B"
    assert etat["proposition"]["numero_cnps_employeur"] == "0912345678"

    inscrit = client.post(f"/acquisition/dossiers/{reference}/inscription", json=DEMANDE)
    assert inscrit.status_code == 201, inscrit.text
    assert (inscrit.json()["inscrit_sous"], inscrit.json()["inscriptible"]) == (NIU, False)

    groupe = client.get(f"/social/dossiers/{NIU}/groupe-risque").json()
    assert groupe == {"groupe": "B", "notifie_le": "2024-02-01"}
    dossier = client.get(f"/portefeuille/entreprises/{NIU}", params={"a_la_date": "2026-10-16"})
    assert dossier.status_code == 200, dossier.text
    corps = dossier.json()
    assert corps["denomination"] == "BRIQUETERIE DU WOURI SARL"

    # Sortie de la liste d'attente, où elle figurait avant l'inscription.
    attente = client.get("/acquisition/inscriptions/en-attente").json()
    assert reference not in {e["reference"] for e in attente}

    # Une fois, pas deux.
    rejeu = client.post(f"/acquisition/dossiers/{reference}/inscription", json=DEMANDE)
    assert rejeu.status_code == 409


def test_ni_dossier_non_paye_ni_niu_deja_present(plateforme):
    client = plateforme
    ouvrir_une_session(client, RESPONSABLE)
    reference = _dossier_affecte(client, "699331101")
    client.cookies.clear()
    ouvrir_une_session(client, REVISEUR)
    assert (
        client.get(f"/acquisition/dossiers/{reference}/inscription").json()["inscriptible"] is False
    )
    assert (
        client.post(f"/acquisition/dossiers/{reference}/inscription", json=DEMANDE).status_code
        == 409
    )

    client.cookies.clear()
    paye = _dossier_paye(client, "699331102")
    ouvrir_une_session(client, REVISEUR)
    deja = client.post(
        f"/acquisition/dossiers/{paye}/inscription", json=DEMANDE | {"niu": "M081234567890P"}
    )
    assert deja.status_code == 409 and "BATIMENT" in deja.json()["detail"]


def test_des_dates_incoherentes_sont_refusees(plateforme):
    client = plateforme
    reference = _dossier_paye(client, "699331103")
    ouvrir_une_session(client, REVISEUR)
    url = f"/acquisition/dossiers/{reference}/inscription"
    futur = client.post(url, json=DEMANDE | {"date_creation": "2099-01-01"})
    assert futur.status_code == 422 and "après aujourd'hui" in futur.json()["detail"]
    assert (
        client.post(url, json=DEMANDE | {"cloture_exercice_en_cours": "2025-12-31"}).status_code
        == 422
    )
    assert client.post(url, json=DEMANDE | {"groupe_risque_notifie_le": None}).status_code == 422
    assert client.get(url).json()["inscriptible"] is True


def test_seul_qui_inscrit_les_statuts_inscrit(plateforme):
    client = plateforme
    reference = _dossier_paye(client, "699331104")
    ouvrir_une_session(client, "l.fotso@cga-brcg.cm")  # comptable
    assert (
        client.post(f"/acquisition/dossiers/{reference}/inscription", json=DEMANDE).status_code
        == 403
    )
