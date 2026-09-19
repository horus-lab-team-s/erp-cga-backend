"""La préparation d'une déclaration de TVA (pas 109). Maquette « Parcours comptable », vue D.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Les lignes du formulaire** : chaque grandeur, sa base, ses écritures ; la base partielle
   d'une écriture mêlant deux catégories ; un formulaire au référentiel qui refuse les doublons.
2. **Le crédit reporté**, enchaîné de mois en mois, et non plus reçu de la requête (le défaut du
   pas 109 : il valait zéro, et le crédit d'un mois se perdait le suivant).
3. **La complétude** : les pièces en souffrance, jamais passées jusqu'ici, et les pièces attendues.
4. **La revue du mois exigée avant le dépôt** : absente, renvoyée, transmise, validée ; bloquante
   ou réserve selon le référentiel.
5. **Les routes**, et PostgreSQL.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import vider_les_revues
from app.contextes.comptabilite.api import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    vider_les_ecritures_en_memoire,
)
from app.contextes.obligations.adaptateurs.sortant.declaration_tva_yaml import (
    charger_le_formulaire_tva,
    charger_les_reglages_du_depot_tva,
)
from app.contextes.obligations.application.declaration_tva import (
    credit_reporte_au_debut_de,
    etablir_declaration_tva,
    mois_declarables_avant,
)
from app.contextes.obligations.application.depot import (
    CompletudeDeLaPeriode,
    ExigenceDeRevue,
    ReglagesDuDepotTVA,
    RevueDeLaPeriode,
    _controler_la_revue,
)
from app.contextes.obligations.application.formulaire_tva import (
    FormulaireTVA,
    Grandeur,
    LigneDuFormulaire,
    formulaire_par_defaut,
    lignes_de_la_declaration,
)
from app.contextes.obligations.domaine.recevabilite import NiveauRecevabilite
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"
LE = datetime(2026, 8, 10, 9)
JUILLET = (date(2026, 7, 1), date(2026, 7, 31))


def _l(compte, sens, montant):
    return LigneEcriture(compte=compte, libelle="x", sens=Sens(sens), montant=Decimal(montant))


def _e(numero, jour, lignes, journal="AC", etat="VALIDEE"):
    valeurs = dict(
        journal=journal,
        exercice="2026",
        numero=numero,
        date_operation=jour,
        libelle=f"écriture {numero}",
        piece_justificative=f"PJ-{numero}",
        lignes=lignes,
        etat=EtatEcriture(etat),
    )
    if etat == "VALIDEE":
        valeurs.update(validee_par="C-004", validee_le=LE)
    return EcritureComptable(**valeurs)


def _ecritures_de_juillet():
    return [
        # Vente taxable : base 1 000, TVA 192.
        _e(
            1,
            date(2026, 7, 2),
            [_l("411", "DEBIT", "1192"), _l("701", "CREDIT", "1000"), _l("4431", "CREDIT", "192")],
            "VE",
        ),
        # Vente exonérée : base 300.
        _e(2, date(2026, 7, 3), [_l("411", "DEBIT", "300"), _l("706", "CREDIT", "300")], "VE"),
        # Achat de biens : 550 moins un rabais de 50 porté au 609, base nette 500, TVA 96.
        _e(
            3,
            date(2026, 7, 4),
            [
                _l("601", "DEBIT", "550"),
                _l("609", "CREDIT", "50"),
                _l("4451", "DEBIT", "96"),
                _l("401", "CREDIT", "596"),
            ],
        ),
        # Service : base 200, TVA 38.
        _e(
            4,
            date(2026, 7, 5),
            [_l("622", "DEBIT", "200"), _l("4454", "DEBIT", "38"), _l("401", "CREDIT", "238")],
        ),
        # Immobilisation : base 2 000, TVA 385.
        _e(
            5,
            date(2026, 7, 6),
            [_l("2441", "DEBIT", "2000"), _l("4452", "DEBIT", "385"), _l("401", "CREDIT", "2385")],
        ),
        # Écriture mêlant biens et services : TVA ventilée, base non répartie.
        _e(
            6,
            date(2026, 7, 7),
            [
                _l("601", "DEBIT", "100"),
                _l("4451", "DEBIT", "19"),
                _l("622", "DEBIT", "100"),
                _l("4454", "DEBIT", "19"),
                _l("401", "CREDIT", "238"),
            ],
        ),
        # Un brouillon et une écriture d'août : ignorés.
        _e(
            7,
            date(2026, 7, 8),
            [_l("601", "DEBIT", "9000"), _l("4451", "DEBIT", "1000"), _l("401", "CREDIT", "10000")],
            etat="BROUILLON",
        ),
        _e(
            8,
            date(2026, 8, 1),
            [_l("601", "DEBIT", "9000"), _l("4451", "DEBIT", "1000"), _l("401", "CREDIT", "10000")],
        ),
    ]


def _lignes(ecritures, formulaire=None, rejet=Decimal(0), credit=Decimal(0)):
    return {
        l_.grandeur: l_
        for l_ in lignes_de_la_declaration(
            ecritures,
            periode_debut=JUILLET[0],
            periode_fin=JUILLET[1],
            tva_rejetee=rejet,
            ecritures_rejetees=["2026/AC/000003"] if rejet else [],
            credit_reporte=credit,
            formulaire=formulaire or formulaire_par_defaut(),
        )
    }


class TestLesLignes:
    def test_chaque_grandeur_sa_base_et_ses_ecritures(self):
        lignes = _lignes(_ecritures_de_juillet(), rejet=Decimal(96), credit=Decimal(50))
        taxables = lignes[Grandeur.VENTES_TAXABLES]
        assert (taxables.code, taxables.base, taxables.montant) == (
            "L01",
            Decimal(1000),
            Decimal(192),
        )
        assert taxables.ecritures == ["2026/VE/000001"]
        exonerees = lignes[Grandeur.VENTES_EXONEREES]
        assert (exonerees.base, exonerees.montant, exonerees.ecritures) == (
            Decimal(300),
            None,
            ["2026/VE/000002"],
        )
        assert lignes[Grandeur.TVA_COLLECTEE].montant == Decimal(192)
        assert lignes[Grandeur.TVA_COLLECTEE].base is None
        biens = lignes[Grandeur.DEDUCTIBLE_BIENS]
        assert (biens.base, biens.montant) == (Decimal(500), Decimal(115))
        assert biens.ecritures == ["2026/AC/000003", "2026/AC/000006"] and biens.base_partielle
        services = lignes[Grandeur.DEDUCTIBLE_SERVICES]
        assert (services.base, services.montant, services.base_partielle) == (
            Decimal(200),
            Decimal(57),
            True,
        )
        immobilisations = lignes[Grandeur.DEDUCTIBLE_IMMOBILISATIONS]
        assert (immobilisations.base, immobilisations.montant, immobilisations.base_partielle) == (
            Decimal(2000),
            Decimal(385),
            False,
        )
        assert lignes[Grandeur.DEDUCTIBLE_AUTRES].montant == 0
        assert lignes[Grandeur.TVA_REJETEE].montant == Decimal(96)
        assert lignes[Grandeur.TVA_REJETEE].ecritures == ["2026/AC/000003"]
        assert lignes[Grandeur.CREDIT_REPORTE].montant == Decimal(50)

    def test_la_somme_des_deductibles_est_celle_du_decompte(self):
        ecritures = _ecritures_de_juillet()
        lignes = _lignes(ecritures)
        decompte = etablir_declaration_tva(
            ecritures, entreprise="X", periode_debut=JUILLET[0], periode_fin=JUILLET[1]
        )
        deductibles = [
            Grandeur.DEDUCTIBLE_BIENS,
            Grandeur.DEDUCTIBLE_SERVICES,
            Grandeur.DEDUCTIBLE_IMMOBILISATIONS,
            Grandeur.DEDUCTIBLE_AUTRES,
        ]
        assert sum(lignes[g].montant for g in deductibles) == decompte.tva_deductible_theorique
        assert lignes[Grandeur.TVA_COLLECTEE].montant == decompte.tva_collectee

    def test_le_formulaire_du_referentiel_et_ses_refus(self):
        lu = charger_le_formulaire_tva(REFERENTIEL)
        assert lu.source == "obligations/formulaire_tva.yaml"
        assert lu.model_dump(exclude={"source"}) == formulaire_par_defaut().model_dump(
            exclude={"source"}
        )
        ligne = LigneDuFormulaire(code="L01", grandeur=Grandeur.VENTES_TAXABLES, libelle="Ventes")
        with pytest.raises(ValidationError, match="qu'une fois"):
            FormulaireTVA(lignes=(ligne, ligne.model_copy(update={"code": "L02"})))
        with pytest.raises(ValidationError):
            FormulaireTVA.model_validate(
                {"lignes": [{"code": "L9", "grandeur": "INVENTEE", "libelle": "Rien"}]}
            )
        reduit = FormulaireTVA(lignes=(ligne,))
        assert list(_lignes(_ecritures_de_juillet(), formulaire=reduit)) == [
            Grandeur.VENTES_TAXABLES
        ]


class TestLeCreditReporte:
    def test_il_s_enchaine_de_mois_en_mois(self):
        ecritures = [
            # Mai : 500 de TVA déductible, rien de collecté → crédit 500.
            _e(
                1,
                date(2026, 5, 10),
                [
                    _l("601", "DEBIT", "2600"),
                    _l("4451", "DEBIT", "500"),
                    _l("401", "CREDIT", "3100"),
                ],
            ),
            # Juin : 300 collectés → 300 imputés sur le crédit, reste 200.
            _e(
                1,
                date(2026, 6, 10),
                [
                    _l("411", "DEBIT", "1860"),
                    _l("701", "CREDIT", "1560"),
                    _l("4431", "CREDIT", "300"),
                ],
                "VE",
            ),
        ]
        mai = (date(2026, 5, 1), date(2026, 5, 31))
        juin = (date(2026, 6, 1), date(2026, 6, 30))
        assert credit_reporte_au_debut_de([], ecritures) == 0
        assert credit_reporte_au_debut_de([mai], ecritures) == Decimal(500)
        assert credit_reporte_au_debut_de([juin, mai], ecritures) == Decimal(200)


class TestLesMoisDeclarables:
    def test_seuls_les_mois_assujettis_et_connus(self):
        def assujettie_au(fin):
            if fin < date(2026, 3, 1):
                raise LookupError("aucun régime connu")
            return fin.month != 5

        mois = mois_declarables_avant(date(2026, 1, 15), date(2026, 7, 1), assujettie_au)
        assert mois == [
            (date(2026, 3, 1), date(2026, 3, 31)),
            (date(2026, 4, 1), date(2026, 4, 30)),
            (date(2026, 6, 1), date(2026, 6, 30)),
        ]
        assert mois_declarables_avant(date(2026, 7, 1), date(2026, 7, 1), assujettie_au) == []


class TestLaRevueExigee:
    @pytest.mark.parametrize(
        ("exigee", "statut", "bloque"),
        [
            (ExigenceDeRevue.AUCUNE, None, False),
            (ExigenceDeRevue.TRANSMISE, None, True),
            (ExigenceDeRevue.TRANSMISE, "RENVOYEE", True),
            (ExigenceDeRevue.TRANSMISE, "TRANSMISE", False),
            (ExigenceDeRevue.TRANSMISE, "VALIDEE", False),
            (ExigenceDeRevue.VALIDEE, "TRANSMISE", True),
            (ExigenceDeRevue.VALIDEE, "VALIDEE", False),
        ],
    )
    def test_ce_qui_suffit(self, exigee, statut, bloque):
        anomalie = _controler_la_revue(
            RevueDeLaPeriode(identifiant="REV-1" if statut else None, statut=statut),
            ReglagesDuDepotTVA(revue_du_mois_exigee=exigee),
        )
        assert (anomalie is not None) is bloque
        if anomalie:
            assert (
                anomalie.code == "REVUE-DU-MOIS" and anomalie.niveau is NiveauRecevabilite.BLOQUANT
            )

    def test_le_message_et_les_reglages(self):
        renvoyee = _controler_la_revue(
            RevueDeLaPeriode(identifiant="REV-1", statut="RENVOYEE"), ReglagesDuDepotTVA()
        )
        assert "transmis au réviseur" in renvoyee.libelle and "renvoyee" in renvoyee.libelle
        assert renvoyee.reference == "REV-1"
        reserve = _controler_la_revue(
            RevueDeLaPeriode(), ReglagesDuDepotTVA(niveau_si_manquante=NiveauRecevabilite.RESERVE)
        )
        assert reserve.niveau is NiveauRecevabilite.RESERVE
        with pytest.raises(ValidationError, match="BLOQUANT ou RESERVE"):
            ReglagesDuDepotTVA(niveau_si_manquante=NiveauRecevabilite.INFORMATION)
        lus = charger_les_reglages_du_depot_tva(REFERENTIEL)
        assert lus.source == "obligations/depot_tva.yaml"
        assert lus.model_dump(exclude={"source"}) == ReglagesDuDepotTVA().model_dump(
            exclude={"source"}
        )

    def test_la_completude(self):
        assert CompletudeDeLaPeriode(pieces_recues=0).taux is None
        partielle = CompletudeDeLaPeriode(
            pieces_recues=7, pieces_en_souffrance=["A", "B", "C", "D"]
        )
        assert (partielle.pieces_traitees, partielle.taux) == (3, 0.4286)


# ── Les routes ────────────────────────────────────────────────────────────────

BATIMENT = "M081234567890P"
AGRO = "M065544332211L"
COMPTABLE = "l.fotso@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_revues()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_revues()
    vider_les_ecritures_en_memoire()


def _client(courriel):
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _declaration(client, niu, debut, fin, **extra):
    reponse = client.get(
        f"/obligations/dossiers/{niu}/declaration-tva",
        params={"periode_debut": debut, "periode_fin": fin, **extra},
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


def _depot(client, niu="M081234567890P"):
    reponse = client.get(
        f"/obligations/dossiers/{niu}/depot-tva",
        params={
            "periode_debut": "2026-07-01",
            "periode_fin": "2026-07-31",
            "a_la_date": "2026-08-10",
        },
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()["recevabilite"]


class TestLesRoutes:
    def test_la_declaration_porte_ses_lignes_sa_completude_et_sa_revue(self):
        comptable = _client(COMPTABLE)
        juillet = _declaration(comptable, BATIMENT, "2026-07-01", "2026-07-31")
        assert [l_["code"] for l_ in juillet["lignes"]] == [
            "L01",
            "L04",
            "L10",
            "L20",
            "L21",
            "L22",
            "L23",
            "L24",
            "L30",
        ]
        biens = next(l_ for l_ in juillet["lignes"] if l_["code"] == "L20")
        assert Decimal(biens["montant"]) == Decimal(juillet["tva_deductible_theorique"])
        assert len(biens["ecritures"]) == 3
        rejet = next(l_ for l_ in juillet["lignes"] if l_["code"] == "L24")
        assert Decimal(rejet["montant"]) == Decimal(juillet["tva_rejetee"]) > 0
        assert juillet["completude"]["pieces_en_souffrance"] == [
            "PJ-2026-0013",
            "PJ-2026-0019",
            "PJ-2026-0024",
            "PJ-2026-0900",
        ]
        assert juillet["completude"]["pieces_recues"] == 7
        assert juillet["revue"] == {"identifiant": None, "statut": None}
        agro = _declaration(comptable, AGRO, "2026-07-01", "2026-07-31")
        assert agro["completude"]["pieces_attendues"] == ["DP-2026-011"]
        attendues = next(
            a for a in _depot(comptable, AGRO)["reserves"] if a["code"] == "PIECES-ATTENDUES"
        )
        assert attendues["reference"] == "DP-2026-011"

    def test_le_credit_de_juillet_passe_en_aout_et_ne_se_recoit_plus(self):
        comptable = _client(COMPTABLE)
        juillet = _declaration(comptable, BATIMENT, "2026-07-01", "2026-07-31")
        assert Decimal(juillet["credit_a_reporter"]) > 0
        aout = _declaration(
            comptable, BATIMENT, "2026-08-01", "2026-08-31", credit_reporte_anterieur="999999"
        )
        assert aout["credit_reporte_anterieur"] == juillet["credit_a_reporter"]
        l30 = next(l_ for l_ in aout["lignes"] if l_["code"] == "L30")
        assert l30["montant"] == juillet["credit_a_reporter"]

    def test_le_depot_exige_le_mois_transmis(self, monkeypatch):
        comptable = _client(COMPTABLE)
        # La revue d'un autre mois ne couvre pas juillet.
        from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues
        from app.contextes.comptabilite.domaine.revue import (
            PassageDeRelais,
            RevueDeDossier,
            StatutRevue,
        )
        from app.partage.locataire import etabli

        with etabli("CGA-BRCG"):
            depot_des_revues().enregistrer(
                RevueDeDossier(
                    identifiant="REV-20260601-20260630",
                    dossier=BATIMENT,
                    exercice="2026",
                    du=date(2026, 6, 1),
                    au=date(2026, 6, 30),
                    statut=StatutRevue.TRANSMISE,
                    transmise_par_compte="C-004",
                    transmise_par="Léonard FOTSO",
                    echantillon=[],
                    ecritures_du_mois=1,
                    historique=[
                        PassageDeRelais(
                            statut=StatutRevue.TRANSMISE, par="Léonard FOTSO", compte="C-004", le=LE
                        )
                    ],
                )
            )
        avant = _depot(comptable)
        revue = next(a for a in avant["bloquants"] if a["code"] == "REVUE-DU-MOIS")
        assert "n'a pas été transmis" in revue["libelle"]
        assert {"PIECES-NON-TRAITEES"} <= {a["code"] for a in avant["reserves"]}

        from app.contextes.comptabilite.adaptateurs.entrant import routes_cloture_mensuelle
        from app.contextes.comptabilite.domaine.cloture_mensuelle import (
            CodePoint,
            ReglagesDeLaClotureMensuelle,
        )

        informatifs = ReglagesDeLaClotureMensuelle(
            points={c.value: {"bloquant": c is CodePoint.BROUILLONS} for c in CodePoint}
        )
        monkeypatch.setattr(routes_cloture_mensuelle, "_reglages", lambda: informatifs)
        transmise = comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/revues",
            json={"du": "2026-07-01", "au": "2026-07-31"},
        )
        assert transmise.status_code == 201, transmise.text
        apres = _depot(comptable)
        assert "REVUE-DU-MOIS" not in {a["code"] for a in apres["anomalies"]}
        assert (
            _declaration(comptable, BATIMENT, "2026-07-01", "2026-07-31")["revue"]["statut"]
            == "TRANSMISE"
        )

        from app.contextes.obligations.adaptateurs.entrant import routes_http

        exigeant = ReglagesDuDepotTVA(
            revue_du_mois_exigee=ExigenceDeRevue.VALIDEE,
            niveau_si_manquante=NiveauRecevabilite.RESERVE,
        )
        monkeypatch.setattr(
            routes_http, "charger_les_reglages_du_depot_tva", lambda _chemin: exigeant
        )
        reserve = _depot(comptable)
        assert "REVUE-DU-MOIS" in {a["code"] for a in reserve["reserves"]}
        assert "REVUE-DU-MOIS" not in {a["code"] for a in reserve["bloquants"]}


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_le_credit_s_enchaine_sur_une_vraie_base(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        juillet = _declaration(client, BATIMENT, "2026-07-01", "2026-07-31")
        aout = _declaration(client, BATIMENT, "2026-08-01", "2026-08-31")
        assert aout["credit_reporte_anterieur"] == juillet["credit_a_reporter"]
        assert _depot(client)["bloquants"][0]["code"] == "REVUE-DU-MOIS"
