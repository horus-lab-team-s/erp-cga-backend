"""La chaîne entière, par les routes : de la société créée à sa DSF (16 octobre 2026).

⚠️ CE QUI EST GARDÉ : les contextes se passent la donnée sans ressaisie.
* la création livrée devient un dossier du portefeuille, né à la date de son RCCM, avec
  son régime et son premier exercice ;
* la comptabilité tient ce dossier dès sa conversion : une écriture s'y saisit et s'y valide ;
* la liasse se tire de ces écritures, et l'impôt estimé avec elle ;
* l'échéancier fiscal du dossier existe, et les pièces de la DSF se suivent.
Chaque maillon a ses tests ; celui-ci vérifie qu'ils tiennent ensemble.
"""

from __future__ import annotations

from datetime import date

from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CREATION = "CR-2026-0015"
NIU = "M112233445566T"


def test_de_la_creation_livree_a_la_liasse_et_aux_pieces_de_la_dsf(plateforme):
    client = plateforme

    # 1. La création livrée entre au portefeuille.
    ouvrir_une_session(client, "p.moukouri@cga-brcg.cm")  # chargée des formalités
    conversion = client.post(
        f"/creations/{CREATION}/conversion",
        json={"regime": "REEL", "centre": "CDI", "adherent": True},
    )
    assert conversion.status_code == 200, conversion.text
    assert conversion.json()["niu"] == NIU
    client.cookies.clear()

    ouvrir_une_session(client, "a.bouba@cga-brcg.cm")  # réviseur, tout le cabinet
    dossier = client.get(f"/portefeuille/entreprises/{NIU}", params={"a_la_date": "2026-10-16"})
    assert dossier.status_code == 200, dossier.text

    # 2. La comptabilité le tient : une facture de vente saisie et validée.
    saisie = client.post(
        f"/comptabilite/dossiers/{NIU}/ecritures",
        json={
            "journal": "VE",
            "exercice": "2026",
            "date_operation": "2026-09-30",
            "libelle": "Prestations de transit, septembre",
            "piece_justificative": "FV-2026-09",
            "lignes": [
                {"compte": "411", "libelle": "Clients", "sens": "DEBIT", "montant": "23850000"},
                {"compte": "706", "libelle": "Transit", "sens": "CREDIT", "montant": "20000000"},
                {
                    "compte": "4431",
                    "libelle": "TVA facturée",
                    "sens": "CREDIT",
                    "montant": "3850000",
                },
            ],
        },
    )
    assert saisie.status_code == 201, saisie.text
    numero = saisie.json()["numero"]
    validation = client.post(f"/comptabilite/dossiers/{NIU}/ecritures/2026/VE/{numero}/validation")
    assert validation.status_code == 200, validation.text

    # 3. La liasse s'en tire, et l'impôt estimé avec elle.
    liasse = client.get(f"/cloture/dossiers/{NIU}/liasse/2026")
    assert liasse.status_code == 200, liasse.text
    impot = liasse.json()["impot"]
    assert impot["estime"] is True and impot["retenu"] in ("TAUX", "MINIMUM")
    assert impot["base_du_minimum"] == "20000000"

    # 4. L'échéancier fiscal du dossier existe dès sa naissance.
    echeancier = client.get(
        f"/obligations/dossiers/{NIU}/echeancier",
        params={"exercice": "2026", "a_la_date": "2026-10-16"},
    )
    assert echeancier.status_code == 200, echeancier.text
    codes = {o["obligation"]["code_obligation"] for o in echeancier.json()}
    assert "DSF" in codes

    # 5. Les pièces de la DSF se suivent déjà, celles que la plateforme produit comprises.
    pieces = client.get(f"/cloture/dossiers/{NIU}/dsf/2026/pieces-jointes")
    assert pieces.status_code == 200, pieces.text
    etats = {p["numero"]: p["etat"] for p in pieces.json()["pieces"]}
    assert etats[3] == "PRODUITE" and etats[1] == "A_FOURNIR"
    assert date.fromisoformat(dossier.json()["date_creation"]) == date(2026, 5, 22)
