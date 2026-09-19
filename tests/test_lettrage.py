"""Le lettrage des comptes de tiers, et les filtres du grand livre (pas 108).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La lettre** : A à Z, puis AA ; jamais réutilisée, même après un lettrage défait.
2. **Les règles du lettrage** : compte lettrable, écritures validées, lignes du compte, une
   seule fois, même tiers, débit égal au crédit (à l'écart toléré près), lettre reprise
   intouchable.
3. **Le grand livre** : la lettre superposée, son origine, la ligne désignée par son rang, la
   pièce de l'écriture ; des filtres qui ne faussent pas le solde progressif.
4. **Les routes** : lettrer, relire, filtrer, délettrer ; le journal d'audit ; les accès ; la
   balance filtrée par période et par journal ; PostgreSQL.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.depots_lettrages import vider_les_lettrages
from app.contextes.comptabilite.adaptateurs.sortant.lettrage_yaml import (
    charger_les_reglages_du_lettrage,
)
from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    EtatEcriture,
    LigneEcriture,
    Sens,
    vider_les_ecritures_en_memoire,
)
from app.contextes.comptabilite.application.lettrage import lettrages_par_ligne, lettrer
from app.contextes.comptabilite.domaine.entites import EcritureComptable
from app.contextes.comptabilite.domaine.lettrage import (
    LettrageDeLignes,
    LettrageRefuse,
    ReferenceDeLigne,
    ReglagesDuLettrage,
    lettre_de_rang,
)
from app.contextes.comptabilite.domaine.projections import grand_livre
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
CHARGEE_DE_CLIENTELE = "p.moukouri@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
LE = datetime(2026, 8, 10, 9)
REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_lettrages()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_lettrages()
    vider_les_ecritures_en_memoire()


def _ecriture(numero, lignes, *, journal="AC", jour=date(2026, 7, 3), etat="VALIDEE", piece=None):
    valeurs = dict(
        journal=journal,
        exercice="2026",
        numero=numero,
        date_operation=jour,
        libelle=f"écriture {numero}",
        piece_justificative=piece or f"PJ-{numero}",
        lignes=lignes,
        etat=EtatEcriture(etat),
    )
    if etat == "VALIDEE":
        valeurs.update(validee_par="C-004", validee_le=LE)
    return EcritureComptable(**valeurs)


def _l(compte, sens, montant, tiers=None, lettrage=None):
    return LigneEcriture(
        compte=compte,
        libelle="x",
        sens=Sens(sens),
        montant=Decimal(montant),
        tiers=tiers,
        lettrage=lettrage,
    )


def _facture(numero=1, montant="1000", tiers="ALPHA", **kw):
    return _ecriture(
        numero, [_l("601", "DEBIT", montant), _l("401", "CREDIT", montant, tiers)], **kw
    )


def _reglement(numero=1, montant="1000", tiers="ALPHA", **kw):
    return _ecriture(
        numero,
        [_l("401", "DEBIT", montant, tiers), _l("521", "CREDIT", montant)],
        journal="BQ",
        jour=date(2026, 8, 2),
        **kw,
    )


def _ref(cle, rang):
    return ReferenceDeLigne(cle_ecriture=cle, rang=rang)


def _lettrer(ecritures, references, existants=(), reglages=None, compte="401"):
    return lettrer(
        dossier=BATIMENT,
        exercice="2026",
        compte=compte,
        references=references,
        ecritures_de_l_exercice=ecritures,
        plan=COMPTES_SYSCOHADA,
        lettrages_du_compte=list(existants),
        reglages=reglages or ReglagesDuLettrage(),
        par="C-004",
        le=LE,
    )


FACTURE, REGLEMENT = "2026/AC/000001", "2026/BQ/000001"


class TestLaLettre:
    @pytest.mark.parametrize(
        ("rang", "lettre"),
        [(0, "A"), (1, "B"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")],
    )
    def test_comme_les_colonnes_d_un_tableur(self, rang, lettre):
        assert lettre_de_rang(rang) == lettre

    def test_les_reglages_du_referentiel_sont_les_defauts(self, tmp_path):
        lus = charger_les_reglages_du_lettrage(REFERENTIEL)
        assert lus.source == "lettrage/reglages.yaml"
        assert lus.model_dump(exclude={"source"}) == ReglagesDuLettrage().model_dump(
            exclude={"source"}
        )
        assert charger_les_reglages_du_lettrage(tmp_path).source == "valeurs par défaut"
        with pytest.raises(ValidationError):
            ReglagesDuLettrage.model_validate({"ecart_toleres": 5})


class TestLesRegles:
    def test_la_facture_et_son_reglement_se_lettrent(self):
        ecritures = [_facture(), _reglement()]
        lettrage = _lettrer(ecritures, [_ref(REGLEMENT, 0), _ref(FACTURE, 1)])
        assert lettrage.lettre == "A" and lettrage.identifiant == "LET-2026-401-A"
        assert lettrage.total_debit == lettrage.total_credit == Decimal(1000)
        assert lettrage.tiers == "ALPHA"
        assert lettrage.lignes == (_ref(FACTURE, 1), _ref(REGLEMENT, 0))

    @pytest.mark.parametrize(
        ("references", "compte", "message"),
        [
            ([_ref(FACTURE, 0), _ref(REGLEMENT, 1)], "601", "n'est pas lettrable"),
            ([_ref(FACTURE, 1), _ref(FACTURE, 1)], "401", "au moins deux lignes"),
            ([_ref(FACTURE, 1), _ref("2026/BQ/000009", 0)], "401", "inconnue"),
            ([_ref(FACTURE, 1), _ref(REGLEMENT, 7)], "401", "pas de ligne 8"),
            ([_ref(FACTURE, 0), _ref(REGLEMENT, 0)], "401", "est au compte 601"),
        ],
    )
    def test_les_refus(self, references, compte, message):
        with pytest.raises(LettrageRefuse, match=message):
            _lettrer([_facture(), _reglement()], references, compte=compte)

    def test_un_brouillon_ne_se_lettre_pas(self):
        with pytest.raises(LettrageRefuse, match="brouillon"):
            _lettrer(
                [_facture(), _reglement(etat="BROUILLON")], [_ref(FACTURE, 1), _ref(REGLEMENT, 0)]
            )

    def test_la_selection_doit_se_solder_a_l_ecart_tolere_pres(self):
        ecritures = [_facture(montant="1000"), _reglement(montant="998")]
        with pytest.raises(LettrageRefuse, match="écart de 2 FCFA"):
            _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])
        tolerant = ReglagesDuLettrage(ecart_tolere=Decimal(2))
        assert _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)], reglages=tolerant).lettre

    def test_deux_tiers_differents_ne_se_lettrent_pas_sauf_reglage(self):
        ecritures = [_facture(tiers="ALPHA"), _reglement(tiers="BETA")]
        with pytest.raises(LettrageRefuse, match="tiers différents \\(ALPHA, BETA\\)"):
            _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])
        souple = ReglagesDuLettrage(meme_tiers_exige=False)
        lettrage = _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)], reglages=souple)
        assert lettrage.tiers is None

    def test_une_ligne_ne_se_lettre_qu_une_fois_et_une_lettre_ne_revient_jamais(self):
        ecritures = [_facture(), _reglement(), _reglement(numero=2)]
        a = _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])
        with pytest.raises(LettrageRefuse, match="déjà lettrée A"):
            _lettrer(ecritures, [_ref(FACTURE, 1), _ref("2026/BQ/000002", 0)], existants=[a])
        defait = a.defaire(par="C-004", le=LE)
        with pytest.raises(LettrageRefuse, match="déjà défait"):
            defait.defaire(par="C-004", le=LE)
        b = _lettrer(ecritures, [_ref(FACTURE, 1), _ref("2026/BQ/000002", 0)], existants=[defait])
        assert b.lettre == "B"

    def test_une_meme_ligne_deux_fois_est_refusee_par_l_entite(self):
        lettrage = _lettrer([_facture(), _reglement()], [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])
        with pytest.raises(ValidationError, match="deux fois"):
            LettrageDeLignes.model_validate(
                {**lettrage.model_dump(), "lignes": [_ref(FACTURE, 1), _ref(FACTURE, 1)]}
            )

    def test_une_lettre_reprise_ne_se_relettre_pas(self):
        reprise = _ecriture(
            1, [_l("601", "DEBIT", "1000"), _l("401", "CREDIT", "1000", "ALPHA", "ZZ")]
        )
        with pytest.raises(LettrageRefuse, match="lettre ZZ reprise"):
            _lettrer([reprise, _reglement()], [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])


class TestLeGrandLivre:
    def test_la_lettre_son_origine_le_rang_et_la_piece(self):
        double = _ecriture(
            2,
            [
                _l("401", "CREDIT", "300", "ALPHA"),
                _l("601", "DEBIT", "1000"),
                _l("401", "CREDIT", "700", "ALPHA", "R"),
            ],
            jour=date(2026, 7, 5),
            piece="F-2026-0999",
        )
        ecritures = [_facture(), _reglement(), double]
        a = _lettrer(ecritures, [_ref(FACTURE, 1), _ref(REGLEMENT, 0)])
        lignes = grand_livre(ecritures, "401", lettrages=lettrages_par_ligne([a]))
        vues = [(l_.cle_ecriture, l_.rang, l_.lettrage, l_.lettrage_origine) for l_ in lignes]
        assert vues == [
            (FACTURE, 1, "A", "PLATEFORME"),
            ("2026/AC/000002", 0, None, None),
            ("2026/AC/000002", 2, "R", "REPRISE"),
            (REGLEMENT, 0, "A", "PLATEFORME"),
        ]
        assert lignes[0].lettrage_identifiant == "LET-2026-401-A"
        assert lignes[1].piece_justificative == "F-2026-0999"
        # Défait, le lettrage ne lettre plus rien.
        defait = a.defaire(par="C-004", le=LE)
        assert all(
            l_.lettrage is None
            for l_ in grand_livre(ecritures, "401", lettrages=lettrages_par_ligne([defait]))
            if l_.cle_ecriture in {FACTURE, REGLEMENT}
        )


# ── Les routes ────────────────────────────────────────────────────────────────


def _client(courriel):
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


ECRITURES = f"/comptabilite/dossiers/{BATIMENT}/ecritures"
GRAND_LIVRE = f"/comptabilite/dossiers/{BATIMENT}/grand-livre/401"
LETTRAGES = f"/comptabilite/dossiers/{BATIMENT}/lettrages"


def _saisir_et_valider(client, journal, jour, lignes, piece):
    corps = {
        "journal": journal,
        "exercice": "2026",
        "date_operation": jour,
        "libelle": f"{journal} {piece}",
        "piece_justificative": piece,
        "lignes": lignes,
    }
    saisie = client.post(ECRITURES, json=corps)
    assert saisie.status_code == 201, saisie.text
    cle = saisie.json()
    validee = client.post(f"{ECRITURES}/2026/{journal}/{cle['numero']}/validation")
    assert validee.status_code == 200, validee.text
    return f"2026/{journal}/{cle['numero']:06d}"


def _ligne(compte, sens, montant, tiers=None):
    return {"compte": compte, "libelle": "x", "sens": sens, "montant": montant, "tiers": tiers}


@pytest.fixture
def facture_et_reglement():
    comptable = _client(COMPTABLE)
    facture = _saisir_et_valider(
        comptable,
        "AC",
        "2026-07-20",
        [_ligne("601", "DEBIT", "236000"), _ligne("401", "CREDIT", "236000", "T-WOURI")],
        "F-LETTRE-1",
    )
    reglement = _saisir_et_valider(
        comptable,
        "BQ",
        "2026-08-03",
        [_ligne("401", "DEBIT", "236000", "T-WOURI"), _ligne("521", "CREDIT", "236000")],
        "VIR-LETTRE-1",
    )
    return comptable, facture, reglement


class TestLesRoutes:
    def test_lettrer_relire_filtrer_puis_delettrer(self, facture_et_reglement):
        comptable, facture, reglement = facture_et_reglement
        avant = comptable.get(GRAND_LIVRE, params={"exercice": "2026"}).json()
        miennes = [l_ for l_ in avant if l_["cle_ecriture"] in {facture, reglement}]
        assert [(l_["rang"], l_["lettrage"]) for l_ in miennes] == [(1, None), (0, None)]
        assert miennes[0]["piece_justificative"] == "F-LETTRE-1"
        solde_final = avant[-1]["solde_progressif"]

        lignes = [{"cle_ecriture": facture, "rang": 1}, {"cle_ecriture": reglement, "rang": 0}]
        fait = comptable.post(
            LETTRAGES, json={"exercice": "2026", "compte": "401", "lignes": lignes}
        )
        assert fait.status_code == 201, fait.text
        lettrage = fait.json()
        assert lettrage["lettre"] == "A" and lettrage["tiers"] == "T-WOURI"

        apres = comptable.get(GRAND_LIVRE, params={"exercice": "2026"}).json()
        lettrees = [l_ for l_ in apres if l_["lettrage_origine"] == "PLATEFORME"]
        assert {l_["cle_ecriture"] for l_ in lettrees} == {facture, reglement}
        assert {l_["lettrage_identifiant"] for l_ in lettrees} == {lettrage["identifiant"]}
        filtre = comptable.get(
            GRAND_LIVRE, params={"exercice": "2026", "lettrage": "NON_LETTREES"}
        ).json()
        assert not {l_["cle_ecriture"] for l_ in filtre} & {facture, reglement}
        assert len(filtre) == len(apres) - 2
        seules = comptable.get(GRAND_LIVRE, params={"exercice": "2026", "lettrage": "LETTREES"})
        assert [l_["cle_ecriture"] for l_ in seules.json()] == [facture, reglement]

        # Filtrer ne recalcule pas le solde : la dernière ligne d'août garde le solde du compte.
        aout = comptable.get(
            GRAND_LIVRE, params={"exercice": "2026", "du": "2026-08-01", "journal": "BQ"}
        ).json()
        assert [l_["cle_ecriture"] for l_ in aout] == [reglement]
        assert aout[0]["solde_progressif"] == solde_final
        achats = comptable.get(GRAND_LIVRE, params={"exercice": "2026", "journal": "AC"}).json()
        assert facture in {l_["cle_ecriture"] for l_ in achats}
        assert reglement not in {l_["cle_ecriture"] for l_ in achats}
        # Des mouvements, aucun dans la période : une liste vide, pas « aucun mouvement ».
        vide = comptable.get(GRAND_LIVRE, params={"exercice": "2026", "du": "2026-12-01"})
        assert vide.status_code == 200 and vide.json() == []

        audit = _client(REVISEUR).get("/transverse/audit?objet_type=lettrage").json()
        assert [e["action"] for e in audit] == ["comptabilite.lettrage_fait"]

        defaire = f"{LETTRAGES}/{lettrage['identifiant']}/delettrage"
        assert comptable.post(defaire, params={"exercice": "2026"}).json()["statut"] == "DEFAIT"
        again = comptable.post(defaire, params={"exercice": "2026"})
        assert again.status_code == 422 and "déjà défait" in again.json()["detail"]
        assert (
            comptable.post(f"{LETTRAGES}/LET-X/delettrage", params={"exercice": "2026"}).status_code
            == 404
        )
        rouverts = comptable.get(
            GRAND_LIVRE, params={"exercice": "2026", "lettrage": "NON_LETTREES"}
        ).json()
        assert {facture, reglement} <= {l_["cle_ecriture"] for l_ in rouverts}
        relettre = comptable.post(
            LETTRAGES, json={"exercice": "2026", "compte": "401", "lignes": lignes}
        )
        assert relettre.json()["lettre"] == "B"
        actions = [
            e["action"]
            for e in _client(REVISEUR).get("/transverse/audit?objet_type=lettrage").json()
        ]
        assert sorted(actions) == sorted(
            [
                "comptabilite.lettrage_fait",
                "comptabilite.lettrage_defait",
                "comptabilite.lettrage_fait",
            ]
        )

    def test_les_refus_et_les_acces(self, facture_et_reglement):
        comptable, facture, reglement = facture_et_reglement
        lignes = [{"cle_ecriture": facture, "rang": 0}, {"cle_ecriture": reglement, "rang": 1}]
        corps = {"exercice": "2026", "compte": "401", "lignes": lignes}
        refus = comptable.post(LETTRAGES, json=corps)
        assert refus.status_code == 422 and "est au compte 601" in refus.json()["detail"]
        bon = {
            **corps,
            "lignes": [
                {"cle_ecriture": facture, "rang": 1},
                {"cle_ecriture": reglement, "rang": 0},
            ],
        }
        assert _client(CHARGEE_DE_CLIENTELE).post(LETTRAGES, json=bon).status_code == 403
        assert _client(COMPTABLE_AUTRE).post(LETTRAGES, json=bon).status_code == 404
        # La chargée de clientèle lit le grand livre, elle ne lettre pas.
        assert (
            _client(CHARGEE_DE_CLIENTELE).get(GRAND_LIVRE, params={"exercice": "2026"}).status_code
            == 200
        )

    def test_la_balance_par_periode_et_par_journal(self, facture_et_reglement):
        comptable, _facture_cle, _reglement_cle = facture_et_reglement
        balance = f"/comptabilite/dossiers/{BATIMENT}/balance"

        def compte(params, numero):
            soldes = comptable.get(balance, params={"exercice": "2026", **params}).json()
            return next((s for s in soldes if s["compte"] == numero), None)

        banque = compte({"journal": "BQ"}, "401")
        assert banque["total_debit"] == "236000" and banque["total_credit"] == "0"
        assert compte({"journal": "BQ"}, "601") is None
        assert compte({"du": "2026-08-01"}, "601") is None
        sante = comptable.get(
            f"/comptabilite/dossiers/{BATIMENT}/sante", params={"exercice": "2026", "journal": "BQ"}
        ).json()
        assert sante["total_debit"] == sante["total_credit"] == "236000"
        assert sante["equilibree"] is True
        tout = comptable.get(
            f"/comptabilite/dossiers/{BATIMENT}/sante", params={"exercice": "2026"}
        ).json()
        assert Decimal(tout["total_debit"]) > Decimal(236000)
        juillet = compte({"du": "2026-07-01", "jusqu_au": "2026-07-31"}, "401")
        assert Decimal(juillet["total_credit"]) >= Decimal(236000)
        assert juillet["total_debit"] == "0"


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_lettrer_et_relire_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        facture = _saisir_et_valider(
            client,
            "AC",
            "2026-07-20",
            [_ligne("601", "DEBIT", "5000"), _ligne("401", "CREDIT", "5000", "T-PG")],
            "F-PG-1",
        )
        reglement = _saisir_et_valider(
            client,
            "BQ",
            "2026-08-03",
            [_ligne("401", "DEBIT", "5000", "T-PG"), _ligne("521", "CREDIT", "5000")],
            "VIR-PG-1",
        )
        lignes = [{"cle_ecriture": facture, "rang": 1}, {"cle_ecriture": reglement, "rang": 0}]
        fait = client.post(LETTRAGES, json={"exercice": "2026", "compte": "401", "lignes": lignes})
        assert fait.status_code == 201, fait.text
        # Un autre compte lettrable a sa propre suite de lettres : le premier lettrage du 411 est
        # A, même si le 401 en a déjà un.
        vente = _saisir_et_valider(
            client,
            "VE",
            "2026-07-21",
            [_ligne("411", "DEBIT", "7000", "C-PG"), _ligne("701", "CREDIT", "7000")],
            "FV-PG-1",
        )
        encaissement = _saisir_et_valider(
            client,
            "BQ",
            "2026-08-04",
            [_ligne("521", "DEBIT", "7000"), _ligne("411", "CREDIT", "7000", "C-PG")],
            "ENC-PG-1",
        )
        client_lettre = client.post(
            LETTRAGES,
            json={
                "exercice": "2026",
                "compte": "411",
                "lignes": [
                    {"cle_ecriture": vente, "rang": 0},
                    {"cle_ecriture": encaissement, "rang": 1},
                ],
            },
        )
        assert client_lettre.status_code == 201, client_lettre.text
        assert client_lettre.json()["lettre"] == "A"
        relu = client.get(GRAND_LIVRE, params={"exercice": "2026", "lettrage": "LETTREES"}).json()
        assert [l_["lettrage"] for l_ in relu] == ["A", "A"]
        defait = client.post(
            f"{LETTRAGES}/{fait.json()['identifiant']}/delettrage", params={"exercice": "2026"}
        )
        assert defait.status_code == 200, defait.text
        assert (
            client.get(GRAND_LIVRE, params={"exercice": "2026", "lettrage": "LETTREES"}).json()
            == []
        )
