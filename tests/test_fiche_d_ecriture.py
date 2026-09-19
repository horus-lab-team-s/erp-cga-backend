"""La fiche d'une écriture et sa correction (pas 110). Maquette « Parcours comptable », vue E.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La contre-passation en double**, refusée : deux annulations d'une même facture passaient
   la charge et la TVA en négatif.
2. **L'aperçu** : les mêmes refus que le geste, les lignes inversées, et rien d'écrit.
3. **La TVA nette** : une contre-passation d'achat réduit la TVA déductible du mois où elle est
   passée, une vente annulée la TVA collectée, l'inverse d'un rejet le rejet. Avant le pas 110,
   la déclaration les ignorait.
4. **Les routes** : la fiche et son historique, le signalement au réviseur et sa notification,
   l'impact d'une contre-passation sur la déclaration du mois, les comptes les plus employés.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import vider_les_revues
from app.contextes.comptabilite.api import (
    COMPTES_SYSCOHADA,
    JOURNAUX_CABINET,
    AttributFiscal,
    BrouillonEcriture,
    ContrepassationAntidatee,
    ContrepassationEnDouble,
    DepotEcrituresMemoire,
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    apercevoir_la_contrepassation,
    contrepasser_une_ecriture,
    enregistrer_une_ecriture,
    valider_une_ecriture,
    vider_les_ecritures_en_memoire,
)
from app.contextes.obligations.application.declaration_tva import etablir_declaration_tva
from app.contextes.obligations.application.formulaire_tva import (
    Grandeur,
    formulaire_par_defaut,
    lignes_de_la_declaration,
)
from app.contextes.portefeuille.contrats import Exercice
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

OUVERT = Exercice(libelle="2026", ouverture=date(2026, 1, 1), cloture=date(2026, 12, 31))
LE = datetime(2026, 8, 10, 9)
BATIMENT = "M081234567890P"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_revues()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_revues()
    vider_les_ecritures_en_memoire()


def _l(compte, sens, montant, attribut=None):
    return LigneEcriture(
        compte=compte,
        libelle="x",
        sens=Sens(sens),
        montant=Decimal(montant),
        attribut_fiscal=attribut,
    )


@pytest.fixture
def registre():
    depot = DepotEcrituresMemoire(BATIMENT)
    brouillon = enregistrer_une_ecriture(
        BrouillonEcriture(
            journal="AC",
            exercice="2026",
            date_operation=date(2026, 7, 12),
            libelle="Achat",
            piece_justificative="PJ-1",
            lignes=[
                _l("601", "DEBIT", "1000"),
                _l("4451", "DEBIT", "192"),
                _l("401", "CREDIT", "1192"),
            ],
        ),
        journaux=JOURNAUX_CABINET,
        plan=COMPTES_SYSCOHADA,
        exercice=OUVERT,
        periodes_verrouillees=(),
        depot=depot,
        par="C-004",
    )
    valider_une_ecriture(
        brouillon.cle, exercice=OUVERT, periodes_verrouillees=(), depot=depot, par="C-004", le=LE
    )
    return depot


def _contrepasser(depot, jour=date(2026, 8, 10)):
    return contrepasser_une_ecriture(
        "2026/AC/000001",
        motif="Facture annulée par le fournisseur",
        jour=jour,
        exercice=OUVERT,
        periodes_verrouillees=(),
        depot=depot,
        par="C-004",
    )


def _apercu(depot, jour=date(2026, 8, 10)):
    return apercevoir_la_contrepassation(
        "2026/AC/000001",
        jour=jour,
        exercice=OUVERT,
        periodes_verrouillees=(),
        depot=depot,
        par="C-004",
    )


class TestLaContrepassationEnDouble:
    def test_une_seconde_contrepassation_est_refusee_brouillon_ou_validee(self, registre):
        inverse = _contrepasser(registre)
        with pytest.raises(ContrepassationEnDouble, match="en brouillon, à valider"):
            _contrepasser(registre)
        valider_une_ecriture(
            inverse.cle,
            exercice=OUVERT,
            periodes_verrouillees=(),
            depot=registre,
            par="C-004",
            le=LE,
        )
        with pytest.raises(ContrepassationEnDouble, match=r"2026/AC/000002 \(validée\)"):
            _contrepasser(registre)
        assert len(registre.lister("2026")) == 2


class TestLApercu:
    def test_les_lignes_inversees_sans_rien_ecrire(self, registre):
        apercu = _apercu(registre)
        assert apercu.refus is None and apercu.origine == "2026/AC/000001"
        assert [(l_.compte, l_.sens) for l_ in apercu.inverse.lignes] == [
            ("601", Sens.CREDIT),
            ("4451", Sens.CREDIT),
            ("401", Sens.DEBIT),
        ]
        assert apercu.inverse.numero == 2
        assert len(registre.lister("2026")) == 1

    def test_les_memes_refus_que_le_geste(self, registre):
        antidatee = _apercu(registre, date(2026, 7, 1))
        assert antidatee.inverse is None
        with pytest.raises(ContrepassationAntidatee) as geste:
            _contrepasser(registre, date(2026, 7, 1))
        assert antidatee.refus == str(geste.value)
        _contrepasser(registre)
        assert "déjà contre-passée" in _apercu(registre).refus


def _e(numero, jour, lignes, etat="VALIDEE"):
    valeurs = dict(
        journal="AC",
        exercice="2026",
        numero=numero,
        date_operation=jour,
        libelle="x",
        piece_justificative=f"PJ-{numero}",
        lignes=lignes,
        etat=EtatEcriture(etat),
    )
    if etat == "VALIDEE":
        valeurs.update(validee_par="C-004", validee_le=LE)
    return EcritureComptable(**valeurs)


def _aout(ecritures):
    return etablir_declaration_tva(
        ecritures,
        entreprise="X",
        periode_debut=date(2026, 8, 1),
        periode_fin=date(2026, 8, 31),
    )


class TestLaTVANette:
    def test_une_contrepassation_d_achat_reduit_la_tva_deductible_du_mois(self):
        achat = _e(
            1,
            date(2026, 7, 12),
            [_l("601", "DEBIT", "1000"), _l("4451", "DEBIT", "192"), _l("401", "CREDIT", "1192")],
        )
        annulation = achat.contrepasser(
            numero=2, date_operation=date(2026, 8, 10), motif="Annulée"
        ).valider(par="C-004", le=LE)
        aout = _aout([achat, annulation])
        assert aout.tva_deductible_theorique == Decimal(-192)
        assert aout.solde == Decimal(192)
        biens = next(
            l_
            for l_ in lignes_de_la_declaration(
                [achat, annulation],
                periode_debut=date(2026, 8, 1),
                periode_fin=date(2026, 8, 31),
                tva_rejetee=aout.tva_rejetee,
                ecritures_rejetees=[],
                credit_reporte=Decimal(0),
                formulaire=formulaire_par_defaut(),
            )
            if l_.grandeur is Grandeur.DEDUCTIBLE_BIENS
        )
        assert biens.montant == Decimal(-192) and biens.base == Decimal(-1000)

    def test_une_vente_annulee_reduit_la_tva_collectee(self):
        avoir = _e(
            1,
            date(2026, 8, 3),
            [_l("701", "DEBIT", "500"), _l("4431", "DEBIT", "96"), _l("411", "CREDIT", "596")],
        )
        vente = _e(
            2,
            date(2026, 8, 2),
            [_l("411", "DEBIT", "1192"), _l("701", "CREDIT", "1000"), _l("4431", "CREDIT", "192")],
        )
        assert _aout([vente, avoir]).tva_collectee == Decimal(96)
        lignes = {
            l_.grandeur: l_
            for l_ in lignes_de_la_declaration(
                [vente, avoir],
                periode_debut=date(2026, 8, 1),
                periode_fin=date(2026, 8, 31),
                tva_rejetee=Decimal(0),
                ecritures_rejetees=[],
                credit_reporte=Decimal(0),
                formulaire=formulaire_par_defaut(),
            )
        }
        assert lignes[Grandeur.TVA_COLLECTEE].montant == Decimal(96)
        # L'avoir porte de la TVA collectée : sa base (négative) va aux ventes taxables.
        assert lignes[Grandeur.VENTES_TAXABLES].base == Decimal(500)

    def test_un_attribut_fiscal_au_nom_faux_est_refuse(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AttributFiscal(deductibilite_tva=False)

    def test_l_inverse_d_un_rejet_annule_le_rejet(self):
        rejet = AttributFiscal(
            tva_deductible=False,
            code_regle_origine="FAC-ACH-007",
            motif_non_deductibilite="Espèces au-delà du seuil",
        )
        achat = _e(
            1,
            date(2026, 8, 5),
            [
                _l("601", "DEBIT", "1000"),
                _l("4451", "DEBIT", "192", rejet),
                _l("401", "CREDIT", "1192"),
            ],
        )
        annulation = achat.contrepasser(
            numero=2, date_operation=date(2026, 8, 20), motif="Annulée"
        ).valider(par="C-004", le=LE)
        seul = _aout([achat])
        assert seul.tva_rejetee == Decimal(192) and seul.tva_deductible_admise == 0
        les_deux = _aout([achat, annulation])
        assert les_deux.tva_rejetee == 0 and les_deux.tva_deductible_theorique == 0
        assert sorted(r.montant for r in les_deux.detail_rejets) == [Decimal(-192), Decimal(192)]


# ── Les routes ────────────────────────────────────────────────────────────────

COMPTABLE = "l.fotso@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
CHARGEE = "p.moukouri@cga-brcg.cm"
AUTRE_COMPTABLE = "c.ndongo@cga-brcg.cm"
FICHE = f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/AC/3"


def _client(courriel):
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


class TestLesRoutes:
    def test_la_fiche_le_signalement_et_la_contrepassation(self):
        comptable, reviseur = _client(COMPTABLE), _client(REVISEUR)
        fiche = comptable.get(f"{FICHE}/fiche").json()
        assert fiche["modifiable"] is False and fiche["peut_contrepasser"] is True
        assert [e["quoi"] for e in fiche["historique"]] == ["Saisie en brouillon", "Validée"]
        assert fiche["historique"][0]["quand"] is None

        court = comptable.post(f"{FICHE}/signalement", json={"message": "court"})
        assert court.status_code == 422
        signale = comptable.post(
            f"{FICHE}/signalement", json={"message": "Compte 622 : location ou sous-traitance ?"}
        )
        assert signale.status_code == 201, signale.text
        voisine = comptable.get(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/AC/2/fiche"
        ).json()
        assert not any(e["quoi"].startswith("Signalée") for e in voisine["historique"])
        titres = [
            n["titre"] for n in reviseur.get("/transverse/notifications").json()["notifications"]
        ]
        assert "Écriture signalée : 2026/AC/000003" in titres
        audit = reviseur.get(
            f"/transverse/audit?objet_type=ecriture&objet_id={BATIMENT}:2026/AC/000003"
        ).json()
        assert [e["action"] for e in audit] == ["comptabilite.ecriture_signalee"]
        assert (
            _client(CHARGEE)
            .post(f"{FICHE}/signalement", json={"message": "Compte 622 à revoir svp"})
            .status_code
            == 403
        )
        assert _client(AUTRE_COMPTABLE).get(f"{FICHE}/fiche").status_code == 404

        apercu = comptable.get(
            f"{FICHE}/contre-passation/apercu", params={"date_operation": "2026-08-10"}
        ).json()
        assert apercu["refus"] is None and len(apercu["inverse"]["lignes"]) == 3
        impact = comptable.get(
            f"/obligations/dossiers/{BATIMENT}/impact-contrepassation",
            params={
                "exercice": "2026",
                "journal": "AC",
                "numero": 3,
                "date_operation": "2026-08-10",
            },
        ).json()
        assert impact["touche_la_tva"] and not impact["deja_deposee"]
        assert Decimal(impact["avant"]["credit_a_reporter"]) - Decimal(
            impact["apres"]["credit_a_reporter"]
        ) == Decimal(269500)
        assert "sera recalculée" in impact["message"]

        cree = comptable.post(
            f"{FICHE}/contre-passation",
            json={"motif": "Facture annulée par le fournisseur", "date_operation": "2026-08-10"},
        )
        assert cree.status_code == 201, cree.text
        apres = comptable.get(f"{FICHE}/fiche").json()
        assert apres["peut_contrepasser"] is False
        assert apres["contrepassee_par"] == [f"2026/AC/{cree.json()['numero']:06d}"]
        assert apres["raison_de_ne_pas_contrepasser"].startswith("Déjà contre-passée par")
        quoi = [e["quoi"] for e in apres["historique"]]
        assert any(q.startswith("Contre-passée par 2026/AC/") for q in quoi)
        assert any(q.startswith("Signalée au réviseur : Compte 622") for q in quoi)
        refus = comptable.post(
            f"{FICHE}/contre-passation", json={"motif": "Encore", "date_operation": "2026-08-11"}
        )
        assert refus.status_code == 409 and "l'annulerait deux fois" in refus.json()["detail"]
        refuse = comptable.get(
            f"/obligations/dossiers/{BATIMENT}/impact-contrepassation",
            params={
                "exercice": "2026",
                "journal": "AC",
                "numero": 3,
                "date_operation": "2026-08-10",
            },
        ).json()
        assert "déjà contre-passée" in refuse["refus"] and refuse["avant"] is None

    def test_sans_tva_et_les_comptes_utilises(self):
        comptable = _client(COMPTABLE)
        utilises = comptable.get(
            f"/comptabilite/dossiers/{BATIMENT}/comptes-utilises", params={"exercice": "2026"}
        ).json()
        assert utilises[0] == {"compte": "401", "lignes": 3}
        assert [u["lignes"] for u in utilises] == sorted(
            (u["lignes"] for u in utilises), reverse=True
        )
        saisie = comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures",
            json={
                "journal": "OD",
                "exercice": "2026",
                "date_operation": "2026-07-20",
                "libelle": "Reclassement",
                "piece_justificative": "OD-1",
                "lignes": [
                    {"compte": "604", "libelle": "x", "sens": "DEBIT", "montant": "1000"},
                    {"compte": "601", "libelle": "x", "sens": "CREDIT", "montant": "1000"},
                ],
            },
        ).json()
        comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/OD/{saisie['numero']}/validation"
        )
        apres = [
            u["compte"]
            for u in comptable.get(
                f"/comptabilite/dossiers/{BATIMENT}/comptes-utilises", params={"exercice": "2026"}
            ).json()
        ]
        # 604 employé trois fois passe avant 601, employé une fois : l'ordre est l'emploi, pas le
        # numéro.
        assert apres.index("604") < apres.index("601")
        impact = comptable.get(
            f"/obligations/dossiers/{BATIMENT}/impact-contrepassation",
            params={
                "exercice": "2026",
                "journal": "OD",
                "numero": saisie["numero"],
                "date_operation": "2026-08-10",
            },
        ).json()
        assert impact["touche_la_tva"] is False and impact["avant"] is None
        assert "ne mouvemente pas de TVA" in impact["message"]


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_la_contrepassation_en_double_et_le_signalement_sur_une_vraie_base(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        corps = {"motif": "Facture annulée par le fournisseur", "date_operation": "2026-08-10"}
        assert client.post(f"{FICHE}/contre-passation", json=corps).status_code == 201
        assert client.post(f"{FICHE}/contre-passation", json=corps).status_code == 409
        assert (
            client.post(
                f"{FICHE}/signalement", json={"message": "Voir la contre-passation svp"}
            ).status_code
            == 201
        )
        fiche = client.get(f"{FICHE}/fiche").json()
        assert fiche["peut_contrepasser"] is False
        assert any(e["quoi"].startswith("Signalée au réviseur") for e in fiche["historique"])
