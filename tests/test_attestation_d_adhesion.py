"""L'attestation d'adhésion au Centre (11 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

L'adhésion ouvre les avantages de l'article 119 du CGI ; le cabinet n'avait rien à
remettre à l'adhérent pour les faire valoir. L'attestation :

  1. se délivre au cabinet et à l'adhérent, pour son seul dossier ;
  2. porte le numéro d'adhésion et sa date d'effet ;
  3. ne se délivre pas à une entreprise dont l'adhésion n'est plus en cours ;
  4. passe au journal d'audit avec sa référence et son empreinte.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import timedelta

from app.contextes.transverse.adaptateurs.entrant.dependances import unite_de_travail
from app.partage.horloge import maintenant
from app.partage.locataire import etabli
from tests.conftest import ouvrir_une_session

BATIMENT = "M081234567890P"
AGRO = "M065544332211L"
URL = "/souscription/dossiers/{niu}/attestation-d-adhesion"


class TestLaDelivrance:
    def test_le_cabinet_la_tire_en_pdf(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        reponse = client.get(URL.format(niu=BATIMENT))
        assert reponse.status_code == 200, reponse.text
        assert reponse.content[:4] == b"%PDF"
        assert reponse.headers["content-type"] == "application/pdf"

    def test_l_adherent_tire_la_sienne(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        assert client.get(URL.format(niu=BATIMENT)).status_code == 200

    def test_l_adherent_ne_tire_pas_celle_d_un_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        assert client.get(URL.format(niu=AGRO)).status_code in (403, 404)

    def test_elle_passe_au_journal_avec_sa_reference(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "b.mballa@cga-brcg.cm")
        client.get(URL.format(niu=BATIMENT))
        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            traces = [
                e for e in boutique.journal.lister() if e.action == "adhesion.attestation_delivree"
            ]
        assert traces, "aucune délivrance au journal"
        assert traces[-1].apres["reference"].startswith("ATT-")
        assert traces[-1].apres["numero_d_adhesion"]


class TestUneAdhesionTerminee:
    def test_aucune_attestation_ne_se_delivre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "a.bouba@cga-brcg.cm")
        resiliation = client.post(
            f"/portefeuille/entreprises/{AGRO}/adhesions/resiliation",
            json={
                "au": (maintenant().date() - timedelta(days=1)).isoformat(),
                "justification": "Résiliation de l'adhésion à la demande écrite du dirigeant.",
            },
        )
        assert resiliation.status_code == 200, resiliation.text
        reponse = client.get(URL.format(niu=AGRO))
        assert reponse.status_code == 409
        assert "n'est pas adhérente" in reponse.json()["detail"]
