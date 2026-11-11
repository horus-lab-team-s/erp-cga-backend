"""Le parcours de création COMPLET, en recette : frais, proforma, tranches, solde (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
« Pour les tests, active les validations de paiement automatiques, mais assure-toi que le
flux est complet. » Ce cas le prouve de bout en bout, par les routes réelles, sous
`CGA_MODE_DEMONSTRATION` : chaque paiement mobile se valide par le chemin d'une vraie
notification de l'opérateur.

1. la direction fixe les frais de création (25 000) et le seuil de démarrage (70 %) ;
2. le visiteur dépose sa demande et règle ses frais par mobile money : validés d'office ;
3. le responsable affecte, qualifie, chiffre, émet et transmet ; le client accepte ;
4. la proforma consultée déduit les frais ;
5. une première tranche porte le versé au seuil : le dossier est ENGAGÉ, un seul
   `PaiementEncaissé` part, et le dossier de création s'ouvre ;
6. la seconde tranche solde : aucun second événement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from sqlalchemy import text

from tests import test_recette_du_parcours as recette
from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_situation_de_reglement import SUPER_ADMIN, _renforcer

pytestmark = exige_postgresql


def _evenements(moteur_test) -> int:
    with moteur_test.connect() as connexion:
        return connexion.execute(
            text("SELECT count(*) FROM boite_d_envoi WHERE nom = 'PaiementEncaissé'")
        ).scalar_one()


def _consulter(client, proforma) -> dict:
    lue = client.get(
        f"/acquisition/proformas/{proforma['numero']}/consultation",
        params={
            "version": proforma["version"],
            "expire_le": proforma["expire_le"],
            "sceau": proforma["lien_acceptation"],
        },
    )
    assert lue.status_code == 200, lue.text
    return lue.json()


def _payer(client, proforma, montant: str | None = None):
    corps = {
        "sceau": proforma["lien_acceptation"],
        "expire_le": proforma["expire_le"],
        "version": proforma["version"],
    }
    if montant is not None:
        corps["montant"] = montant
    reponse = client.post(
        f"/acquisition/proformas/{proforma['numero']}/paiement-en-ligne", json=corps
    )
    assert reponse.status_code == 202, reponse.text
    assert reponse.json()["simule"] is True


def test_de_la_demande_au_solde_en_recette(plateforme_amorcee, moteur_test, monkeypatch):
    from app.infrastructure.config import configuration

    monkeypatch.setenv("CGA_MODE_DEMONSTRATION", "true")
    configuration.cache_clear()
    client = plateforme_amorcee
    try:
        # 1 · La direction fixe les frais et le seuil.
        ouvrir_une_session(client, SUPER_ADMIN)
        _renforcer(client)
        regle = client.put(
            "/souscription/versements/regle",
            json={
                "frais_par_service": {"CREATION": "25000"},
                "seuil_demarrage_pourcent": 70,
                "motif": "recette du parcours complet de création",
            },
        )
        assert regle.status_code == 200, regle.text

        # 2 · Le visiteur dépose et règle ses frais : validés d'office.
        client.cookies.clear()
        accuse = client.post(
            "/acquisition/demandes",
            json={
                "nom": "Station Bonabéri",
                "telephone": "699112233",
                "courriel": "gerant@station-bonaberi.cm",
                "service_souhaite": "CREATION",
                "canal_prefere": "APPEL",
            },
        ).json()
        assert accuse["frais_de_traitement"] == "25000", accuse
        frais = client.post(
            "/acquisition/demandes/frais-de-traitement/paiement-en-ligne",
            json={"jeton": accuse["jeton_de_frais"]},
        )
        assert frais.status_code == 202, frais.text
        assert (
            client.post(
                "/acquisition/demandes/frais-de-traitement/etat",
                json={"jeton": accuse["jeton_de_frais"]},
            ).json()["verses"]
            is True
        )

        # 3 · Le responsable mène le dossier jusqu'à l'acceptation.
        ouvrir_une_session(client, recette.RESPONSABLE)
        reference = client.get("/acquisition/dossiers").json()[0]["reference"]
        assert (
            client.post(f"/acquisition/dossiers/{reference}/affectation", json={}).status_code
            == 200
        )
        parcours = recette.TestLeParcoursEntier()
        parcours._qualifier(client, reference, [("forme_juridique", "SARL"), ("associes", 2)])
        complete = parcours._qualifier(
            client,
            reference,
            [
                ("capital_social", "1000000"),
                ("apports_en_nature", False),
                ("chiffre_affaires_prevu", "DE_10M_A_50M"),
                ("salaries_prevus", 3),
                ("region_siege", "LITTORAL"),
                ("activite", "station-service et boutique"),
                ("denomination_souhaitee", "STATION BONABÉRI SARL"),
                ("siege", "Bonabéri, Douala"),
            ],
        )
        assert complete["complete"] is True, complete["manquantes"]
        chiffrage = client.post(
            f"/acquisition/dossiers/{reference}/chiffrage", json={"debours": []}
        )
        assert chiffrage.status_code == 200, chiffrage.text
        emission = client.post(
            f"/acquisition/dossiers/{reference}/proforma",
            json={"montant": chiffrage.json()["reference"]},
        )
        assert emission.status_code == 201, emission.text
        proforma = emission.json()
        assert (
            client.post(f"/acquisition/proformas/{proforma['numero']}/transmission").status_code
            == 200
        )
        acceptation = client.post(
            f"/acquisition/proformas/{proforma['numero']}/acceptation",
            json={
                "sceau": proforma["lien_acceptation"],
                "expire_le": proforma["expire_le"],
                "version": proforma["version"],
                "identite_declaree": "Awa NDONGO, fondatrice",
            },
        )
        assert acceptation.status_code == 200, acceptation.text

        # 4 · La proforma consultée déduit les frais.
        total = Decimal(chiffrage.json()["reference"])
        lue = _consulter(client, proforma)
        assert Decimal(lue["frais_verses"]) == Decimal("25000")
        assert Decimal(lue["reste"]) == total - Decimal("25000")
        assert lue["payee"] is False and lue["engage"] is False

        # 5 · La première tranche porte le versé au seuil : engagé, un seul événement.
        seuil = (total * Decimal("0.70")).quantize(Decimal(1), rounding=ROUND_CEILING)
        _payer(client, proforma, str(seuil - Decimal("25000")))
        lue = _consulter(client, proforma)
        assert lue["engage"] is True and lue["payee"] is False, lue
        assert _evenements(moteur_test) == 1
        rapport = recette._tour()
        assert all(r.reussi for r in rapport.resultats), [
            r.compte_rendu for r in rapport.resultats if not r.reussi
        ]
        with moteur_test.connect() as connexion:
            ouverts = connexion.execute(
                text("SELECT count(*) FROM dossier_creation WHERE reference = :r"),
                {"r": f"CRE-{reference}"},
            ).scalar_one()
        assert ouverts == 1, "le dossier de création ne s'est pas ouvert au seuil"

        # 6 · La seconde tranche solde : aucun second événement.
        _payer(client, proforma)
        lue = _consulter(client, proforma)
        assert lue["payee"] is True and Decimal(lue["reste"]) == 0, lue
        assert _evenements(moteur_test) == 1
    finally:
        monkeypatch.delenv("CGA_MODE_DEMONSTRATION")
        configuration.cache_clear()
