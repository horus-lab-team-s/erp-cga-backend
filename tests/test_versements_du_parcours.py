"""Frais de traitement, tranches de la proforma, seuil de démarrage (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
RÉUNION DU 4 NOVEMBRE 2026, DEMANDE 21, ET PRÉCISION DE L'UTILISATEUR DU 9 NOVEMBRE

« Afficher le montant payé, le reste à payer et le statut soldé. » Les frais de
traitement versés à la soumission sont déduits du total. Le super administrateur fixe
les frais par service et le seuil à partir duquel le dossier est engagé (70 %…).

⚠️ CE QUI EST GARDÉ
* sans règle : ni frais, ni seuil réduit — le parcours d'avant ce lot, à l'identique ;
* payé = frais + tranches ; reste ; soldé ; seuil arrondi au franc supérieur ;
* un versement au-delà du reste, ou des frais versés deux fois, sont refusés ;
* le dossier est engagé (PAYÉE, un seul événement) au seuil, puis se solde ;
* un dossier payé avant ce lot, sans versement, se lit soldé ;
* par les routes : la règle par le super administrateur seul, les frais au guichet,
  les tranches au guichet et par l'opérateur, la caisse qui affiche le reste ;
* l'accusé de la demande porte les frais et le jeton qui permet de les régler.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.versements_du_parcours import (
    REGLE_SANS_FRAIS,
    NatureVersement,
    RegleDesVersements,
    VersementDuParcours,
    VersementRefuse,
    situation_des_versements,
    verifier_le_versement,
)
from tests import test_recette_du_parcours as recette
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import SUPER_ADMIN, _renforcer

LE = datetime(2026, 11, 9, 9)
DOSSIER = "dos-essai"


def _regle(seuil: int = 70, **frais) -> RegleDesVersements:
    return RegleDesVersements(
        frais_par_service={k: Decimal(v) for k, v in frais.items()},
        seuil_demarrage_pourcent=seuil,
        motif="règle d'essai",
        fixee_par="C-010",
        fixee_le=LE,
    )


def _versement(montant: str, nature=NatureVersement.TRANCHE, ref: str = "") -> VersementDuParcours:
    return VersementDuParcours(
        identifiant=f"v-{montant}-{ref}",
        dossier=DOSSIER,
        nature=nature,
        montant=Decimal(montant),
        canal=CanalDeReglement.GUICHET,
        reference_externe=ref or f"reçu-{montant}",
        constate_le=LE,
        constate_par="C-009",
    )


class TestLaSituation:
    def test_sans_regle_ni_frais_ni_seuil_reduit(self):
        assert REGLE_SANS_FRAIS.frais_de("CREATION") is None
        assert REGLE_SANS_FRAIS.seuil_demarrage_pourcent == 100

    def test_frais_deduits_puis_tranches_jusqu_au_solde(self):
        total = Decimal("200000")
        frais = _versement("25000", NatureVersement.FRAIS_DE_TRAITEMENT)
        s = situation_des_versements(DOSSIER, total, [frais], _regle())
        assert (s.paye, s.reste, s.solde, s.seuil) == (
            Decimal("25000"),
            Decimal("175000"),
            False,
            Decimal("140000"),
        )
        assert not s.seuil_atteint

        s = situation_des_versements(DOSSIER, total, [frais, _versement("115000")], _regle())
        assert s.seuil_atteint and not s.solde and s.reste == Decimal("60000")

        s = situation_des_versements(
            DOSSIER, total, [frais, _versement("115000"), _versement("60000")], _regle()
        )
        assert s.solde and s.reste == 0

    def test_le_seuil_s_arrondit_au_franc_superieur(self):
        s = situation_des_versements(DOSSIER, Decimal("100001"), [], _regle(70))
        assert s.seuil == Decimal("70001")

    def test_sans_proforma_seuls_les_frais_se_lisent(self):
        frais = _versement("25000", NatureVersement.FRAIS_DE_TRAITEMENT)
        s = situation_des_versements(DOSSIER, None, [frais], _regle())
        assert s.total is None and s.reste is None and s.frais_verses == Decimal("25000")


class TestCeQuiEstRefuse:
    def test_au_dela_du_reste(self):
        s = situation_des_versements(DOSSIER, Decimal("100000"), [_versement("60000")], _regle())
        with pytest.raises(VersementRefuse, match="dépasse le reste"):
            verifier_le_versement(s, NatureVersement.TRANCHE, Decimal("40001"))
        verifier_le_versement(s, NatureVersement.TRANCHE, Decimal("40000"))

    def test_des_frais_verses_deux_fois_sont_tous_deduits(self):
        """9 novembre 2026 : au rendez-vous, puis au formulaire — la somme des deux."""
        premier = _versement("25000", NatureVersement.FRAIS_DE_TRAITEMENT, ref="a")
        s = situation_des_versements(DOSSIER, Decimal("200000"), [premier], _regle())
        verifier_le_versement(s, NatureVersement.FRAIS_DE_TRAITEMENT, Decimal("25000"))
        second = _versement("25000", NatureVersement.FRAIS_DE_TRAITEMENT, ref="b")
        s = situation_des_versements(DOSSIER, Decimal("200000"), [premier, second], _regle())
        assert s.frais_verses == Decimal("50000") and s.reste == Decimal("150000")

    def test_une_penalite_n_est_jamais_deduite(self):
        penalite = _versement("10000", NatureVersement.PENALITE_RENDEZ_VOUS)
        s = situation_des_versements(DOSSIER, Decimal("200000"), [penalite], _regle())
        assert s.paye == 0 and s.reste == Decimal("200000")
        assert s.penalites_versees == Decimal("10000")

    def test_une_tranche_sans_proforma(self):
        s = situation_des_versements(DOSSIER, None, [], _regle())
        with pytest.raises(VersementRefuse, match="aucune proforma"):
            verifier_le_versement(s, NatureVersement.TRANCHE, Decimal("1000"))

    def test_des_frais_nuls_ne_se_fixent_pas(self):
        with pytest.raises(ValueError, match="montant positif"):
            _regle(CREATION="0")


# ── Par les routes ──────────────────────────────────────────────────────────────────

CAISSIERE = "n.essama@cga-brcg.cm"
DIRECTRICE = "b.mballa@cga-brcg.cm"
MOTIF = "décision du 9 novembre 2026 sur les versements"


#: Le second facteur du super administrateur, enrôlé une fois par cas : un second
#: enrôlement est refusé, on renforce donc la session avec le secret déjà connu.
_SECRETS: dict[int, str] = {}


@pytest.fixture(autouse=True)
def _oublier_les_secrets():
    """Chaque cas repart d'une base neuve : aucun secret ne survit d'un cas à l'autre."""
    _SECRETS.clear()
    yield
    _SECRETS.clear()


def _fixer_la_regle(client, *, seuil: int = 70, frais: str | None = "25000"):
    client.cookies.clear()
    ouvrir_une_session(client, SUPER_ADMIN)
    secret = _SECRETS.get(id(client))
    _SECRETS[id(client)] = _renforcer(client, secret, decalage=30 if secret else 0)
    corps = {
        "frais_par_service": {"CREATION": frais} if frais else {},
        "seuil_demarrage_pourcent": seuil,
        "motif": MOTIF,
    }
    reponse = client.put("/souscription/versements/regle", json=corps)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _en_caisse(client):
    client.cookies.clear()
    ouvrir_une_session(client, CAISSIERE)


def _vente_acceptee(client) -> tuple[str, str]:
    return recette.TestLeReglementNotifieParLOperateur()._dossier_accepte(client)


def _evenements(moteur_test, reference: str) -> int:
    with moteur_test.connect() as connexion:
        return connexion.execute(
            text("SELECT count(*) FROM boite_d_envoi WHERE cle = :c AND nom = :n"),
            {"c": reference, "n": "PaiementEncaissé"},
        ).scalar_one()


@exige_postgresql
class TestParLesRoutes:
    def test_seul_le_super_administrateur_fixe_la_regle(self, plateforme):
        client = plateforme
        client.cookies.clear()
        ouvrir_une_session(client, DIRECTRICE)
        lue = client.get("/souscription/versements/regle")
        assert lue.status_code == 200 and lue.json()["par_defaut"] is True, lue.text
        refus = client.put(
            "/souscription/versements/regle",
            json={"frais_par_service": {}, "seuil_demarrage_pourcent": 50, "motif": MOTIF},
        )
        assert refus.status_code == 403, refus.text

    def test_frais_puis_tranches_au_guichet_jusqu_au_solde(self, plateforme, moteur_test):
        client = plateforme
        reference, numero = _vente_acceptee(client)  # 250 000 FCFA
        _fixer_la_regle(client, seuil=70)  # seuil : 175 000

        _en_caisse(client)
        frais = client.post(
            f"/acquisition/dossiers/{reference}/frais-de-traitement/encaissement",
            json={"reference_externe": "Reçu 2026-1001"},
        )
        assert frais.status_code == 201, frais.text
        assert Decimal(frais.json()["reste"]) == Decimal("225000")
        # 9 novembre 2026 : des frais versés une seconde fois sont reçus, et TOUS déduits.
        double = client.post(
            f"/acquisition/dossiers/{reference}/frais-de-traitement/encaissement",
            json={"reference_externe": "Reçu 2026-1002"},
        )
        assert double.status_code == 201, double.text
        assert Decimal(double.json()["reste"]) == Decimal("200000")

        # Une tranche sans reçu : refusée, un double clic compterait deux fois.
        sans_recu = client.post(
            f"/acquisition/proformas/{numero}/encaissement", json={"montant": "100000"}
        )
        assert sans_recu.status_code == 422, sans_recu.text

        # 50 000 + 100 000 = 150 000 : sous le seuil, le dossier attend.
        t1 = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"montant": "100000", "reference_externe": "Reçu 2026-1003"},
        )
        assert t1.status_code == 200 and t1.json()["etat"] == "ACCEPTEE", t1.text
        assert _evenements(moteur_test, reference) == 0

        # Au-delà du reste : refusé.
        trop = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"montant": "125001", "reference_externe": "Reçu 2026-1004"},
        )
        assert trop.status_code == 409, trop.text

        # 150 000 + 50 000 = 200 000 : au-delà du seuil, le dossier est engagé.
        t2 = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"montant": "50000", "reference_externe": "Reçu 2026-1005"},
        )
        assert t2.status_code == 200 and t2.json()["etat"] == "PAYEE", t2.text
        assert _evenements(moteur_test, reference) == 1

        (ligne,) = [
            r for r in client.get("/acquisition/caisse").json() if r["dossier"] == reference
        ]
        assert ligne["etat"] == "A_ENCAISSER" and ligne["engage"] is True
        assert Decimal(ligne["reste"]) == Decimal("50000")
        assert Decimal(ligne["montant"]) == Decimal("50000")

        # Le solde, sans montant : tout le reste. Aucun second événement.
        solde = client.post(
            f"/acquisition/proformas/{numero}/encaissement",
            json={"reference_externe": "Reçu 2026-1006"},
        )
        assert solde.status_code == 200, solde.text
        assert _evenements(moteur_test, reference) == 1
        situation = client.get(f"/acquisition/dossiers/{reference}/versements").json()
        assert situation["solde"] is True and Decimal(situation["paye"]) == Decimal("250000")
        (ligne,) = [
            r for r in client.get("/acquisition/caisse").json() if r["dossier"] == reference
        ]
        assert ligne["etat"] == "ENCAISSE"

        # Le journal de caisse porte chaque versement.
        journal = client.get("/souscription/caisse/journal").json()
        mien = [ligne for ligne in journal["lignes"] if ligne["dossier"] == reference]
        assert sorted(Decimal(ligne["montant"]) for ligne in mien) == [
            Decimal("25000"),
            Decimal("25000"),
            Decimal("50000"),
            Decimal("50000"),
            Decimal("100000"),
        ]

    def test_une_tranche_notifiee_par_l_operateur(self, plateforme, moteur_test):
        client = plateforme
        reference, numero = _vente_acceptee(client)
        _fixer_la_regle(client, seuil=50, frais=None)  # seuil : 125 000

        _en_caisse(client)
        demande = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"montant": "125000"}
        )
        assert demande.status_code == 202, demande.text
        assert Decimal(demande.json()["montant"]) == Decimal("125000")
        with moteur_test.connect() as connexion:
            cle = connexion.execute(
                text("SELECT donnees->>'cle_idempotence' FROM paiement WHERE identifiant = :p"),
                {"p": demande.json()["paiement"]},
            ).scalar_one()
        charge = {
            "productId": cle,
            "paymentId": "TARA-TRANCHE-0001",
            "status": "SUCCESS",
            "amount": "125000",
            "phoneNumber": "237690112233",
        }
        notification = client.post("/souscription/notification/tara", json=charge)
        assert notification.status_code == 200, notification.text
        (ligne,) = [
            r for r in client.get("/acquisition/caisse").json() if r["dossier"] == reference
        ]
        assert ligne["engage"] is True and ligne["etat"] == "A_ENCAISSER"
        # Rejouée : rien n'est compté deux fois.
        client.post("/souscription/notification/tara", json=charge)
        situation = client.get(f"/acquisition/dossiers/{reference}/versements").json()
        assert Decimal(situation["paye"]) == Decimal("125000")
        assert Decimal(situation["reste"]) == Decimal("125000")
        assert _evenements(moteur_test, reference) == 1

        # Engagé mais pas soldé : le reste se demande encore.
        reste = client.post(f"/acquisition/proformas/{numero}/reglement", json={})
        assert reste.status_code == 202, reste.text
        assert Decimal(reste.json()["montant"]) == Decimal("125000")

    def test_l_accuse_porte_les_frais_et_leur_jeton(self, plateforme):
        client = plateforme
        _fixer_la_regle(client)
        client.cookies.clear()
        depot = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Station Bonabéri",
                "telephone": "699112233",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        )
        assert depot.status_code == 201, depot.text
        accuse = depot.json()
        assert Decimal(accuse["frais_de_traitement"]) == Decimal("25000")
        jeton = accuse["jeton_de_frais"]
        assert jeton and jeton != accuse["jeton_de_pieces"]

        etat = client.post("/acquisition/demandes/frais-de-traitement/etat", json={"jeton": jeton})
        assert etat.status_code == 200 and etat.json() == {"montant": "25000", "verses": False}

        paiement = client.post(
            "/acquisition/demandes/frais-de-traitement/paiement-en-ligne", json={"jeton": jeton}
        )
        assert paiement.status_code == 202, paiement.text
        assert Decimal(paiement.json()["montant"]) == Decimal("25000")

        # Le jeton des pièces n'ouvre pas les frais : un usage, un jeton.
        faux = client.post(
            "/acquisition/demandes/frais-de-traitement/etat",
            json={"jeton": accuse["jeton_de_pieces"]},
        )
        assert faux.status_code == 404, faux.text

    def test_sans_regle_l_accuse_ne_parle_pas_de_frais(self, plateforme):
        client = plateforme
        depot = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Station Bonabéri",
                "telephone": "699112233",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        )
        assert depot.status_code == 201, depot.text
        assert depot.json()["frais_de_traitement"] is None
        assert depot.json()["jeton_de_frais"] is None


# ── Sur la proforma ─────────────────────────────────────────────────────────────────


def test_la_proforma_deduit_les_frais_deja_verses():
    from tests.test_creation_reunion_du_4_novembre import _proforma
    from tests.test_document_de_la_proforma import _texte_du_pdf

    texte = _texte_du_pdf(_proforma(frais_deja_verses=Decimal("25000")))
    assert "Frais de traitement de dossier déjà versés" in texte
    assert "RESTE À RÉGLER" in texte
    assert "plusieurs tranches" in texte
    # Sans frais versés, rien de tout cela.
    assert "RESTE À RÉGLER" not in _texte_du_pdf(_proforma())


@exige_postgresql
def test_la_caisse_voit_les_frais_attendus_puis_les_constate(plateforme):
    client = plateforme
    _fixer_la_regle(client)
    client.cookies.clear()
    depot = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Couture Ngono",
            "telephone": "699445566",
            "service_souhaite": "CREATION",
            "canal_prefere": "APPEL",
        },
    )
    assert depot.status_code == 201, depot.text

    _en_caisse(client)
    (ligne,) = [
        r
        for r in client.get("/acquisition/caisse").json()
        if r["objet"] == "FRAIS_DE_TRAITEMENT" and r["client"] == "Couture Ngono"
    ]
    assert Decimal(ligne["montant"]) == Decimal("25000") and ligne["proforma"] == ""

    constat = client.post(
        f"/acquisition/dossiers/{ligne['dossier']}/frais-de-traitement/encaissement",
        json={"reference_externe": "Reçu 2026-2001"},
    )
    assert constat.status_code == 201, constat.text
    assert not [
        r
        for r in client.get("/acquisition/caisse").json()
        if r["objet"] == "FRAIS_DE_TRAITEMENT" and r["dossier"] == ligne["dossier"]
    ]


# ── La compensation côté Tara, et la recette (9 novembre 2026) ─────────────────────


def _cle_du_paiement(moteur_test, identifiant: str) -> str:
    with moteur_test.connect() as connexion:
        return connexion.execute(
            text("SELECT donnees->>'cle_idempotence' FROM paiement WHERE identifiant = :p"),
            {"p": identifiant},
        ).scalar_one()


@exige_postgresql
def test_un_paiement_notifie_au_dela_du_reste_n_est_jamais_perdu(plateforme, moteur_test):
    """Le client valide 125 000 sur son téléphone ; entre-temps la caisse a reçu 200 000.
    L'opérateur notifie : 50 000 soldent la proforma, 75 000 deviennent un trop-perçu à
    restituer — et non un débit que rien ne crédite, qui ferait repayer le client."""
    client = plateforme
    reference, numero = _vente_acceptee(client)  # 250 000 FCFA
    _fixer_la_regle(client, seuil=100, frais=None)

    _en_caisse(client)
    demande = client.post(f"/acquisition/proformas/{numero}/reglement", json={"montant": "125000"})
    assert demande.status_code == 202, demande.text
    guichet = client.post(
        f"/acquisition/proformas/{numero}/encaissement",
        json={"montant": "200000", "reference_externe": "Reçu 2026-4001"},
    )
    assert guichet.status_code == 200, guichet.text

    notification = client.post(
        "/souscription/notification/tara",
        json={
            "productId": _cle_du_paiement(moteur_test, demande.json()["paiement"]),
            "paymentId": "TARA-COMPENSATION-1",
            "status": "SUCCESS",
            "amount": "125000",
            "phoneNumber": "237690112233",
        },
    )
    assert notification.status_code == 200, notification.text
    situation = client.get(f"/acquisition/dossiers/{reference}/versements").json()
    assert situation["solde"] is True and situation["paye"] == "250000"
    assert situation["trop_percu"] == "75000"

    (ligne,) = [
        r
        for r in client.get("/acquisition/caisse").json()
        if r["dossier"] == reference and r["objet"] == "TROP_PERCU"
    ]
    assert ligne["etat"] == "A_RESTITUER" and ligne["montant"] == "75000"

    rendu = client.post(
        f"/acquisition/dossiers/{reference}/trop-percu/restitution",
        json={"reference_externe": "Reçu de restitution 1", "motif": "rendu en espèces"},
    )
    assert rendu.status_code == 201, rendu.text
    assert rendu.json()["trop_percu"] == "0"
    assert not [
        r
        for r in client.get("/acquisition/caisse").json()
        if r["dossier"] == reference and r["objet"] == "TROP_PERCU"
    ]


@exige_postgresql
def test_des_frais_notifies_comptent_meme_si_la_regle_a_change(plateforme, moteur_test):
    client = plateforme
    _fixer_la_regle(client)  # 25 000
    client.cookies.clear()
    accuse = client.post(
        "/acquisition/demandes",
        json={
            "nom": "Couture Ngono",
            "telephone": "699445566",
            "service_souhaite": "CREATION",
            "canal_prefere": "APPEL",
        },
    ).json()
    paiement = client.post(
        "/acquisition/demandes/frais-de-traitement/paiement-en-ligne",
        json={"jeton": accuse["jeton_de_frais"]},
    )
    assert paiement.status_code == 202, paiement.text
    _fixer_la_regle(client, frais="30000")  # la direction change le montant entre-temps
    with moteur_test.connect() as connexion:
        identifiant, cle = connexion.execute(
            text(
                "SELECT identifiant, donnees->>'cle_idempotence' FROM paiement "
                "WHERE donnees->>'nature' = 'FRAIS_DE_TRAITEMENT'"
            )
        ).one()
    client.cookies.clear()
    notification = client.post(
        "/souscription/notification/tara",
        json={
            "productId": cle,
            "paymentId": "TARA-FRAIS-1",
            "status": "SUCCESS",
            "amount": "25000",
            "phoneNumber": "237699445566",
        },
    )
    assert notification.status_code == 200, notification.text
    etat = client.post(
        "/acquisition/demandes/frais-de-traitement/etat", json={"jeton": accuse["jeton_de_frais"]}
    ).json()
    assert etat["verses"] is True


@exige_postgresql
def test_en_recette_frais_et_tranches_se_valident_seuls(plateforme, monkeypatch):
    """« Pour les tests, active les validations de paiement automatiques » : sous
    `CGA_MODE_DEMONSTRATION` seulement, par le chemin d'une vraie notification."""
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true")
    configuration.cache_clear()
    try:
        client = plateforme
        reference, numero = _vente_acceptee(client)  # 250 000 FCFA
        _fixer_la_regle(client, seuil=50)

        client.cookies.clear()
        accuse = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Couture Ngono",
                "telephone": "699445566",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        ).json()
        frais = client.post(
            "/acquisition/demandes/frais-de-traitement/paiement-en-ligne",
            json={"jeton": accuse["jeton_de_frais"]},
        )
        assert frais.status_code == 202 and frais.json()["simule"] is True, frais.text
        etat = client.post(
            "/acquisition/demandes/frais-de-traitement/etat",
            json={"jeton": accuse["jeton_de_frais"]},
        ).json()
        assert etat["verses"] is True

        _en_caisse(client)
        tranche = client.post(
            f"/acquisition/proformas/{numero}/reglement", json={"montant": "125000"}
        )
        assert tranche.status_code == 202, tranche.text
        (ligne,) = [
            r
            for r in client.get("/acquisition/caisse").json()
            if r["dossier"] == reference and r["objet"] == "PROFORMA"
        ]
        # 125 000 = 50 % : validée d'office, le dossier est engagé.
        assert ligne["engage"] is True and ligne["reste"] == "125000"
    finally:
        monkeypatch.delenv("CGA_MODE_DEMONSTRATION")
        configuration.cache_clear()


def test_hors_recette_rien_ne_se_valide_seul():
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        valider_d_office_en_recette,
    )

    # Sans `CGA_MODE_DEMONSTRATION`, la fonction ne lit même pas le paiement.
    assert valider_d_office_en_recette("pay-inexistant") is False
