"""Charge et production, et la réaffectation d'un dossier (pas 105).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La charge** : les points, la capacité du rôle, la saturation ; un rôle sans capacité
   n'a pas de pourcentage.
2. **Les propositions** : même rôle, jamais au-delà du seuil cible chez le collègue, on cesse
   de soulager au seuil cible, deux propositions ne comptent pas deux fois la même marge.
3. **Le retrait daté** d'un dossier, symétrique de l'affectation : le passé n'est pas réécrit.
4. **La réaffectation** : un geste, trois faits au journal, trois publics prévenus, et ses refus.
5. **La vue** : réservée à la direction, sans adhérent ni inspecteur parmi les collaborateurs.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.pilotage.domaine.charge_et_production import (
    DossierPourLaCharge,
    PorteurDeDossiers,
    ReglagesDeLaCharge,
    mesurer_la_charge,
    proposer_des_reaffectations,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.contextes.transverse.domaine.habilitations import Habilitation, MotifHabilitation
from app.contextes.transverse.domaine.roles import Role
from app.main import creer_application
from app.partage.horloge import horloge_figee

REGLAGES = ReglagesDeLaCharge(
    capacite_par_role={"COMPTABLE": 100},
    points_par_dossier=10,
    points_par_piece_en_attente=1,
    points_par_echeance_du_mois=0,
    points_par_retard=0,
    seuil_de_saturation=90,
    seuil_cible=80,
)
BATIMENT = "M081234567890P"
INSTANT = datetime(2026, 8, 17, 9, 0)


def _dossier(niu, pieces):
    """Un dossier de 10 + `pieces` points."""
    return DossierPourLaCharge(niu=niu, denomination=f"DOSSIER {niu}", pieces_en_attente=pieces)


def _porteur(compte, dossiers, role="COMPTABLE"):
    return PorteurDeDossiers(
        compte=compte,
        nom=f"NOM {compte}",
        role=role,
        habilitation=f"H-{compte}",
        dossiers=tuple(dossiers),
    )


class TestLaCharge:
    def test_points_capacite_et_saturation(self):
        dossiers = {"A": _dossier("A", 40), "B": _dossier("B", 50), "C": _dossier("C", 0)}
        lignes = mesurer_la_charge(
            [
                _porteur("C-1", ["A", "B"]),
                _porteur("C-2", ["C"]),
                _porteur("C-3", ["A"], role="AUTRE"),
            ],
            dossiers,
            REGLAGES,
        )
        assert [(l_.compte, l_.points, l_.charge, l_.sature) for l_ in lignes] == [
            ("C-1", 110, 110, True),
            ("C-2", 10, 10, False),
            ("C-3", 50, None, False),
        ]
        assert lignes[0].pieces_en_attente == 90 and lignes[0].dossiers == 2

    def test_la_charge_est_rapportee_a_la_capacite(self):
        reglages = REGLAGES.model_copy(update={"capacite_par_role": {"COMPTABLE": 200}})
        [ligne] = mesurer_la_charge([_porteur("C-1", ["A"])], {"A": _dossier("A", 60)}, reglages)
        assert (ligne.points, ligne.charge) == (70, 35)

    def test_au_seuil_on_n_est_pas_sature(self):
        [ligne] = mesurer_la_charge([_porteur("C-1", ["A"])], {"A": _dossier("A", 80)}, REGLAGES)
        assert (ligne.charge, ligne.sature) == (90, False)

    def test_des_reglages_incoherents_sont_refuses(self):
        with pytest.raises(ValidationError, match="seuil_cible dépasse"):
            ReglagesDeLaCharge(seuil_de_saturation=80, seuil_cible=85)
        with pytest.raises(ValidationError, match="strictement positive"):
            ReglagesDeLaCharge(capacite_par_role={"COMPTABLE": 0})
        with pytest.raises(ValidationError):
            ReglagesDeLaCharge.model_validate({"capacite_par_roles": {}})


class TestLesPropositions:
    def test_le_plus_gros_dossier_va_au_collegue_le_moins_charge_apres(self):
        dossiers = {
            "A": _dossier("A", 60),
            "B": _dossier("B", 20),
            "C": _dossier("C", 0),
            "D": _dossier("D", 30),
        }
        porteurs = [_porteur("C-1", ["A", "B"]), _porteur("C-2", ["C"]), _porteur("C-3", ["D"])]
        [proposition] = proposer_des_reaffectations(porteurs, dossiers, REGLAGES)
        # C-1 : 70 + 30 = 100 %. A (70 points) irait chez C-2 (10 → 80) : au seuil cible, permis.
        assert (proposition.dossier, proposition.vers_compte) == ("A", "C-2")
        assert (proposition.charge_de_avant, proposition.charge_de_apres) == (100, 30)
        assert (proposition.charge_vers_avant, proposition.charge_vers_apres) == (10, 80)
        assert proposition.vers_habilitation == "H-C-2"
        assert "sans dépasser 80 %" in proposition.raison

    def test_jamais_au_dela_du_seuil_cible(self):
        dossiers = {"A": _dossier("A", 85), "C": _dossier("C", 0)}
        porteurs = [_porteur("C-1", ["A"]), _porteur("C-2", ["C"])]  # 95 % et 10 %
        # A (95 points) amènerait C-2 à 105 % : rien n'est proposé.
        assert proposer_des_reaffectations(porteurs, dossiers, REGLAGES) == []

    def test_entre_le_seuil_cible_et_la_saturation(self):
        """Seuil cible 80, saturation 90 : les deux ne se confondent pas."""
        # C-1 à 85 % n'est pas saturé : rien n'est cherché pour lui, alors même que A (40 points)
        # tiendrait chez C-2 (10 → 50 %).
        dossiers = {"A": _dossier("A", 30), "D": _dossier("D", 35), "B": _dossier("B", 0)}
        assert (
            proposer_des_reaffectations(
                [_porteur("C-1", ["A", "D"]), _porteur("C-2", ["B"])], dossiers, REGLAGES
            )
            == []
        )
        # C-1 saturé à 100 % ; C (15 points) mènerait C-2 à 85 %, au-dessus du seuil cible.
        dossiers = {"A": _dossier("A", 75), "C": _dossier("C", 5), "B": _dossier("B", 60)}
        porteurs = [_porteur("C-1", ["A", "C"]), _porteur("C-2", ["B"])]
        assert proposer_des_reaffectations(porteurs, dossiers, REGLAGES) == []

    def test_jamais_vers_un_autre_role_ni_vers_qui_suit_deja_le_dossier(self):
        dossiers = {"A": _dossier("A", 20), "B": _dossier("B", 60)}
        porteurs = [
            _porteur("C-1", ["A", "B"]),  # 30 + 70 = 100 %
            _porteur("C-2", ["A"]),  # 30 % : suit déjà A, et B l'amènerait à 100 %
            _porteur("C-4", [], role="CHARGE_CLIENTELE"),  # 0 %, mais pas le même rôle
        ]
        reglages = REGLAGES.model_copy(
            update={"capacite_par_role": {"COMPTABLE": 100, "CHARGE_CLIENTELE": 1000}}
        )
        assert proposer_des_reaffectations(porteurs, dossiers, reglages) == []

    def test_on_cesse_de_soulager_au_seuil_cible_et_la_marge_n_est_pas_comptee_deux_fois(self):
        dossiers = {n: _dossier(n, 20) for n in "ABCDE"}  # 30 points chacun
        dossiers["Z"] = _dossier("Z", 0)
        porteurs = [
            _porteur("C-1", ["A", "B", "C", "D"]),  # 120 %
            _porteur("C-2", ["E"]),  # 30 %
            _porteur("C-3", ["Z"]),  # 10 %
        ]
        propositions = proposer_des_reaffectations(porteurs, dossiers, REGLAGES)
        # Deux dossiers suffisent (120 → 90 → 60) ; le premier va chez C-3 (10 → 40), le second
        # chez C-3 serait 70 et chez C-2 serait 60 : C-2, recalculé après la première proposition.
        assert [(p.dossier, p.vers_compte, p.charge_de_apres) for p in propositions] == [
            ("A", "C-3", 90),
            ("B", "C-2", 60),
        ]

    def test_un_role_sans_capacite_n_est_jamais_sature(self):
        dossiers = {"A": _dossier("A", 500), "B": _dossier("B", 0)}
        porteurs = [_porteur("C-1", ["A"], role="AUTRE"), _porteur("C-2", ["B"], role="AUTRE")]
        assert proposer_des_reaffectations(porteurs, dossiers, REGLAGES) == []


def _hab(**autres):
    valeurs = dict(
        identifiant="H-1",
        compte="C-1",
        role=Role.COMPTABLE,
        portee=frozenset({"N1", "N2"}),
        debut=date(2021, 9, 1),
        motif=MotifHabilitation.RECRUTEMENT,
        accordee_par="C-001",
    )
    return Habilitation(**{**valeurs, **autres})


class TestLeRetrait:
    def test_le_passe_n_est_pas_reecrit(self):
        fermee, successeur = _hab().retirer(
            "N1", le=date(2026, 8, 17), identifiant_successeur="H-2", par="C-001"
        )
        assert (fermee.fin, fermee.portee) == (date(2026, 8, 17), frozenset({"N1", "N2"}))
        assert (successeur.debut, successeur.portee) == (date(2026, 8, 17), frozenset({"N2"}))
        assert "dossier N1 retiré" in successeur.precision

    def test_ouverte_le_jour_meme_elle_se_reduit_sur_place(self):
        [reduite] = _hab(debut=date(2026, 8, 17)).retirer(
            "N1", le=date(2026, 8, 17), identifiant_successeur="H-2", par="C-001"
        )
        assert (reduite.identifiant, reduite.portee) == ("H-1", frozenset({"N2"}))

    @pytest.mark.parametrize(
        ("habilitation", "message"),
        [
            (_hab(portee=None, role=Role.REVISEUR), "transverse"),
            (_hab(role=Role.ADHERENT), "souscription ou une mission"),
            (_hab(fin=date(2026, 8, 1)), "fermée le 01/08/2026"),
            (_hab(portee=frozenset({"N2"})), "n'est pas dans la portée"),
        ],
    )
    def test_les_refus(self, habilitation, message):
        with pytest.raises(ValueError, match=message):
            habilitation.retirer(
                "N1", le=date(2026, 8, 17), identifiant_successeur="H-2", par="C-001"
            )


# ── Par les routes ────────────────────────────────────────────────────────────


@pytest.fixture
def plateforme():
    reinitialiser_atelier()
    with horloge_figee(INSTANT):
        yield creer_application()


def _client(application, courriel):
    client = TestClient(application)
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _notifications(client):
    return [n["titre"] for n in client.get("/transverse/notifications").json()["notifications"]]


class TestLesRoutes:
    def test_la_vue_propose_et_la_direction_reaffecte(self, plateforme):
        direction = _client(plateforme, "b.mballa@cga-brcg.cm")
        vue = direction.get("/pilotage/charge-et-production?a_la_date=2026-08-17").json()
        assert {l_["nom"] for l_ in vue["collaborateurs"]} == {
            "Léonard FOTSO",
            "Patricia MOUKOURI",
            "Christelle NDONGO",
        }
        assert (vue["mois_du"], vue["mois_au"]) == ("2026-08-01", "2026-08-31")
        [proposition] = vue["propositions"]
        assert (proposition["dossier"], proposition["de_nom"], proposition["vers_nom"]) == (
            BATIMENT,
            "Léonard FOTSO",
            "Christelle NDONGO",
        )
        reponse = direction.post(
            f"/transverse/dossiers/{BATIMENT}/reaffectation",
            json={
                "de": proposition["de_habilitation"],
                "vers": proposition["vers_habilitation"],
                "motif": "Léonard FOTSO saturé avant les échéances du 15.",
            },
        )
        assert reponse.status_code == 200, reponse.text
        corps = reponse.json()
        assert BATIMENT not in corps["source"]["portee"] and BATIMENT in corps["cible"]["portee"]

        apres = direction.get("/pilotage/charge-et-production?a_la_date=2026-08-17").json()
        charges = {l_["nom"]: l_["dossiers"] for l_ in apres["collaborateurs"]}
        assert (charges["Léonard FOTSO"], charges["Christelle NDONGO"]) == (2, 3)
        assert apres["propositions"] == []

        # Le passé n'est pas réécrit : Léonard FOTSO suivait le dossier en juillet.
        juillet = {
            h["compte"]
            for h in direction.get(
                f"/transverse/dossiers/{BATIMENT}/acces", params={"a_la_date": "2026-07-15"}
            ).json()
        }
        aujourd_hui = {
            h["compte"]
            for h in direction.get(
                f"/transverse/dossiers/{BATIMENT}/acces", params={"a_la_date": "2026-08-17"}
            ).json()
        }
        assert "C-004" in juillet and "C-004" not in aujourd_hui and "C-005" in aujourd_hui

        actions = [e["action"] for e in direction.get("/transverse/audit").json()][-3:]
        assert actions == ["dossier.retire", "dossier.affecte", "dossier.reaffecte"]
        assert f"Le dossier {BATIMENT} ne vous est plus confié" in _notifications(
            _client(plateforme, "l.fotso@cga-brcg.cm")
        )
        assert f"Le dossier {BATIMENT} vous est confié" in _notifications(
            _client(plateforme, "c.ndongo@cga-brcg.cm")
        )
        assert f"Nouvel interlocuteur comptable : {BATIMENT}" in _notifications(
            _client(plateforme, "p.moukouri@cga-brcg.cm")
        )
        assert _notifications(_client(plateforme, "jp.nkoa@batimentplus.cm")) == []

    @pytest.mark.parametrize(
        ("corps", "message"),
        [
            ({"de": "H-004", "vers": "H-005", "motif": "court"}, "10 caractères"),
            ({"de": "H-004", "vers": "H-007", "motif": "Rééquilibrage de charge."}, "même rôle"),
            (
                {"de": "H-004", "vers": "H-004", "motif": "Rééquilibrage de charge."},
                "même collaborateur",
            ),
            (
                {"de": "H-005", "vers": "H-004", "motif": "Rééquilibrage de charge."},
                "n'est pas dans la portée",
            ),
        ],
    )
    def test_les_refus_de_la_reaffectation(self, plateforme, corps, message):
        direction = _client(plateforme, "b.mballa@cga-brcg.cm")
        reponse = direction.post(f"/transverse/dossiers/{BATIMENT}/reaffectation", json=corps)
        assert reponse.status_code == 422, reponse.text
        assert message in reponse.json()["detail"]
        # Rien n'a bougé : le refus arrive avant tout enregistrement.
        acces = {
            h["compte"]
            for h in direction.get(
                f"/transverse/dossiers/{BATIMENT}/acces", params={"a_la_date": "2026-08-17"}
            ).json()
        }
        assert "C-004" in acces

    # Pas 109 : le dépôt sans l'exigence du mois transmis, ce cas éprouve la charge.
    @pytest.mark.usefixtures("depot_tva_sans_revue_exigee")
    def test_une_echeance_deposee_ne_compte_plus_dans_le_mois(self, plateforme):
        from tests.test_depots_reconnus import _deposer_juillet

        direction = _client(plateforme, "b.mballa@cga-brcg.cm")

        def echeances():
            vue = direction.get("/pilotage/charge-et-production?a_la_date=2026-08-10").json()
            return vue["echeances_du_mois"]

        avant = echeances()
        _deposer_juillet(_client(plateforme, "a.bouba@cga-brcg.cm"))
        assert echeances() == avant - 1

    def test_permissions(self, plateforme):
        comptable = _client(plateforme, "l.fotso@cga-brcg.cm")
        assert comptable.get("/pilotage/charge-et-production").status_code == 403
        corps = {"de": "H-004", "vers": "H-005", "motif": "Je me décharge du dossier."}
        assert (
            comptable.post(f"/transverse/dossiers/{BATIMENT}/reaffectation", json=corps).status_code
            == 403
        )
        assert (
            _client(plateforme, "a.bouba@cga-brcg.cm")
            .get("/pilotage/charge-et-production")
            .status_code
            == 403
        )
