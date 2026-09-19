"""La revue d'un mois transmis (pas 102).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **Le circuit** : transmis, renvoyé avec des remarques, répondu, retransmis, validé ; et
   chaque geste refusé hors de son état.
2. **Les quatre yeux** : celui qui a transmis ne valide pas, même réviseur.
3. **La remarque rattachée à un objet réel** de la période : écriture, pièce ou compte.
4. **L'échantillon** : ses critères, son ordre, son plafond, figé à la transmission et
   recalculé à la retransmission.
5. **Les points de contrôle**, dont le rapprochement bancaire arrêté du pas 101.
6. **Les accès et les notifications** : la file du réviseur, le renvoi qui arrive au comptable.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import (
    DepotRevuesMemoire,
    vider_les_revues,
)
from app.contextes.comptabilite.api import vider_les_ecritures_en_memoire
from app.contextes.comptabilite.application.revue import (
    points_de_controle,
    remarquer,
    transmettre_un_mois,
)
from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    LigneEcriture,
    Sens,
    TypeEcriture,
)
from app.contextes.comptabilite.domaine.rapprochement import (
    LigneDeReleve,
    RapprochementBancaire,
    StatutRapprochement,
)
from app.contextes.comptabilite.domaine.revue import (
    CritereAtypique,
    NatureObjet,
    ObjetDeRemarque,
    ReglagesDeLEchantillon,
    RevueRefusee,
    StatutRemarque,
    StatutRevue,
    echantillonner,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = "M081234567890P"
COMPTABLE = "l.fotso@cga-brcg.cm"
COMPTABLE_AUTRE = "c.ndongo@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
INSTANT = datetime(2026, 8, 5, 9, 0)
REGLAGES = ReglagesDeLEchantillon()
JUILLET = {"du": "2026-07-01", "au": "2026-07-31"}


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_revues()
    vider_les_ecritures_en_memoire()
    yield
    vider_les_revues()
    vider_les_ecritures_en_memoire()


@pytest.fixture
def cabinet():
    with etabli("CGA-BRCG"):
        yield


def _ecriture(
    numero,
    jour,
    montant,
    compte="601",
    journal="AC",
    etat=EtatEcriture.VALIDEE,
    type_=TypeEcriture.NORMALE,
    mois=7,
    piece="PJ-2026-0001",
):
    valeurs = dict(
        journal=journal,
        exercice="2026",
        numero=numero,
        date_operation=date(2026, mois, jour),
        libelle=f"écriture {numero}",
        piece_justificative=piece,
        lignes=[
            LigneEcriture(compte=compte, libelle="x", sens=Sens.DEBIT, montant=Decimal(montant)),
            LigneEcriture(compte="401", libelle="x", sens=Sens.CREDIT, montant=Decimal(montant)),
        ],
        etat=etat,
        type=type_,
    )
    if etat is EtatEcriture.VALIDEE:
        valeurs.update(validee_par="C-004", validee_le=INSTANT)
    if type_ is TypeEcriture.CONTREPASSATION:
        valeurs.update(
            ecriture_contrepassee="2026/AC/000001", motif_contrepassation="Erreur de compte."
        )
    return EcritureComptable(**valeurs)


# ── L'échantillon ─────────────────────────────────────────────────────────────


class TestLEchantillon:
    def test_les_criteres_et_leur_ordre(self):
        ecritures = [
            _ecriture(1, 3, 1000000),  # rond, élevé
            _ecriture(2, 4, 999999, compte="2441"),  # élevé, compte rare
            _ecriture(3, 5, 12500),  # rien de particulier… sauf s'il est parmi les 3 plus gros
            _ecriture(4, 6, 8000),
            _ecriture(5, 7, 7000, type_=TypeEcriture.CONTREPASSATION),
            _ecriture(6, 8, 500000),  # rond seulement ? non : 3e plus gros
        ]
        # Le compte 601 est fréquent sur l'exercice : il n'est pas « rare ».
        exercice = ecritures + [_ecriture(10 + i, 1, 100, mois=3) for i in range(5)]
        echantillon = echantillonner(ecritures, exercice, REGLAGES)
        par_cle = {e.ecriture: e.criteres for e in echantillon}
        assert par_cle["2026/AC/000001"] == [
            CritereAtypique.MONTANT_ROND,
            CritereAtypique.MONTANT_ELEVE,
        ]
        assert par_cle["2026/AC/000002"] == [
            CritereAtypique.MONTANT_ELEVE,
            CritereAtypique.COMPTE_RARE,
        ]
        assert par_cle["2026/AC/000006"] == [
            CritereAtypique.MONTANT_ROND,
            CritereAtypique.MONTANT_ELEVE,
        ]
        assert par_cle["2026/AC/000005"] == [CritereAtypique.CONTRE_PASSATION]
        assert "2026/AC/000003" not in par_cle and "2026/AC/000004" not in par_cle
        # Les plus signalées d'abord, puis les plus gros montants.
        assert [e.ecriture for e in echantillon] == [
            "2026/AC/000001",
            "2026/AC/000002",
            "2026/AC/000006",
            "2026/AC/000005",
        ]
        assert "compte 2441 rarement mouvementé" in echantillon[1].raisons

    def test_un_montant_rond_sous_le_seuil_n_est_pas_signale(self):
        reglages = ReglagesDeLEchantillon(plus_gros_montants=0)
        exercice = [_ecriture(1, 3, 400000)] * 5
        assert echantillonner([_ecriture(1, 3, 400000)], exercice, reglages) == []
        assert echantillonner([_ecriture(1, 3, 500000)], exercice, reglages)[0].criteres == [
            CritereAtypique.MONTANT_ROND
        ]
        assert echantillonner([_ecriture(1, 3, 600000)], exercice, reglages)[0].criteres == [
            CritereAtypique.MONTANT_ROND
        ]
        # Au-dessus du seuil mais pas multiple du pas : pas « rond ».
        assert echantillonner([_ecriture(1, 3, 550000)], exercice, reglages) == []

    def test_un_compte_est_rare_jusqu_a_la_borne_incluse(self):
        reglages = ReglagesDeLEchantillon(plus_gros_montants=0, compte_rare_au_plus=2)
        cible = _ecriture(1, 3, 100, compte="2441")
        deux_fois = [cible, _ecriture(2, 3, 100, compte="2441")] + [_ecriture(3, 3, 100)] * 3
        trois_fois = deux_fois + [_ecriture(4, 3, 100, compte="2441")]
        assert echantillonner([cible], deux_fois, reglages)[0].criteres == [
            CritereAtypique.COMPTE_RARE
        ]
        assert echantillonner([cible], trois_fois, reglages) == []

    def test_le_plafond(self):
        ecritures = [_ecriture(i, 3, 1000000 + 100000 * i) for i in range(1, 10)]
        assert (
            len(echantillonner(ecritures, ecritures * 3, ReglagesDeLEchantillon(taille_maximum=4)))
            == 4
        )

    def test_un_reglage_mal_ecrit_est_refuse(self):
        with pytest.raises(ValidationError):
            ReglagesDeLEchantillon.model_validate({"plus_gros_montant": 3})


# ── Le circuit ────────────────────────────────────────────────────────────────


def _transmettre(depot, ecritures, du=date(2026, 7, 1), au=date(2026, 7, 31), compte="C-004"):
    return transmettre_un_mois(
        dossier=BATIMENT,
        exercice="2026",
        du=du,
        au=au,
        ecritures_de_l_exercice=ecritures,
        reglages=REGLAGES,
        par="Léonard FOTSO",
        compte=compte,
        le=INSTANT,
        message="Juillet prêt.",
        depot=depot,
        # La clôture qui précède la transmission a ses propres tests (pas 107,
        # `test_cloture_mensuelle.py`) ; ici, aucun point ne bloque.
        points_de_cloture=[],
    )


def _objet(reference="2026/AC/000001", nature=NatureObjet.ECRITURE):
    return ObjetDeRemarque(nature=nature, reference=reference)


REMARQUE = "Compte de charge : est-ce un investissement ?"
REPONSE = "Vérifié : fournitures consommées, compte correct."


@pytest.mark.usefixtures("cabinet")
class TestLeCircuit:
    def test_transmettre_refuse_un_mois_vide_un_brouillon_ou_un_chevauchement(self):
        depot = DepotRevuesMemoire()
        with pytest.raises(RevueRefusee, match="rien à réviser"):
            _transmettre(depot, [_ecriture(1, 3, 100, mois=6)])
        with pytest.raises(RevueRefusee, match="1 écriture\\(s\\) en brouillon.*2026/AC/000002"):
            _transmettre(
                depot, [_ecriture(1, 3, 100), _ecriture(2, 4, 100, etat=EtatEcriture.BROUILLON)]
            )
        revue = _transmettre(depot, [_ecriture(1, 3, 100)])
        assert revue.statut is StatutRevue.TRANSMISE
        assert revue.historique[0].message == "Juillet prêt."
        with pytest.raises(RevueRefusee, match="chevauche la revue du 01/07/2026"):
            # Jusqu'au 4 août : transmis le 5, un mois qui n'est pas fini ne se transmet plus
            # (pas 107).
            _transmettre(depot, [_ecriture(1, 20, 100)], du=date(2026, 7, 15), au=date(2026, 8, 4))

    def test_une_remarque_se_rattache_a_un_objet_de_la_periode(self):
        ecritures = [
            _ecriture(1, 3, 100, compte="604", piece="PJ-2026-0024"),
            _ecriture(2, 3, 100, mois=6),
        ]
        revue = _transmettre(DepotRevuesMemoire(), ecritures)
        for nature, reference in (
            (NatureObjet.ECRITURE, "2026/AC/000001"),
            (NatureObjet.PIECE, "PJ-2026-0024"),
            (NatureObjet.COMPTE, "604"),
        ):
            revue = remarquer(
                revue,
                objet=_objet(f" {reference} ", nature),
                texte=REMARQUE,
                ecritures_de_l_exercice=ecritures,
                par="Aïcha BOUBA",
                le=INSTANT,
            )
        assert [(r.rang, r.objet.reference) for r in revue.remarques] == [
            (1, "2026/AC/000001"),
            (2, "PJ-2026-0024"),
            (3, "604"),
        ]
        for nature, reference, message in (
            (NatureObjet.ECRITURE, "2026/AC/000002", "aucune écriture"),  # juin, hors période
            (NatureObjet.PIECE, "PJ-9999", "aucune pièce"),
            (NatureObjet.COMPTE, "2441", "aucun compte mouvementé"),
        ):
            with pytest.raises(RevueRefusee, match=message):
                remarquer(
                    revue,
                    objet=_objet(reference, nature),
                    texte=REMARQUE,
                    ecritures_de_l_exercice=ecritures,
                    par="Aïcha BOUBA",
                    le=INSTANT,
                )

    def test_le_circuit_complet_et_les_gestes_hors_de_leur_etat(self):
        ecritures = [_ecriture(1, 3, 100)]
        revue = _transmettre(DepotRevuesMemoire(), ecritures)
        with pytest.raises(RevueRefusee, match="renvoyer sans remarque ouverte"):
            revue.renvoyer(par="A. BOUBA", compte="C-003", le=INSTANT, message=None)
        with pytest.raises(RevueRefusee, match="mois renvoyé"):
            revue.repondre(1, REPONSE, par="L. FOTSO", le=INSTANT)
        revue = revue.remarquer(_objet(), REMARQUE, par="A. BOUBA", le=INSTANT)
        with pytest.raises(RevueRefusee, match="remarque\\(s\\) 1 non close"):
            revue.valider(par="A. BOUBA", compte="C-003", le=INSTANT)
        revue = revue.renvoyer(par="A. BOUBA", compte="C-003", le=INSTANT, message="À voir.")
        with pytest.raises(RevueRefusee, match="mois transmis"):
            revue.remarquer(_objet(), REMARQUE, par="A. BOUBA", le=INSTANT)
        with pytest.raises(RevueRefusee, match="1 sans réponse"):
            revue.retransmettre(
                par="L. FOTSO",
                compte="C-004",
                le=INSTANT,
                echantillon=[],
                ecritures_du_mois=1,
                message=None,
            )
        with pytest.raises(RevueRefusee, match="au moins 10"):
            revue.repondre(1, "ok", par="L. FOTSO", le=INSTANT)
        revue = revue.repondre(1, REPONSE, par="L. FOTSO", le=INSTANT)
        assert revue.remarque(1).statut is StatutRemarque.TRAITEE
        revue = revue.retransmettre(
            par="L. FOTSO",
            compte="C-004",
            le=INSTANT,
            echantillon=echantillonner([_ecriture(1, 3, 2000000)], [], REGLAGES),
            ecritures_du_mois=1,
            message="Répondu.",
        )
        # La retransmission porte l'échantillon recalculé : les écritures ont pu changer.
        assert [e.ecriture for e in revue.echantillon] == ["2026/AC/000001"]
        assert revue.echantillon[0].montant == Decimal(2000000)
        revue = revue.clore_la_remarque(1, par="A. BOUBA", le=INSTANT)
        with pytest.raises(RevueRefusee, match="déjà close"):
            revue.clore_la_remarque(1, par="A. BOUBA", le=INSTANT)
        validee = revue.valider(par="A. BOUBA", compte="C-003", le=INSTANT)
        assert validee.statut is StatutRevue.VALIDEE
        assert [p.statut for p in validee.historique] == [
            StatutRevue.TRANSMISE,
            StatutRevue.RENVOYEE,
            StatutRevue.TRANSMISE,
            StatutRevue.VALIDEE,
        ]
        with pytest.raises(RevueRefusee, match="mois transmis"):
            validee.valider(par="A. BOUBA", compte="C-003", le=INSTANT)

    def test_celui_qui_transmet_ne_valide_pas_sa_propre_revue(self):
        revue = _transmettre(DepotRevuesMemoire(), [_ecriture(1, 3, 100)], compte="C-003")
        with pytest.raises(RevueRefusee, match="autre paire d'yeux"):
            revue.valider(par="A. BOUBA", compte="C-003", le=INSTANT)

    def test_une_remarque_close_ne_se_repond_plus_et_une_revue_validee_n_en_garde_aucune_ouverte(
        self,
    ):
        revue = _transmettre(DepotRevuesMemoire(), [_ecriture(1, 3, 100)])
        revue = revue.remarquer(_objet(), REMARQUE, par="A", le=INSTANT)
        revue = revue.remarquer(_objet(), REMARQUE, par="A", le=INSTANT)
        revue = revue.clore_la_remarque(1, par="A", le=INSTANT)
        revue = revue.renvoyer(par="A", compte="C-003", le=INSTANT, message=None)
        with pytest.raises(RevueRefusee, match="close : elle n'attend plus"):
            revue.repondre(1, REPONSE, par="L", le=INSTANT)
        with pytest.raises(ValidationError, match="aucune remarque ouverte"):
            revue.model_validate(
                {**revue.model_dump(), "statut": StatutRevue.VALIDEE, "validee_par": "A"}
            )


def _rapprochement(journal, au, statut):
    ligne = LigneDeReleve(rang=1, date=au, libelle="x", montant=Decimal(1), sens=Sens.DEBIT)
    return RapprochementBancaire(
        identifiant=f"RB-{journal}",
        dossier=BATIMENT,
        journal=journal,
        compte="521",
        exercice="2026",
        du=date(au.year, au.month, 1),
        au=au,
        solde_initial=Decimal(0),
        solde_final=Decimal(1),
        lignes=[ligne],
        source="saisie",
        importe_par="x",
        importe_le=INSTANT,
        statut=statut,
    )


@pytest.mark.usefixtures("cabinet")
class TestLesPointsDeControle:
    def test_le_rapprochement_arrete_couvrant_la_fin_du_mois(self):
        banque = _ecriture(1, 10, 500, compte="521", journal="BQ")
        revue = _transmettre(DepotRevuesMemoire(), [banque])
        journaux = {"BQ": "521", "MM": "5231"}

        def point(rapprochements):
            return next(
                p
                for p in points_de_controle(revue, [banque], journaux, rapprochements)
                if p.code == "RAPPROCHEMENT"
            )

        assert not point([]).conforme
        assert "pour BQ" in point([]).detail
        assert not point(
            [_rapprochement("BQ", date(2026, 7, 31), StatutRapprochement.EN_COURS)]
        ).conforme
        assert not point(
            [_rapprochement("BQ", date(2026, 6, 30), StatutRapprochement.VALIDE)]
        ).conforme
        assert point([_rapprochement("BQ", date(2026, 7, 31), StatutRapprochement.VALIDE)]).conforme

    def test_les_autres_points(self):
        ecritures = [_ecriture(1, 3, 100), _ecriture(3, 4, 100)]  # le numéro 2 manque
        revue = _transmettre(DepotRevuesMemoire(), ecritures)
        points = {p.code: p for p in points_de_controle(revue, ecritures, {}, [])}
        assert points["BROUILLONS"].conforme
        assert points["EQUILIBRE"].conforme
        assert (
            not points["NUMEROTATION"].conforme and "manquants [2]" in points["NUMEROTATION"].detail
        )
        assert points["RAPPROCHEMENT"].detail == "aucun compte de banque mouvementé"
        # Un brouillon apparu depuis la transmission (une écriture remise en brouillon ne se peut
        # pas, mais une écriture saisie ensuite sur la période, si).
        tard = ecritures + [_ecriture(2, 5, 100, etat=EtatEcriture.BROUILLON)]
        points = {p.code: p for p in points_de_controle(revue, tard, {}, [])}
        assert not points["BROUILLONS"].conforme and "2026/AC/000002" in points["BROUILLONS"].detail
        assert points["NUMEROTATION"].conforme


@pytest.mark.usefixtures("cabinet")
def test_un_autre_cabinet_ne_voit_pas_la_revue():
    depot = DepotRevuesMemoire()
    _transmettre(depot, [_ecriture(1, 3, 100)])
    with etabli("AUTRE-CABINET"):
        assert depot.toutes() == [] and depot.du_dossier(BATIMENT) == []
        assert _transmettre(depot, [_ecriture(1, 3, 100)]).statut is StatutRevue.TRANSMISE


# ── Les routes ────────────────────────────────────────────────────────────────


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _base(identifiant="REV-20260701-20260731") -> str:
    return f"/comptabilite/dossiers/{BATIMENT}/revues/{identifiant}"


def _notifications(client) -> list[str]:
    return [n["titre"] for n in client.get("/transverse/notifications").json()["notifications"]]


@pytest.fixture
def cloture_sans_point_bloquant(monkeypatch):
    """Le circuit de la revue, sans les points bloquants de la clôture mensuelle (pas 107).

    ⚠️ Juillet, au jeu de démonstration, a quatre pièces lues et non comptabilisées : depuis le
    pas 107, il ne se transmet plus, et c'est voulu. Ces tests gardent le **circuit** entre le
    comptable et le réviseur ; la clôture qui le précède est gardée par
    `test_cloture_mensuelle.py`. Les points restent calculés et affichés, seul leur caractère
    bloquant est levé, comme un cabinet le ferait au référentiel.
    """
    from app.contextes.comptabilite.adaptateurs.entrant import routes_cloture_mensuelle
    from app.contextes.comptabilite.domaine.cloture_mensuelle import (
        CodePoint,
        ReglagesDeLaClotureMensuelle,
    )

    informatifs = ReglagesDeLaClotureMensuelle(
        points={
            code.value: {"bloquant": code is CodePoint.BROUILLONS}
            for code in CodePoint
        }
    )
    monkeypatch.setattr(routes_cloture_mensuelle, "_reglages", lambda: informatifs)


@pytest.mark.usefixtures("cloture_sans_point_bloquant")
class TestLesRoutes:
    def test_le_parcours_entre_le_comptable_et_le_reviseur(self):
        comptable, reviseur = _client(COMPTABLE), _client(REVISEUR)
        transmise = comptable.post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        assert transmise.status_code == 201, transmise.text
        vue = transmise.json()
        assert vue["revue"]["transmise_par"] == "Léonard FOTSO"
        assert vue["revue"]["ecritures_du_mois"] == len(vue["ecritures"]) > 0
        assert {p["code"] for p in vue["points"]} == {
            "BROUILLONS",
            "NUMEROTATION",
            "EQUILIBRE",
            "RAPPROCHEMENT",
        }
        assert "Mois à réviser : " + BATIMENT in _notifications(reviseur)
        file = reviseur.get("/comptabilite/revues?statut=TRANSMISE").json()
        assert [r["identifiant"] for r in file] == ["REV-20260701-20260731"]

        ecriture = vue["ecritures"][0]["cle"]
        remarque = reviseur.post(
            f"{_base()}/remarques",
            json={"nature": "ECRITURE", "reference": ecriture, "texte": REMARQUE},
        )
        assert remarque.status_code == 200, remarque.text
        assert (
            reviseur.post(f"{_base()}/renvoi", json={"message": "Une question."}).status_code == 200
        )
        assert "Mois renvoyé avec 1 remarque(s) : " + BATIMENT in _notifications(comptable)
        assert [
            r["statut"] for r in comptable.get("/comptabilite/revues?statut=RENVOYEE").json()
        ] == ["RENVOYEE"]

        assert (
            comptable.post(f"{_base()}/remarques/1/reponse", json={"reponse": REPONSE}).status_code
            == 200
        )
        assert comptable.post(f"{_base()}/retransmission", json={}).status_code == 200
        assert reviseur.post(f"{_base()}/remarques/1/cloture").status_code == 200
        validee = reviseur.post(f"{_base()}/validation")
        assert validee.status_code == 200, validee.text
        assert validee.json()["revue"]["validee_par"] == "Aïcha BOUBA"
        assert "Mois validé : " + BATIMENT in _notifications(comptable)
        assert comptable.get("/comptabilite/revues?statut=TRANSMISE").json() == []
        assert [
            r["statut"] for r in comptable.get("/comptabilite/revues?statut=VALIDEE").json()
        ] == ["VALIDEE"]

    def test_le_comptable_ne_revise_pas_et_le_reviseur_ne_valide_pas_ce_qu_il_a_transmis(self):
        comptable, reviseur = _client(COMPTABLE), _client(REVISEUR)
        comptable.post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        corps = {"nature": "COMPTE", "reference": "401", "texte": REMARQUE}
        assert comptable.post(f"{_base()}/remarques", json=corps).status_code == 403
        assert comptable.post(f"{_base()}/validation").status_code == 403
        assert comptable.post(f"{_base()}/renvoi", json={}).status_code == 403
        # Le réviseur détient aussi la saisie : s'il transmet lui-même, il ne valide pas.
        vider_les_revues()
        reviseur.post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        vue = reviseur.get(_base()).json()
        assert vue["auteur_de_la_transmission"] is True
        refus = reviseur.post(f"{_base()}/validation")
        assert refus.status_code == 422 and "autre paire d'yeux" in refus.json()["detail"]

    def test_hors_perimetre_la_revue_n_existe_pas_et_la_file_est_restreinte(self):
        _client(COMPTABLE).post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        autre = _client(COMPTABLE_AUTRE)
        assert autre.get(_base()).status_code == 404
        assert autre.get("/comptabilite/revues").json() == []
        assert _client(DIRECTION).get("/comptabilite/revues").status_code == 200
        assert _client(REVISEUR).get(_base("REV-INCONNUE")).status_code == 404

    def test_les_refus_parlent_en_clair(self):
        comptable, reviseur = _client(COMPTABLE), _client(REVISEUR)
        vide = comptable.post(
            f"/comptabilite/dossiers/{BATIMENT}/revues",
            json={"du": "2026-03-01", "au": "2026-03-31"},
        )
        assert vide.status_code == 422 and "rien à réviser" in vide.json()["detail"]
        comptable.post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        court = reviseur.post(
            f"{_base()}/remarques", json={"nature": "COMPTE", "reference": "401", "texte": "court"}
        )
        assert court.status_code == 422 and "10 caractères" in court.json()["detail"]
        faux = reviseur.post(
            f"{_base()}/remarques",
            json={"nature": "COMPTE", "reference": "2441", "texte": REMARQUE},
        )
        assert (
            faux.status_code == 422 and "aucun compte mouvementé « 2441 »" in faux.json()["detail"]
        )
        assert comptable.post(f"{_base()}/retransmission", json={}).status_code == 422


@pytest.mark.usefixtures("cloture_sans_point_bloquant")
class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_transmettre_remarquer_et_relire_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        transmise = client.post(f"/comptabilite/dossiers/{BATIMENT}/revues", json=JUILLET)
        assert transmise.status_code == 201, transmise.text
        ouvrir_une_session(client, REVISEUR)
        corps = {"nature": "COMPTE", "reference": "401", "texte": REMARQUE}
        assert client.post(f"{_base()}/remarques", json=corps).status_code == 200
        relue = client.get(_base()).json()
        assert relue["revue"]["remarques"][0]["objet"]["reference"] == "401"
        assert [r["statut"] for r in client.get("/comptabilite/revues").json()] == ["TRANSMISE"]
