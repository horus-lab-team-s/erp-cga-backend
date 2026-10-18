"""Une demande ponctuelle, de la vitrine à sa mission (18 octobre 2026, point 22).

⚠️ CE QUI EST GARDÉ
* payée, la demande se présente dans la file « à ouvrir », sans recopier sa référence ;
* une demande, une mission : la seconde ouverture est refusée, et la file se vide ;
* le client apprend la prise en charge par courriel, sans nom de juriste ;
* la mission se confie à un autre juriste, motif à l'appui ; jamais à qui n'instruit pas.
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

DIRECTRICE = "b.mballa@cga-brcg.cm"
JURISTE = "p.moukouri@cga-brcg.cm"  # C-007, instruit les missions
COURRIEL = "contact@briqueterie-wouri.cm"


def _demande_payee(client) -> str:
    from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotDossiersSql
    from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
    from app.contextes.transverse.api import unite_de_travail
    from app.partage.locataire import etabli

    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Briqueterie du Wouri",
            "telephone": "699441122",
            "courriel": COURRIEL,
            "service_souhaite": "PONCTUEL",
            "canal_prefere": "APPEL",
            "message": "Relire notre bail commercial avant signature.",
        },
    )
    assert depot.status_code in (200, 201), depot.text
    ouvrir_une_session(client, DIRECTRICE)
    (dossier,) = [
        d
        for d in client.get("/acquisition/dossiers").json()
        if d["etat"] == "DEPOSEE" and d["service"] == "PONCTUEL"
    ]
    reference = dossier["reference"]
    with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
        depots = DepotDossiersSql(boutique.session, "CGA-BRCG")
        depots.enregistrer(depots.lire(reference).model_copy(update={"etat": EtatDossier.PAYEE}))
    client.cookies.clear()
    return reference


def test_de_la_demande_payee_a_sa_mission(plateforme):
    from app.contextes.transverse.api import service_de_notification

    client = plateforme
    reference = _demande_payee(client)
    ouvrir_une_session(client, JURISTE)

    file = client.get("/missions/a-ouvrir").json()
    assert reference in [d["reference"] for d in file]
    ouverte = client.post(
        "/missions",
        json={
            "type": "ANALYSE_CONTRAT",
            "objet": "Bail commercial",
            "reference_commerciale": reference,
        },
    )
    assert ouverte.status_code == 201, ouverte.text
    assert ouverte.json()["client_courriel"] == COURRIEL

    seconde = client.post(
        "/missions",
        json={"type": "ANALYSE_CONTRAT", "objet": "Encore", "reference_commerciale": reference},
    )
    assert seconde.status_code == 409 and ouverte.json()["identifiant"] in seconde.text
    assert reference not in [d["reference"] for d in client.get("/missions/a-ouvrir").json()]

    ouvertes = service_de_notification().derniers("mission.ouverte")
    avis = [a for a in ouvertes if a.destinataire == COURRIEL]
    assert avis and avis[0].contexte["reference"] == reference


def test_la_mission_se_confie_a_un_autre_juriste(plateforme):
    client = plateforme
    reference = _demande_payee(client)
    ouvrir_une_session(client, JURISTE)
    mission = client.post(
        "/missions",
        json={"type": "ANALYSE_CONTRAT", "objet": "Bail", "reference_commerciale": reference},
    ).json()["identifiant"]

    client.cookies.clear()
    ouvrir_une_session(client, DIRECTRICE)
    candidats = {c["compte"] for c in client.get(f"/missions/{mission}/candidats").json()}
    assert "C-007" in candidats and "C-004" not in candidats  # le comptable n'instruit pas

    refus = client.post(
        f"/missions/{mission}/reaffectation",
        json={"responsable": "C-004", "motif": "absence de la juriste"},
    )
    assert refus.status_code == 422, refus.text
    autre = next(c for c in candidats if c != "C-007")
    confiee = client.post(
        f"/missions/{mission}/reaffectation",
        json={"responsable": autre, "motif": "absence de la juriste cette semaine"},
    )
    assert confiee.status_code == 200, confiee.text
    assert confiee.json()["responsable"] == autre

    client.cookies.clear()
    ouvrir_une_session(client, JURISTE)
    assert client.get(f"/missions/{mission}/candidats").status_code == 403
