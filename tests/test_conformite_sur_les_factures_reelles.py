"""La conformité contrôle les factures réelles, et jamais le jeu fictif en production.

─────────────────────────────────────────────────────────────────────────────────
CONSTATÉ À L'AUDIT AVANT LA MISE EN PRODUCTION, LE 20 OCTOBRE 2026

La boîte de réception, la file des anomalies, les relances, l'essai d'une règle et
l'écart en masse lisaient le seul jeu de démonstration, en production comme ailleurs :
la direction y aurait vu des factures inventées, et les pièces réelles, lues et
confirmées, aucun verdict.

⚠️ CE QUI EST GARDÉ

* une pièce lue et confirmée reçoit son verdict, sur les mêmes routes ;
* hors des environnements exposés, le jeu fictif s'y ajoute ; dans un environnement
  exposé, il n'y paraît jamais ;
* un numéro de facture partagé par deux dossiers rend celui que l'appelant voit ;
* une facture réelle d'un autre dossier reste introuvable.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.contextes.conformite.adaptateurs.sortant import factures_a_controler as source
from app.contextes.conformite.api import (
    FACTURES_DEMO,
    Document,
    FactureAControler,
    Montants,
    Partie,
    brancher_la_source_des_factures,
    facture_par_reference,
    factures_a_controler,
)

# ⚠️ Le MODULE : ses classes `Test…` importées ici seraient collectées deux fois.
from tests import test_facture_vers_ecriture as lecture
from tests.conftest import exige_postgresql, ouvrir_une_session


def _reelle(reference: str, dossier: str) -> FactureAControler:
    return FactureAControler(
        document=Document(reference=reference, date_emission=date(2026, 9, 12)),
        emetteur=Partie(denomination="QUINCAILLERIE DU WOURI", niu="M091234567890Q"),
        destinataire=Partie(niu=dossier),
        montants=Montants(
            total_ht=Decimal(150000), total_tva=Decimal(28875), total_ttc=Decimal(178875)
        ),
    )


@pytest.fixture
def exposee(monkeypatch):
    """Un environnement exposé : recette, préproduction ou production."""
    monkeypatch.setattr(source, "_avec_la_demonstration", lambda: False)


@pytest.fixture
def branchee():
    """Une source de factures réelles, débranchée après l'essai."""
    reelles: list[FactureAControler] = []
    brancher_la_source_des_factures(lambda: reelles)
    yield reelles
    from app.contextes.collecte.adaptateurs.entrant.factures_pour_la_conformite import (
        factures_des_pieces_lues,
    )

    brancher_la_source_des_factures(factures_des_pieces_lues)


class TestLaSource:
    def test_exposee_seules_les_factures_reelles(self, branchee, exposee):
        branchee.append(_reelle("FA-1", lecture.BATIMENT))
        assert [f.document.reference for f in factures_a_controler()] == ["FA-1"]

    def test_exposee_et_sans_piece_lue_rien_plutot_que_du_fictif(self, branchee, exposee):
        assert factures_a_controler() == []

    def test_hors_production_le_jeu_fictif_s_ajoute(self, branchee):
        branchee.append(_reelle("FA-1", lecture.BATIMENT))
        references = [f.document.reference for f in factures_a_controler()]
        assert references[0] == "FA-1"
        assert len(references) == 1 + len(FACTURES_DEMO)

    def test_une_reelle_remplace_la_fictive_de_meme_numero_et_de_meme_dossier(self, branchee):
        fictive = next(iter(FACTURES_DEMO.values()))
        branchee.append(_reelle(fictive.document.reference, fictive.destinataire.niu))
        meme = [
            f for f in factures_a_controler() if f.document.reference == fictive.document.reference
        ]
        assert len(meme) == 1 and meme[0].emetteur.denomination == "QUINCAILLERIE DU WOURI"

    def test_un_numero_partage_rend_le_dossier_que_l_on_voit(self, branchee, exposee):
        branchee.extend([_reelle("FA-1", lecture.COLOMBE), _reelle("FA-1", lecture.BATIMENT)])
        vue = facture_par_reference("FA-1", voit=lambda niu: niu == lecture.BATIMENT)
        assert vue is not None and vue.destinataire.niu == lecture.BATIMENT


@exige_postgresql
class TestSurLesRoutes:
    def test_la_piece_lue_recoit_son_verdict(self, plateforme):
        client = plateforme
        piece = lecture._piece(client)
        lue = client.post(f"/collecte/pieces/{piece}/facture", json=lecture.FACTURE)
        assert lue.status_code == 200, lue.text

        rapports = client.get("/conformite/demonstration/rapports")
        assert rapports.status_code == 200, rapports.text
        assert "FA-2026-0815" in {r["facture"]["document"]["reference"] for r in rapports.json()}
        unitaire = client.get("/conformite/demonstration/FA-2026-0815")
        assert unitaire.status_code == 200, unitaire.text
        assert unitaire.json()["facture"]["destinataire"]["niu"] == lecture.BATIMENT

    def test_exposee_la_boite_ne_montre_aucune_facture_fictive(self, plateforme, exposee):
        client = plateforme
        piece = lecture._piece(client)
        client.post(f"/collecte/pieces/{piece}/facture", json=lecture.FACTURE)

        references = {
            r["facture"]["document"]["reference"]
            for r in client.get("/conformite/demonstration/rapports").json()
        }
        assert references == {"FA-2026-0815"}

    def test_la_facture_d_un_autre_dossier_reste_introuvable(self, plateforme):
        client = plateforme
        piece = lecture._piece(client)
        client.post(f"/collecte/pieces/{piece}/facture", json=lecture.FACTURE)

        client.cookies.clear()
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        assert client.get("/conformite/demonstration/FA-2026-0815").status_code == 404
        assert "FA-2026-0815" not in {
            r["facture"]["document"]["reference"]
            for r in client.get("/conformite/demonstration/rapports").json()
        }
