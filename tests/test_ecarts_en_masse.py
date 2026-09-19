"""Écarter des constats en masse (pas 103).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Tout ou rien** : un seul constat refusé, et aucun n'est écarté ; le message nomme
   chaque pièce refusée.
2. **La même politique qu'à l'unité** : sévérité non écartable, second regard, motif minimum,
   règles non écartables, écart déjà ouvert.
3. **Le motif type accélère, le motif détaillé engage** : le type est facultatif, doit être
   proposé pour la règle, et le détail reste obligatoire.
4. **La vue d'une règle** : constats du périmètre, chiffres de la maquette, lignes
   sélectionnables ou non, et pourquoi.
5. **Les conséquences annoncées** : effectifs, en attente, enjeu levé, dossiers, pièces
   redevenues comptabilisables ; et les comptables prévenus.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.conformite.adaptateurs.sortant.depots_ecarts import DepotEcartsMemoire
from app.contextes.conformite.api import FACTURES_DEMO, moteur_par_defaut, vider_les_ecarts
from app.contextes.conformite.application.ecarts import proposer_un_ecart
from app.contextes.conformite.application.ecarts_en_masse import (
    consequences_des_ecarts,
    ecarter_en_masse,
)
from app.contextes.conformite.domaine.ecarts import (
    EcartRefuse,
    MotifInsuffisant,
    MotifType,
    PolitiqueDEcart,
    RegleDEcart,
    StatutEcart,
)
from app.contextes.conformite.domaine.entites import Severite
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

REVISEUR = "a.bouba@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
FISCALISTE = "r.ebolo@cga-brcg.cm"
MOTIF = "Désignations précisées par les bons de livraison joints aux pièces."
INSTANT = datetime(2026, 8, 9, 10, 0)
JUILLET = "du=2026-07-01&au=2026-07-31"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_ecarts()
    yield
    vider_les_ecarts()


def _politique(**autres) -> PolitiqueDEcart:
    valeurs = dict(
        motif_minimum=20,
        par_severite={
            Severite.BLOQUANT: RegleDEcart(ecartable=False),
            Severite.MAJEUR: RegleDEcart(ecartable=True, second_regard=True),
            Severite.AVERTISSEMENT: RegleDEcart(ecartable=True, second_regard=False),
        },
        motifs_types=(
            MotifType(
                code="BON_DE_LIVRAISON", libelle="Bon de livraison joint", regles=("FAC-DOC-011",)
            ),
            MotifType(code="ERREUR_DE_LECTURE", libelle="Erreur de lecture vérifiée"),
        ),
        source="essai",
    )
    return PolitiqueDEcart(**{**valeurs, **autres})


def _pieces(*references):
    moteur = moteur_par_defaut()
    return [
        (FACTURES_DEMO[r].destinataire.niu, moteur.controler(FACTURES_DEMO[r])) for r in references
    ]


def _ecarter(depot, references, code="FAC-DOC-011", motif=MOTIF, motif_type=None, politique=None):
    return ecarter_en_masse(
        _pieces(*references),
        code_regle=code,
        motif=motif,
        motif_type=motif_type,
        par="C-003",
        le=INSTANT,
        politique=politique or _politique(),
        depot=depot,
    )


@pytest.fixture
def cabinet():
    with etabli("CGA-BRCG"):
        yield


@pytest.mark.usefixtures("cabinet")
class TestLeGeste:
    def test_toutes_les_pieces_sont_ecartees_d_une_decision(self):
        depot = DepotEcartsMemoire()
        ecarts = _ecarter(
            depot, ["F-2026-0415", "F-2026-0425", "F-2026-0435"], motif_type="BON_DE_LIVRAISON"
        )
        assert [e.statut for e in ecarts] == [StatutEcart.EFFECTIF] * 3
        assert {e.motif_type for e in ecarts} == {"BON_DE_LIVRAISON"}
        assert len(depot.toutes()) == 3

    def test_un_seul_refus_et_aucun_n_est_ecarte(self):
        depot = DepotEcartsMemoire()
        _ecarter(depot, ["F-2026-0425"])
        with pytest.raises(
            EcartRefuse, match="aucun constat n'a été écarté \\(1 refus sur 2\\)"
        ) as refus:
            _ecarter(depot, ["F-2026-0415", "F-2026-0425"])
        assert "F-2026-0425 : un écart est déjà" in str(refus.value)
        assert "F-2026-0415" not in str(refus.value)
        # La pièce acceptable n'a pas été écartée pour autant.
        assert (
            depot.pour_la_piece(FACTURES_DEMO["F-2026-0415"].destinataire.niu, "F-2026-0415") == []
        )
        assert len(depot.toutes()) == 1

    def test_un_constat_absent_d_une_piece_la_refuse(self):
        with pytest.raises(
            EcartRefuse, match="F-2026-0412 : le rapport de F-2026-0412 ne porte aucun"
        ):
            _ecarter(DepotEcartsMemoire(), ["F-2026-0415", "F-2026-0412"])

    def test_la_politique_s_applique_a_chaque_constat(self):
        with pytest.raises(EcartRefuse, match="ne permet pas d'écarter un constat BLOQUANT"):
            _ecarter(DepotEcartsMemoire(), ["F-2026-0424", "F-2026-0427"], code="FAC-ID-003")
        with pytest.raises(EcartRefuse, match="ne permet pas d'écarter"):
            _ecarter(
                DepotEcartsMemoire(),
                ["F-2026-0415"],
                politique=_politique(regles_non_ecartables=frozenset({"FAC-DOC-011"})),
            )
        majeurs = _ecarter(DepotEcartsMemoire(), ["F-2026-0412", "F-2026-0424"], code="FAC-ACH-007")
        assert [e.statut for e in majeurs] == [StatutEcart.EN_ATTENTE] * 2

    def test_le_motif_et_le_motif_type_sont_verifies_une_fois_avant_tout(self):
        with pytest.raises(MotifInsuffisant):
            _ecarter(DepotEcartsMemoire(), ["F-2026-0415", "F-2026-0425"], motif="trop court")
        with pytest.raises(
            EcartRefuse,
            match=(
                "« BON_DE_LIVRAISON » n'est pas proposé pour la règle FAC-ACH-007 "
                "\\(proposés : ERREUR_DE_LECTURE\\)"
            ),
        ):
            _ecarter(
                DepotEcartsMemoire(),
                ["F-2026-0412"],
                code="FAC-ACH-007",
                motif_type="BON_DE_LIVRAISON",
            )
        assert _ecarter(
            DepotEcartsMemoire(),
            ["F-2026-0412"],
            code="FAC-ACH-007",
            motif_type="ERREUR_DE_LECTURE",
        )

    def test_rien_ou_deux_fois_la_meme_piece(self):
        with pytest.raises(EcartRefuse, match="aucune pièce"):
            ecarter_en_masse(
                [],
                code_regle="FAC-DOC-011",
                motif=MOTIF,
                motif_type=None,
                par="C-003",
                le=INSTANT,
                politique=_politique(),
                depot=DepotEcartsMemoire(),
            )
        with pytest.raises(EcartRefuse, match="deux fois"):
            _ecarter(DepotEcartsMemoire(), ["F-2026-0415", "F-2026-0415"])

    def test_les_consequences(self):
        politique = _politique(
            par_severite={
                Severite.BLOQUANT: RegleDEcart(ecartable=True, second_regard=False),
                Severite.MAJEUR: RegleDEcart(ecartable=True, second_regard=True),
            }
        )
        depot = DepotEcartsMemoire()
        pieces = _pieces("F-2026-0424", "F-2026-0427")
        ecarts = ecarter_en_masse(
            pieces,
            code_regle="FAC-ID-003",
            motif=MOTIF,
            motif_type=None,
            par="C-003",
            le=INSTANT,
            politique=politique,
            depot=depot,
        )
        consequences = consequences_des_ecarts(pieces, ecarts, politique, depot)
        assert (consequences.ecarts, consequences.effectifs, consequences.en_attente) == (2, 2, 0)
        assert consequences.enjeu_leve == sum((e.enjeu for e in ecarts), Decimal(0)) > 0
        assert consequences.dossiers == sorted({"M081234567890P", "M065544332211L"})
        assert (
            "F-2026-0427" in consequences.pieces_comptabilisables
            or consequences.pieces_comptabilisables
        )
        attente = ecarter_en_masse(
            _pieces("F-2026-0412"),
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            motif_type=None,
            par="C-003",
            le=INSTANT,
            politique=politique,
            depot=depot,
        )
        suite = consequences_des_ecarts(_pieces("F-2026-0412"), attente, politique, depot)
        assert (suite.effectifs, suite.en_attente, suite.enjeu_leve) == (0, 1, Decimal(0))
        assert suite.pieces_comptabilisables == []

    def test_un_ecart_en_attente_ne_rend_pas_la_piece_comptabilisable(self):
        politique = _politique(
            par_severite={Severite.BLOQUANT: RegleDEcart(ecartable=True, second_regard=True)}
        )
        depot = DepotEcartsMemoire()
        pieces = _pieces("F-2026-0427")
        ecarts = ecarter_en_masse(
            pieces,
            code_regle="FAC-ID-003",
            motif=MOTIF,
            motif_type=None,
            par="C-003",
            le=INSTANT,
            politique=politique,
            depot=depot,
        )
        consequences = consequences_des_ecarts(pieces, ecarts, politique, depot)
        assert (consequences.en_attente, consequences.pieces_comptabilisables) == (1, [])

    def test_a_l_unite_aussi_le_motif_type_doit_etre_propose_pour_la_regle(self):
        ((dossier, rapport),) = _pieces("F-2026-0412")
        with pytest.raises(EcartRefuse, match="n'est pas proposé pour la règle FAC-ACH-007"):
            proposer_un_ecart(
                rapport,
                dossier=dossier,
                code_regle="FAC-ACH-007",
                motif=MOTIF,
                par="C-003",
                le=INSTANT,
                politique=_politique(),
                depot=DepotEcartsMemoire(),
                motif_type="BON_DE_LIVRAISON",
            )

    def test_deux_motifs_types_de_meme_code_sont_refuses(self):
        motif = MotifType(code="DOUBLON_X", libelle="Premier motif")
        with pytest.raises(ValidationError, match="en double"):
            PolitiqueDEcart(motifs_types=(motif, motif))


# ── Les routes ────────────────────────────────────────────────────────────────


def _client(courriel):
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


class TestLesRoutes:
    def test_la_vue_d_une_regle_bloquante(self):
        vue = _client(REVISEUR).get(f"/conformite/regles/FAC-ID-003/constats?{JUILLET}").json()
        assert (vue["ecartable"], vue["constats"], vue["adherents"]) == (False, 4, 3)
        assert Decimal(vue["enjeu_cumule"]) == sum(Decimal(l_["enjeu"]) for l_ in vue["lignes"])
        assert {l_["selectionnable"] for l_ in vue["lignes"]} == {False}
        assert "non écartable" in vue["lignes"][0]["raison"]
        assert [m["code"] for m in vue["motifs_types"]] == [
            "NIU_VERIFIE_HORS_FACTURE",
            "ERREUR_DE_LECTURE",
        ]
        # La vérification DGI est rendue telle quelle : radié, actif ou indisponible.
        assert False in {l_["verification_dgi"] for l_ in vue["lignes"]}
        # Les plus gros enjeux d'abord.
        enjeux = [Decimal(l_["enjeu"]) for l_ in vue["lignes"]]
        assert enjeux == sorted(enjeux, reverse=True)

    def test_ecarter_en_masse_puis_relire_la_regle(self):
        reviseur = _client(REVISEUR)
        reponse = reviseur.post(
            "/conformite/regles/FAC-DOC-011/ecarts",
            json={
                "pieces": ["F-2026-0415", "F-2026-0425", "F-2026-0435"],
                "motif_type": "DESIGNATION_PRECISEE",
                "motif": MOTIF,
            },
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["consequences"]["effectifs"] == 3
        vue = reviseur.get(f"/conformite/regles/FAC-DOC-011/constats?{JUILLET}").json()
        assert vue["taux_d_ecartement"] == 1.0
        assert {l_["raison"] for l_ in vue["lignes"]} == {"écart déjà effectif"}
        journal = reviseur.get("/transverse/audit?objet_type=ecart_de_constat").json()
        assert [e["apres"]["motif_type"] for e in journal] == ["DESIGNATION_PRECISEE"] * 3
        # Les comptables des dossiers sont prévenus ; celui d'un autre portefeuille ne l'est pas.
        titres = [
            n["titre"]
            for n in _client(COMPTABLE).get("/transverse/notifications").json()["notifications"]
        ]
        assert (
            "Constat écarté sur F-2026-0435" in titres
            and "Constat écarté sur F-2026-0415" in titres
        )
        autres = [
            n["titre"]
            for n in _client(COMPTABLE_AUTRE)
            .get("/transverse/notifications")
            .json()["notifications"]
        ]
        assert autres == ["Constat écarté sur F-2026-0425"]

    def test_les_refus(self):
        reviseur = _client(REVISEUR)
        refus = reviseur.post(
            "/conformite/regles/FAC-ID-003/ecarts", json={"pieces": ["F-2026-0414"], "motif": MOTIF}
        )
        assert refus.status_code == 409 and "BLOQUANT" in refus.json()["detail"]
        court = reviseur.post(
            "/conformite/regles/FAC-DOC-011/ecarts",
            json={"pieces": ["F-2026-0415"], "motif": "dix lettres"},
        )
        assert court.status_code == 422
        assert (
            reviseur.post(
                "/conformite/regles/INCONNUE/ecarts",
                json={"pieces": ["F-2026-0415"], "motif": MOTIF},
            ).status_code
            == 404
        )
        assert reviseur.get("/conformite/regles/INCONNUE/constats").status_code == 404
        assert (
            reviseur.post(
                "/conformite/regles/FAC-DOC-011/ecarts", json={"pieces": ["F-9999"], "motif": MOTIF}
            ).status_code
            == 404
        )

    def test_la_periode_restreint_les_constats(self):
        vue = (
            _client(REVISEUR)
            .get("/conformite/regles/FAC-DOC-011/constats?du=2026-07-20&au=2026-07-31")
            .json()
        )
        assert sorted(l_["piece"] for l_ in vue["lignes"]) == ["F-2026-0415", "F-2026-0435"]
        assert (vue["du"], vue["au"], vue["constats"]) == ("2026-07-20", "2026-07-31", 2)

    def test_permissions_et_perimetre(self):
        # Le comptable contrôle mais n'écarte pas.
        comptable = _client(COMPTABLE)
        assert (
            comptable.get(f"/conformite/regles/FAC-DOC-011/constats?{JUILLET}").status_code == 200
        )
        assert (
            comptable.post(
                "/conformite/regles/FAC-DOC-011/ecarts",
                json={"pieces": ["F-2026-0435"], "motif": MOTIF},
            ).status_code
            == 403
        )
        # Hors périmètre, la vue ne montre que ses dossiers.
        vue = (
            _client(COMPTABLE_AUTRE)
            .get(f"/conformite/regles/FAC-DOC-011/constats?{JUILLET}")
            .json()
        )
        assert [l_["piece"] for l_ in vue["lignes"]] == ["F-2026-0425"]


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_ecarter_en_masse_et_relire(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        reponse = client.post(
            "/conformite/regles/FAC-DOC-011/ecarts",
            json={"pieces": ["F-2026-0415", "F-2026-0435"], "motif": MOTIF},
        )
        assert reponse.status_code == 200, reponse.text
        vue = client.get(f"/conformite/regles/FAC-DOC-011/constats?{JUILLET}").json()
        assert sum(1 for l_ in vue["lignes"] if l_["raison"] == "écart déjà effectif") == 2
