"""La cliente d'une création retrouve sa proforma et son reçu, connectée (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* elle lit SES proformas et son reçu, sans le lien du courriel ;
* le reçu porte le montant ARRÊTÉ de la proforma, et n'existe qu'une fois réglé ;
* la proforma ou le reçu d'un autre dossier rend 404 ;
* un collaborateur sans `SUIVRE_MA_CREATION` n'emprunte pas ces routes.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from tests import test_compte_de_la_creation as compte
from tests import test_recette_du_parcours as recette
from tests import test_recu_honoraires as recu_texte
from tests.conftest import exige_postgresql, ouvrir_une_session

pytestmark = exige_postgresql

CAISSIERE = "n.essama@cga-brcg.cm"


def _vente(client, *, reglee: bool) -> tuple[str, str]:
    """Une création acceptée, son PDF rangé au magasin comme l'émission le range."""
    from app.contextes.souscription.adaptateurs.entrant.routes_proforma import (
        magasin_des_proformas,
    )
    from app.contextes.souscription.domaine.proforma import empreinte

    marque = uuid4().hex[:8]
    reference, numero = recette.TestLeReglementNotifieParLOperateur()._dossier_accepte(
        client, reference=f"dos-{marque}", numero=f"PRO-2026-{marque}"
    )
    from app.partage.locataire import etabli

    with etabli("CGA-BRCG"):
        magasin_des_proformas().deposer(
            empreinte(b"%PDF recette"), b"%PDF recette", type_mime="application/pdf"
        )
    if reglee:
        client.cookies.clear()
        ouvrir_une_session(client, CAISSIERE)
        constat = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"reference_externe": "Reçu de caisse n° 2026-0412"},
        )
        assert constat.status_code == 200, constat.text
    client.cookies.clear()
    return reference, numero


def _sa_session(client, reference: str) -> None:
    compte._connectee(client, f"CRE-{reference}", f"cliente.{uuid4().hex[:6]}@exemple.cm")


class TestElleRetrouveSesDocuments:
    def test_sa_proforma_et_son_recu(self, plateforme):
        client = plateforme
        reference, numero = _vente(client, reglee=True)
        _sa_session(client, reference)

        (vue,) = client.get("/mes-documents/creation").json()
        assert vue["dossier"] == reference and vue["reglee"] is True
        assert [p["numero"] for p in vue["proformas"]] == [numero]
        assert Decimal(vue["proformas"][0]["total"]) == Decimal("250000")

        proforma = client.get(f"/mes-documents/creation/proformas/{numero}/document")
        assert proforma.status_code == 200, proforma.text
        assert proforma.content.startswith(b"%PDF")

        recu = client.get(f"/mes-documents/creation/{reference}/recu")
        assert recu.status_code == 200, recu.text
        assert recu.headers["content-type"] == "application/pdf"
        texte = recu_texte._texte(recu.content)
        assert "REÇU DE RÈGLEMENT" in texte and f"N° R-{numero}" in texte
        assert "250 000" in texte and "au guichet du cabinet" in texte

    def test_pas_de_recu_avant_le_reglement(self, plateforme):
        client = plateforme
        reference, _ = _vente(client, reglee=False)
        _sa_session(client, reference)

        assert client.get(f"/mes-documents/creation/{reference}/recu").status_code == 409


class TestRienDUneAutre:
    def test_la_proforma_et_le_recu_d_un_autre_dossier_n_existent_pas(self, plateforme):
        client = plateforme
        autre, numero_autre = _vente(client, reglee=True)
        sien, _ = _vente(client, reglee=True)
        _sa_session(client, sien)

        assert (
            client.get(f"/mes-documents/creation/proformas/{numero_autre}/document").status_code
            == 404
        )
        assert client.get(f"/mes-documents/creation/{autre}/recu").status_code == 404
        assert [v["dossier"] for v in client.get("/mes-documents/creation").json()] == [sien]

    def test_une_proforma_annulee_ne_s_ouvre_pas(self, plateforme):
        """Revue du 15 octobre 2026 : le document suivait le dossier, pas l'état."""
        from app.contextes.souscription.adaptateurs.sortant.depots_sql import DepotProformasSql
        from app.contextes.souscription.domaine.proforma import EtatProforma
        from app.contextes.transverse.api import unite_de_travail
        from app.partage.locataire import etabli

        client = plateforme
        reference, numero = _vente(client, reglee=False)
        with etabli("CGA-BRCG"), unite_de_travail("CGA-BRCG") as boutique:
            depot = DepotProformasSql(boutique.session, "CGA-BRCG")
            depot.enregistrer(depot.lire(numero).model_copy(update={"etat": EtatProforma.ANNULEE}))
        _sa_session(client, reference)

        assert client.get(f"/mes-documents/creation/proformas/{numero}/document").status_code == 404
        (vue,) = client.get("/mes-documents/creation").json()
        assert vue["proformas"] == []

    def test_un_collaborateur_n_emprunte_pas_ces_routes(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, CAISSIERE)
        assert client.get("/mes-documents/creation").status_code == 403
