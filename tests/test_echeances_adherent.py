"""Les échéances de l'adhérent, et la preuve qu'il a payé (pas 113).

Maquette « Espace adhérent CGA », vue D, sans le paiement en ligne (question Q29).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La période lisible** : un mois, un trimestre, une année, sinon les dates.
2. **Une carte par obligation** : la plus ancienne période en retard sans preuve, le nombre des
   autres ; une preuve envoyée garde sa carte ; le dernier dépôt seul ; l'horizon ; la période
   précédente et la prochaine échéance ; l'ordre (ce qui demande un geste d'abord).
3. **Les réglages** qui mentiraient : une obligation expliquée deux fois, des textes « validés »
   sans nom ; le fichier livré n'explique que des obligations du catalogue.
4. **Les routes** : la lecture, la preuve (et l'avis au cabinet), ses refus, puis le dépôt consigné
   qui remplace la preuve par l'accusé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    CATALOGUE_OBLIGATIONS,
)
from app.contextes.obligations.adaptateurs.sortant.explications_adherent_yaml import (
    charger_les_echeances_de_l_adherent,
)
from app.contextes.obligations.api import ObligationInstance
from app.contextes.obligations.domaine.echeances_adherent import (
    EtatPourLAdherent,
    ExplicationDObligation,
    PreuveEnvoyee,
    ReglagesDesEcheancesDeLAdherent,
    echeances_de_l_adherent,
    libelle_de_periode,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"
JOUR = date(2026, 8, 20)


class TestLaPeriode:
    def test_mois_trimestre_annee_et_le_reste(self):
        assert libelle_de_periode(date(2026, 7, 1), date(2026, 7, 31)) == "juillet 2026"
        assert libelle_de_periode(date(2026, 1, 1), date(2026, 3, 31)) == "1er trimestre 2026"
        assert libelle_de_periode(date(2026, 7, 1), date(2026, 9, 30)) == "3e trimestre 2026"
        assert libelle_de_periode(date(2025, 1, 1), date(2025, 12, 31)) == "année 2025"
        assert libelle_de_periode(date(2026, 2, 1), date(2026, 4, 30)) == "du 01/02/2026 au 30/04/2026"
        assert libelle_de_periode(date(2026, 7, 15), date(2026, 7, 31)) == "du 15/07/2026 au 31/07/2026"


def _mois(code, mois, **champs):
    debut = date(2026, mois, 1)
    fin = date(2026, mois + 1, 1).replace(day=1) if mois < 12 else date(2027, 1, 1)
    from datetime import timedelta

    valeurs = {
        "entreprise": "M081234567890P",
        "code_obligation": code,
        "libelle": f"Libellé {code}",
        "periode_debut": debut,
        "periode_fin": fin - timedelta(days=1),
        "echeance": fin + timedelta(days=14),
    }
    return ObligationInstance(**{**valeurs, **champs})


def _deposee(code, mois, le, numero):
    return _mois(code, mois, statut="DECLAREE", declaree_le=le, reference_depot=numero)


REGLAGES = ReglagesDesEcheancesDeLAdherent(
    explications=[
        ExplicationDObligation(
            code="CNPS",
            titre="Cotisations CNPS",
            de_quoi_s_agit_il="Les cotisations sociales de vos employés.",
            en_cas_de_retard="Un retard entraîne des majorations.",
        )
    ]
)


def _vue(instances, preuves=(), montants=None, reglages=REGLAGES):
    return echeances_de_l_adherent(
        instances=instances,
        jour=JOUR,
        reglages=reglages,
        montants_constates=montants or {},
        preuves=list(preuves),
    )


class TestUneCarteParObligation:
    def test_la_plus_ancienne_en_retard_et_le_nombre_des_autres(self):
        vue = _vue([_mois("CNPS", m) for m in (4, 5, 6, 7, 8, 9)])
        (carte,) = vue
        assert carte.etat is EtatPourLAdherent.EN_RETARD
        assert carte.periode == "avril 2026" and carte.echeance == date(2026, 5, 15)
        assert carte.jours == 97
        # mai, juin, juillet : échéances passées au 20 août ; août (15/09) et septembre sont à venir.
        assert carte.autres_periodes_en_retard == 3
        assert carte.titre == "Cotisations CNPS"
        assert carte.de_quoi_s_agit_il.startswith("Les cotisations")
        # La prochaine à venir (août, 15/09), pas mai, lui-même en retard.
        assert carte.prochaine_echeance == date(2026, 9, 15)

    def test_sans_retard_la_prochaine_a_venir_dans_l_horizon(self):
        vue = _vue([_deposee("CNPS", 7, date(2026, 8, 10), "A-7"), _mois("CNPS", 8), _mois("CNPS", 9), _mois("CNPS", 12)])
        etats = [(c.etat, c.periode) for c in vue]
        assert etats == [
            (EtatPourLAdherent.A_VENIR, "août 2026"),
            (EtatPourLAdherent.DEPOSEE, "juillet 2026"),
        ]
        a_venir = vue[0]
        assert a_venir.jours == 26 and a_venir.precedente.periode == "juillet 2026"
        assert a_venir.precedente.declaree_le == date(2026, 8, 10)
        assert a_venir.prochaine_echeance == date(2026, 10, 15)
        # Décembre (échéance 15/01/2027) est au-delà de l'horizon de 60 jours : il n'est pas montré.
        assert all(c.periode != "décembre 2026" for c in vue)

    def test_la_preuve_garde_sa_carte_et_la_suivante_devient_la_carte_en_retard(self):
        preuve = PreuveEnvoyee(
            code_obligation="CNPS",
            periode_debut=date(2026, 5, 1),
            piece="PJ-1",
            envoyee_le=datetime(2026, 8, 19, 10, 0),
            par="A-001",
        )
        vue = _vue([_mois("CNPS", m) for m in (5, 6)], preuves=[preuve])
        assert [(c.etat, c.periode, c.autres_periodes_en_retard) for c in vue] == [
            (EtatPourLAdherent.EN_RETARD, "juin 2026", 0),
            (EtatPourLAdherent.PREUVE_ENVOYEE, "mai 2026", 0),
        ]
        assert vue[1].preuve.piece == "PJ-1" and vue[0].preuve is None

    def test_le_dernier_depot_seul_et_son_montant(self):
        vue = _vue(
            [
                _deposee("CNPS", 5, date(2026, 6, 10), "A-5"),
                _deposee("CNPS", 6, date(2026, 7, 10), "A-6"),
                _deposee("TVA", 1, date(2026, 2, 10), "T-1"),
            ],
            montants={"A-5": Decimal(100), "A-6": Decimal(184500)},
        )
        (carte,) = vue
        assert carte.etat is EtatPourLAdherent.DEPOSEE and carte.periode == "juin 2026"
        assert carte.montant_constate == Decimal(184500)
        assert carte.precedente.montant_constate == Decimal(100)
        # Sans explication au référentiel : le libellé du catalogue.
        assert _vue([_mois("TVA", 7)])[0].titre == "Libellé TVA"

    def test_l_ordre_ce_qui_demande_un_geste_d_abord(self):
        preuve = PreuveEnvoyee(
            code_obligation="IGS", periode_debut=date(2026, 7, 1), piece="P", envoyee_le=datetime(2026, 8, 1), par="A"
        )
        vue = _vue(
            [
                _deposee("DSF", 2, date(2026, 8, 1), "D"),
                _mois("TVA", 8),
                _mois("IGS", 7),
                _mois("CNPS", 6),
                _mois("IRPP", 5),
            ],
            preuves=[preuve],
        )
        assert [(c.code_obligation, c.etat) for c in vue] == [
            ("IRPP", EtatPourLAdherent.EN_RETARD),
            ("CNPS", EtatPourLAdherent.EN_RETARD),
            ("IGS", EtatPourLAdherent.PREUVE_ENVOYEE),
            ("TVA", EtatPourLAdherent.A_VENIR),
            ("DSF", EtatPourLAdherent.DEPOSEE),
        ]

    def test_hors_horizon_rien_et_la_prochaine_saute_les_periodes_deposees(self):
        assert _vue([_mois("CNPS", 12)]) == []
        (carte, _) = _vue(
            [_mois("CNPS", 5), _deposee("CNPS", 8, date(2026, 8, 18), "A-8"), _mois("CNPS", 9)]
        )
        # Août (échéance 15/09) est déjà déposé : la prochaine est septembre, 15/10.
        assert carte.periode == "mai 2026" and carte.prochaine_echeance == date(2026, 10, 15)

    def test_un_depot_trop_ancien_n_est_plus_montre(self):
        assert _vue([_deposee("CNPS", 3, date(2026, 4, 10), "A-3")]) == []


class TestLesReglages:
    def test_ce_qui_mentirait(self):
        texte = {"titre": "Titre", "de_quoi_s_agit_il": "Une explication.", "en_cas_de_retard": "Une majoration."}
        with pytest.raises(ValidationError, match="expliquée deux fois"):
            ReglagesDesEcheancesDeLAdherent(explications=[{"code": "IGS", **texte}, {"code": "IGS", **texte}])
        with pytest.raises(ValidationError, match="nomment qui les a validés"):
            ReglagesDesEcheancesDeLAdherent(statut="VALIDE")
        assert ReglagesDesEcheancesDeLAdherent(statut="VALIDE", valide_par="r.ebolo").statut == "VALIDE"

    def test_le_fichier_livre_est_a_valider_et_n_explique_que_le_catalogue(self):
        reglages = charger_les_echeances_de_l_adherent(REFERENTIEL)
        assert reglages.source == "obligations/explications_adherent.yaml"
        assert reglages.statut == "A_VALIDER" and reglages.valide_par is None
        codes = {t.code for t in CATALOGUE_OBLIGATIONS}
        assert {e.code for e in reglages.explications} == codes
        assert reglages.explication("IGS").titre == "Impôt trimestriel"
        assert charger_les_echeances_de_l_adherent(Path("/nulle/part")).explications == ()


# ── Les routes ────────────────────────────────────────────────────────────────

BATIMENT = "M081234567890P"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
ECHEANCES = f"/obligations/dossiers/{BATIMENT}/mes-echeances"
PREUVES = f"/obligations/dossiers/{BATIMENT}/preuves-de-paiement"
CNPS_JUILLET = {"code_obligation": "CNPS", "periode_debut": "2026-07-01", "periode_fin": "2026-07-31"}


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    with horloge_figee(datetime(2026, 8, 20, 9, 0)):
        yield


def _client(courriel):
    client = TestClient(creer_application())
    reponse = client.post("/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO})
    assert reponse.status_code == 200, reponse.text
    return client


def _carte(vue, code, periode_debut=None):
    return next(
        e
        for e in vue["echeances"]
        if e["code_obligation"] == code and (periode_debut is None or e["periode_debut"] == periode_debut)
    )


class TestLesRoutes:
    def test_la_lecture(self):
        vue = _client(ADHERENT).get(ECHEANCES).json()
        assert vue["denomination"] and vue["explications_validees"] is False
        codes = [e["code_obligation"] for e in vue["echeances"] if e["etat"] != "DEPOSEE"]
        assert len(codes) == len(set(codes)), "une carte par obligation"
        cnps = _carte(vue, "CNPS")
        assert cnps["titre"] == "Cotisations sociales CNPS"
        assert cnps["de_quoi_s_agit_il"]
        assert _client(AUTRE_ADHERENT).get(ECHEANCES).status_code == 404

    def test_la_preuve_puis_le_depot(self):
        from tests.test_teledeclaration import _renforcer

        adherent = _client(ADHERENT)
        envoi = adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-2026-0013"})
        assert envoi.status_code == 201, envoi.text
        assert envoi.json()["titre"] == "Cotisations sociales CNPS"
        assert envoi.json()["periode"] == "juillet 2026"
        carte = _carte(adherent.get(ECHEANCES).json(), "CNPS", "2026-07-01")
        assert carte["etat"] == "PREUVE_ENVOYEE" and carte["preuve"]["piece"] == "PJ-2026-0013"

        double = adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-2026-0013"})
        assert double.status_code == 409 and "déjà été envoyée" in double.json()["detail"]
        # Une autre quittance pour la même échéance (la première était illisible) est reçue.
        assert adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-2026-0019"}).status_code == 201

        reviseur = _client(REVISEUR)
        avis = reviseur.get("/transverse/notifications").json()["notifications"]
        recus = [n for n in avis if n["titre"].startswith("Preuve de paiement reçue")]
        assert len(recus) == 2
        assert {n["titre"] for n in recus} == {"Preuve de paiement reçue : Cotisations sociales CNPS, juillet 2026"}
        assert any("PJ-2026-0013" in n["texte"] for n in recus)
        assert all(n["lien"] == f"/obligations?dossier={BATIMENT}" for n in recus)

        _renforcer(reviseur)
        depot = reviseur.post(
            f"/obligations/dossiers/{BATIMENT}/depots",
            json={
                **CNPS_JUILLET,
                "numero": "CNPS-2026-DIPE-00418",
                "depose_le": "2026-08-12T09:30:00",
                "montant_constate": "184500",
                "piece_jointe": "PJ-2026-0013",
            },
        )
        assert depot.status_code in (200, 201), depot.text
        apres = adherent.get(ECHEANCES).json()
        deposee = _carte(apres, "CNPS", "2026-07-01")
        assert deposee["etat"] == "DEPOSEE" and deposee["preuve"] is None
        assert Decimal(deposee["montant_constate"]) == Decimal(184500)
        tardive = adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-2026-0019"})
        assert tardive.status_code == 409 and "rien à prouver" in tardive.json()["detail"]

    def test_les_refus(self):
        adherent = _client(ADHERENT)
        inconnue = adherent.post(PREUVES, json={**CNPS_JUILLET, "periode_fin": "2026-07-30", "piece": "PJ-2026-0013"})
        assert inconnue.status_code == 404 and "Périodes à régler" in inconnue.json()["detail"]
        piece = adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-INEXISTANTE"})
        assert piece.status_code == 404
        # Une pièce d'un autre dossier : même réponse qu'inconnue.
        from app.contextes.collecte.api import pieces_en_memoire

        autre = next(p for p in pieces_en_memoire().toutes() if p.entreprise != BATIMENT)
        assert adherent.post(PREUVES, json={**CNPS_JUILLET, "piece": autre.identifiant}).status_code == 404
        assert _client(AUTRE_ADHERENT).post(PREUVES, json={**CNPS_JUILLET, "piece": "PJ-2026-0013"}).status_code == 404
