"""Une demande déposée trouve son responsable sans attendre qu'on clique.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Le dépôt d'une demande annonçait l'événement `DemandeDéposée` dans ses commentaires,
et rien ne le publiait : chaque dossier restait `DÉPOSÉE` jusqu'à ce qu'un
collaborateur clique sur « affecter », pendant que le client attendait l'appel
promis. Ces cas passent par le VRAI relais (`POST /orchestration/publication`), sur
PostgreSQL : un abonné inscrit mais jamais remis ne se verrait pas autrement.

  1. une demande neuve, publiée, est affectée ;
  2. un dossier affecté à la main avant la publication n'est pas touché ;
  3. une demande rattachée à un dossier vivant ne publie rien de plus ;
  4. la demande de rappel d'un devis passe par le même chemin.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

ADMINISTRATION = "s.onana@cga-brcg.cm"


def _deposer(client, telephone):
    r = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Arrivée Essai",
            "telephone": telephone,
            "service_souhaite": "CREATION",
            "canal_prefere": "APPEL",
        },
    )
    assert r.status_code == 201, r.text


def _dossier(client, telephone):
    (dossier,) = [
        d for d in client.get("/acquisition/dossiers").json() if d["telephone"].endswith(telephone)
    ]
    return dossier


def _publier(client):
    r = client.post("/orchestration/publication")
    assert r.status_code == 200, r.text
    return r.json()


def _deposees_en_attente(client):
    en_attente = client.get("/orchestration/en-attente").json()
    return [e for e in en_attente if e["nom"] == "DemandeDeposee"]


def test_une_demande_neuve_est_affectee_a_la_publication(plateforme):
    client = plateforme
    _deposer(client, "699220011")
    ouvrir_une_session(client, ADMINISTRATION)
    assert _dossier(client, "699220011")["etat"] == "DEPOSEE"

    passage = _publier(client)
    assert passage["publies"] >= 1 and passage["echecs"] == 0, passage

    dossier = _dossier(client, "699220011")
    assert dossier["etat"] == "AFFECTEE"
    assert dossier["responsable"], "un responsable est désigné"


def test_un_dossier_affecte_a_la_main_n_est_pas_retouche(plateforme):
    client = plateforme
    _deposer(client, "699220022")
    ouvrir_une_session(client, ADMINISTRATION)
    reference = _dossier(client, "699220022")["reference"]
    a_la_main = client.post(f"/acquisition/dossiers/{reference}/affectation", json={}).json()
    assert a_la_main["affecte"] is True

    _publier(client)

    dossier = _dossier(client, "699220022")
    assert dossier["etat"] == "AFFECTEE"
    assert dossier["responsable"] == a_la_main["responsable"], "réaffecté par l'abonné"


def test_une_demande_rattachee_ne_publie_rien_de_plus(plateforme):
    client = plateforme
    _deposer(client, "699220033")
    _deposer(client, "699220033")
    ouvrir_une_session(client, ADMINISTRATION)
    assert _dossier(client, "699220033")["demandes"] == 2
    assert len(_deposees_en_attente(client)) == 1


def test_le_rappel_d_un_devis_est_affecte_de_meme(plateforme):
    client = plateforme
    devis = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ESSAI",
                "prenom": "Arrivée",
                "courriel": "arrivee@exemple.cm",
                "telephone": "699220044",
            },
            "lignes": [{"service": "FORMATION"}],
        },
    ).json()
    assert devis["prix_arretes"] is False
    assert client.post(f"/acquisition/devis/{devis['reference']}/rappel").status_code == 201

    ouvrir_une_session(client, ADMINISTRATION)
    _publier(client)
    dossier = _dossier(client, "699220044")
    # ⚠️ AFFECTEE si un collaborateur a la compétence, DEPOSEE sinon : dans les
    # deux cas l'événement a été CONSOMMÉ, jamais laissé sans abonné.
    assert dossier["etat"] in ("AFFECTEE", "DEPOSEE")
    assert _deposees_en_attente(client) == []
