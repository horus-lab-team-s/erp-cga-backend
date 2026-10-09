"""Le prix est une décision du gérant.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CES CAS TIENNENT

Le catalogue public portait des montants recopiés de la maquette de la vitrine,
affichés comme fermes et **encaissés en ligne**, sans qu'aucun responsable du
cabinet ne les ait arrêtés. Ils n'étaient pas sur la fiche de contreseing : ils
échappaient à toute validation humaine.

Désormais :

* un tarif est `A_VALIDER` tant que la direction ne l'a pas fixé ;
* un tarif à valider **ne se paie pas** : le devis s'établit, l'engagement est
  refusé et dit pourquoi ;
* seule la direction fixe un prix, à partir d'aujourd'hui ou d'une date à venir,
  jamais dans le passé ;
* chaque décision garde son auteur, son instant et son motif, et le compte du
  gérant ne sort pas sur la route publique.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant.routes_http import (
    reinitialiser_comptoir,
)
from app.contextes.souscription.api import (
    CATALOGUE,
    FixationRefusee,
    StatutTarif,
    TarifFixe,
    appliquer_les_fixations,
    fixer,
    service_par_code,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

INSTANT = datetime(2026, 9, 21, 10, 0)
AUJOURD_HUI = INSTANT.date()


def _decision(**autres) -> TarifFixe:
    base = dict(
        identifiant="TF-1",
        service="DOMICILIATION",
        formule=None,
        montant=Decimal(195000),
        a_partir_du=date(2026, 10, 1),
        motif="Révision du loyer des bureaux de Douala.",
        fixe_par="C-001",
        fixe_le=INSTANT,
    )
    base.update(autres)
    return TarifFixe(**base)


# ── Le domaine ──────────────────────────────────────────────────────────────


class TestLeCatalogueSansDecision:
    def test_tous_les_prix_sont_a_valider(self):
        # L'état réel d'aujourd'hui : personne au cabinet n'a arrêté ces montants.
        for service in CATALOGUE:
            tarifs = [t for f in service.formules for t in f.tarifs] or service.tarifs
            assert all(t.statut is StatutTarif.A_VALIDER for t in tarifs), service.code


class TestFixer:
    def test_ouvre_une_periode_sans_reecrire_la_precedente(self):
        domiciliation = service_par_code(CATALOGUE, "DOMICILIATION")
        apres = fixer(domiciliation, _decision())

        # ⚠️ Le 30 septembre reste au prix d'avant, À VALIDER : un devis émis
        # avant la décision ne change pas de prix.
        avant = apres.tarif_au(date(2026, 9, 30))
        assert avant.montant == Decimal(180000)
        assert avant.statut is StatutTarif.A_VALIDER

        nouveau = apres.tarif_au(date(2026, 10, 1))
        assert nouveau.montant == Decimal(195000)
        assert nouveau.fixe
        assert nouveau.fixe_par == "C-001"

    def test_une_formule_se_fixe_seule(self):
        adhesion = service_par_code(CATALOGUE, "ADHESION")
        apres = fixer(
            adhesion,
            _decision(service="ADHESION", formule="LIBERATOIRE", montant=Decimal(12500)),
        )
        formules = {f.code: f for f in apres.formules}
        assert formules["LIBERATOIRE"].tarif_au(date(2026, 10, 2)).fixe
        # Les autres tranches ne sont pas touchées.
        assert not formules["REEL_SIMPLIFIE"].tarif_au(date(2026, 10, 2)).fixe

    def test_un_service_a_formules_exige_la_formule(self):
        with pytest.raises(FixationRefusee, match="préciser laquelle"):
            fixer(service_par_code(CATALOGUE, "ADHESION"), _decision(service="ADHESION"))

    def test_une_formule_inconnue_est_refusee(self):
        with pytest.raises(FixationRefusee, match="pas de formule"):
            fixer(
                service_par_code(CATALOGUE, "ADHESION"),
                _decision(service="ADHESION", formule="PREMIUM"),
            )

    def test_le_sur_etude_ne_recoit_pas_de_prix_unique(self):
        with pytest.raises(FixationRefusee, match="sur étude"):
            fixer(service_par_code(CATALOGUE, "CREATION"), _decision(service="CREATION"))

    def test_un_prix_nul_ou_sans_motif_ne_se_fixe_pas(self):
        with pytest.raises(ValueError):
            _decision(montant=Decimal(0))
        with pytest.raises(ValueError):
            _decision(motif="")

    def test_les_decisions_se_rejouent_dans_l_ordre_ou_elles_ont_ete_prises(self):
        # Le gérant fixe d'abord octobre, puis se ravise pour novembre : les deux
        # tiennent, chacune à sa date.
        octobre = _decision(identifiant="TF-1", fixe_le=datetime(2026, 9, 21, 9))
        novembre = _decision(
            identifiant="TF-2",
            montant=Decimal(200000),
            a_partir_du=date(2026, 11, 1),
            fixe_le=datetime(2026, 9, 21, 11),
        )
        catalogue = appliquer_les_fixations(CATALOGUE, [novembre, octobre])
        domiciliation = service_par_code(catalogue, "DOMICILIATION")
        assert domiciliation.tarif_au(date(2026, 10, 15)).montant == Decimal(195000)
        assert domiciliation.tarif_au(date(2026, 11, 15)).montant == Decimal(200000)


# ── Les routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> Iterator[TestClient]:
    reinitialiser_atelier()
    reinitialiser_comptoir()
    with horloge_figee(INSTANT):
        yield TestClient(creer_application())


def _connecter(client: TestClient, courriel: str) -> TestClient:
    r = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert r.status_code == 200, r.text
    return client


def _devis_de_domiciliation(client: TestClient) -> str:
    r = client.post(
        "/souscription/devis",
        json={
            "prospect": {
                "nom": "ESSAI",
                "prenom": "Prix",
                "courriel": "prix.essai@exemple.cm",
                "telephone": "677000333",
            },
            "lignes": [{"service": "DOMICILIATION"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["reference"]


FIXATION = {
    "service": "DOMICILIATION",
    "montant": "180000",
    "a_partir_du": "2026-09-21",
    "motif": "Barème 2026 confirmé par la gérante.",
}


class TestLaRouteDeFixation:
    def test_seule_la_direction_fixe_un_prix(self, client: TestClient):
        _connecter(client, "p.moukouri@cga-brcg.cm")  # chargée de clientèle
        assert client.post("/souscription/tarifs", json=FIXATION).status_code == 403
        _connecter(client, "s.onana@cga-brcg.cm")  # administrateur
        assert client.post("/souscription/tarifs", json=FIXATION).status_code == 403

    def test_un_visiteur_ne_lit_meme_pas_l_etat_des_prix(self, client: TestClient):
        r = client.get("/souscription/tarifs", params={"a_la_date": "2026-09-21"})
        assert r.status_code == 401

    def test_la_direction_voit_ce_qui_reste_a_fixer(self, client: TestClient):
        _connecter(client, "b.mballa@cga-brcg.cm")
        etat = client.get("/souscription/tarifs", params={"a_la_date": "2026-09-21"}).json()
        # Libératoire, réel simplifié, réel, paie, domiciliation, formation : six
        # prix à fixer ; la création et le ponctuel se chiffrent sur étude.
        #
        # ⚠️ LE COMPTE MONTE QUAND L'OFFRE S'ÉLARGIT, ET C'EST CE QU'ON VEUT
        # VOIR. La paie est entrée au catalogue le 30 septembre avec un tarif de
        # travail : la direction a donc un prix de plus à arrêter, et cet écran
        # est précisément celui qui le lui dit. Un service ajouté sans que ce
        # compte bouge serait un service dont personne n'aurait à valider le
        # prix.
        assert etat["a_fixer"] == 6
        fixables = {(x["service"], x["formule"]) for x in etat["lignes"] if x["fixable"]}
        assert ("CREATION", None) not in fixables

    def test_la_direction_fixe_et_la_decision_est_tracee(self, client: TestClient):
        _connecter(client, "b.mballa@cga-brcg.cm")
        r = client.post("/souscription/tarifs", json=FIXATION)
        assert r.status_code == 201, r.text
        etat = r.json()
        # Un prix fixé, il en reste cinq. Voir le cas précédent pour le total.
        assert etat["a_fixer"] == 5
        ligne = next(x for x in etat["lignes"] if x["service"] == "DOMICILIATION")
        assert ligne["statut"] == "FIXE"
        assert ligne["fixe_par"] == "C-001"
        # Le nom, et non l'identifiant seul : c'est ce que la direction relit.
        assert ligne["fixe_par_nom"] == "Bernadette MBALLA"
        assert etat["decisions"][0]["fixe_par_nom"] == "Bernadette MBALLA"
        assert etat["decisions"][0]["motif"] == FIXATION["motif"]

    def test_un_prix_ne_se_fixe_pas_dans_le_passe(self, client: TestClient):
        _connecter(client, "b.mballa@cga-brcg.cm")
        r = client.post(
            "/souscription/tarifs", json={**FIXATION, "a_partir_du": "2026-09-20"}
        )
        assert r.status_code == 422
        assert "passé" in r.json()["detail"]

    def test_une_prestation_inconnue(self, client: TestClient):
        _connecter(client, "b.mballa@cga-brcg.cm")
        r = client.post("/souscription/tarifs", json={**FIXATION, "service": "INCONNU"})
        assert r.status_code == 404

    def test_le_compte_du_gerant_ne_sort_pas_sur_la_route_publique(
        self, client: TestClient
    ):
        _connecter(client, "b.mballa@cga-brcg.cm")
        client.post("/souscription/tarifs", json=FIXATION)
        public = TestClient(creer_application())
        r = public.get("/souscription/services", params={"a_la_date": "2026-09-21"})
        assert "C-001" not in r.text
        dom = next(s for s in r.json() if s["code"] == "DOMICILIATION")
        assert dom["tarifs"][-1]["statut"] == "FIXE"


class TestLePaiementAttendLaDecision:
    def test_un_prix_a_valider_ne_se_paie_pas(self, client: TestClient):
        # ⚠️ Le cas qui compte : le devis s'établit — le visiteur voit un prix
        # indicatif —, mais l'engagement est refusé, et dit pourquoi.
        reference = _devis_de_domiciliation(client)
        r = client.post(f"/souscription/devis/{reference}/engagement", json={})
        # 409 : le code que la route donne à tout engagement refusé. L'état du
        # tarif, et non la demande, empêche le paiement.
        assert r.status_code == 409, r.text
        assert "arrêté par le cabinet" in r.json()["detail"]

    def test_une_fois_le_prix_fixe_il_se_paie(self, client: TestClient):
        direction = _connecter(TestClient(creer_application()), "b.mballa@cga-brcg.cm")
        assert direction.post("/souscription/tarifs", json=FIXATION).status_code == 201

        reference = _devis_de_domiciliation(client)
        r = client.post(f"/souscription/devis/{reference}/engagement", json={})
        assert r.status_code == 201, r.text
        assert r.json()["paiement"]["montant"] == "180000"


class TestLeDevisDitSIlSePaie:
    def test_un_devis_sur_prix_indicatif_le_dit(self, client: TestClient):
        reference = _devis_de_domiciliation(client)
        devis = client.get(f"/souscription/devis/{reference}").json()
        assert devis["prix_arretes"] is False
        assert devis["lignes"][0]["prix_arrete"] is False

    def test_un_devis_etabli_apres_la_decision_se_paie(self, client: TestClient):
        direction = _connecter(TestClient(creer_application()), "b.mballa@cga-brcg.cm")
        direction.post("/souscription/tarifs", json=FIXATION)
        devis = client.get(
            f"/souscription/devis/{_devis_de_domiciliation(client)}"
        ).json()
        assert devis["prix_arretes"] is True

    def test_un_devis_anterieur_a_la_decision_reste_indicatif(self, client: TestClient):
        # ⚠️ Le devis est figé : il a recopié un prix indicatif, il le reste. Le
        # visiteur en établit un nouveau, au prix décidé.
        ancien = _devis_de_domiciliation(client)
        direction = _connecter(TestClient(creer_application()), "b.mballa@cga-brcg.cm")
        direction.post("/souscription/tarifs", json=FIXATION)

        assert client.get(f"/souscription/devis/{ancien}").json()["prix_arretes"] is False
        r = client.post(f"/souscription/devis/{ancien}/engagement", json={})
        assert r.status_code == 409
        nouveau = _devis_de_domiciliation(client)
        r = client.post(f"/souscription/devis/{nouveau}/engagement", json={})
        assert r.status_code == 201, r.text
