"""Le cycle d'une demande de pièce : demander, satisfaire, classer, tracer la relance.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 74)

Le domaine disait « le cabinet ne peut relancer que ce qu'il sait attendre » et « une
relance non tracée n'a pas eu lieu ». Aucune route ne créait, ne satisfaisait, ne
classait ni ne traçait une demande : une pièce reçue laissait sa demande ouverte et
relancée, et la liste de défense du Centre restait vide. L'écran d'une pièce offrait
« Demander une facture rectificative », sans action.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import (
    DepotDemandesMemoire,
    DepotPiecesMemoire,
)
from app.contextes.collecte.adaptateurs.sortant.magasins_memoire import (
    pieces_en_memoire,
    vider_les_magasins_de_la_collecte,
)
from app.contextes.collecte.api import (
    CanalDepot,
    DemandePiece,
    PieceJustificative,
    StatutDemande,
    TypePiece,
)
from app.contextes.collecte.application.demandes_de_piece import (
    DemandeRefusee,
    demander_une_rectificative,
    satisfaire_une_demande,
)
from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    Habilitation,
    MotifHabilitation,
    Role,
    atelier,
    reinitialiser_atelier,
)
from app.main import creer_application

LE_JOUR = date(2026, 9, 14)
BATIMENT = "M081234567890P"
#: Une facture de BATIMENT PLUS, lue, sans demande ouverte.
PIECE_LIBRE = "PJ-2026-0024"
#: Une facture de BATIMENT PLUS dont la rectificative est déjà demandée (DP-2026-002).
PIECE_DEJA_DEMANDEE = "PJ-2026-0013"
#: Une facture de la BOULANGERIE : un autre dossier.
PIECE_D_AILLEURS = "PJ-2026-0003"
MOTIF = "Le NIU du fournisseur est absent : la TVA n'est pas déductible en l'état."


def _demande(**champs) -> DemandePiece:
    base = dict(
        identifiant="DP-T", entreprise=BATIMENT, type_attendu=TypePiece.FACTURE_ACHAT,
        motif="Rectificative", demandee_le=date(2026, 9, 1), piece_a_rectifier="PJ-A",
    )
    base.update(champs)
    return DemandePiece(**base)


class TestLesTransitions:
    def test_une_demande_satisfaite_ne_se_satisfait_plus(self):
        satisfaite = _demande().satisfaire("PJ-B", date(2026, 9, 5))
        with pytest.raises(ValueError, match="ne se satisfait plus"):
            satisfaite.satisfaire("PJ-C", date(2026, 9, 6))

    def test_la_piece_a_rectifier_ne_repond_pas_a_sa_propre_demande(self):
        with pytest.raises(ValueError, match="sa propre demande"):
            _demande().satisfaire("PJ-A", date(2026, 9, 5))

    def test_on_ne_satisfait_ni_ne_relance_avant_la_demande(self):
        with pytest.raises(ValueError, match="avant d'avoir été demandée"):
            _demande().satisfaire("PJ-B", date(2026, 8, 30))
        with pytest.raises(ValueError, match="avant la demande"):
            _demande().relancer(date(2026, 8, 30), CanalDepot.WHATSAPP)

    def test_une_relance_ne_se_trace_pas_deux_fois(self):
        relancee = _demande().relancer(date(2026, 9, 8), CanalDepot.WHATSAPP)
        with pytest.raises(ValueError, match="déjà tracée"):
            relancee.relancer(date(2026, 9, 8), CanalDepot.WHATSAPP)
        # Un autre canal le même jour reste une autre relance.
        assert len(relancee.relancer(date(2026, 9, 8), CanalDepot.COURRIEL).relances) == 2

    def test_classer_exige_un_motif_et_ne_se_fait_qu_une_fois(self):
        with pytest.raises(ValueError):
            _demande().classer_sans_suite("   ", LE_JOUR)
        classee = _demande().classer_sans_suite("Opération annulée par le client.", LE_JOUR)
        assert (classee.statut, classee.classee_le) == (StatutDemande.CLASSEE_SANS_SUITE, LE_JOUR)
        with pytest.raises(ValueError, match="ne se classe plus"):
            classee.classer_sans_suite("Encore.", LE_JOUR)


class TestLesCasDUsage:
    @pytest.fixture
    def depots(self):
        return DepotPiecesMemoire.avec_demonstration(), DepotDemandesMemoire.avec_demonstration()

    def test_le_dossier_et_le_type_viennent_de_la_piece(self, depots):
        pieces, demandes = depots
        emise = demander_une_rectificative(
            identifiant="DP-X", piece=PIECE_LIBRE, motif=MOTIF, attendue_pour=None,
            le=LE_JOUR, par="C-004", pieces=pieces, demandes=demandes,
        )
        assert (emise.entreprise, emise.type_attendu) == (BATIMENT, TypePiece.FACTURE_ACHAT)
        assert (emise.piece_a_rectifier, emise.bloquante, emise.demandee_par) == (
            PIECE_LIBRE, True, "C-004"
        )

    def test_une_seule_demande_ouverte_par_piece(self, depots):
        pieces, demandes = depots
        with pytest.raises(DemandeRefusee, match="DP-2026-002"):
            demander_une_rectificative(
                identifiant="DP-X", piece=PIECE_DEJA_DEMANDEE, motif=MOTIF, attendue_pour=None,
                le=LE_JOUR, par="C-004", pieces=pieces, demandes=demandes,
            )

    def test_une_piece_d_un_autre_dossier_ne_satisfait_pas(self, depots):
        pieces, demandes = depots
        with pytest.raises(DemandeRefusee, match="appartient au dossier"):
            satisfaire_une_demande(
                "DP-2026-002", piece=PIECE_D_AILLEURS, le=LE_JOUR,
                pieces=pieces, demandes=demandes,
            )
        assert demandes.par_identifiant("DP-2026-002").ouverte


class TestParLesRoutes:
    @pytest.fixture
    def application(self):
        reinitialiser_atelier()
        vider_les_magasins_de_la_collecte()
        service_de_notification().vider()
        yield creer_application()
        vider_les_magasins_de_la_collecte()

    def _client(self, application, courriel):
        client = TestClient(application)
        assert client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code == 200
        return client

    def test_le_comptable_demande_la_rectificative_et_l_adherent_est_prevenu(self, application):
        comptable = self._client(application, "l.fotso@cga-brcg.cm")
        reponse = comptable.post(
            f"/collecte/pieces/{PIECE_LIBRE}/rectificative", json={"motif": MOTIF}
        )
        assert reponse.status_code == 201, reponse.text
        corps = reponse.json()
        assert corps["demande"]["piece_a_rectifier"] == PIECE_LIBRE
        assert corps["demande"]["entreprise"] == BATIMENT
        assert corps["adherents_prevenus"] == 1
        (message,) = service_de_notification().derniers("piece.rectificative_demandee")
        assert message.destinataire == "jp.nkoa@batimentplus.cm"
        assert message.contexte["reference"] == "F-2026-0435"

        seconde = comptable.post(
            f"/collecte/pieces/{PIECE_LIBRE}/rectificative", json={"motif": MOTIF}
        )
        assert seconde.status_code == 409
        assert corps["demande"]["identifiant"] in seconde.json()["detail"]

    def test_la_piece_recue_satisfait_la_demande_et_elle_ne_se_relance_plus(self, application):
        comptable = self._client(application, "l.fotso@cga-brcg.cm")
        propre = comptable.post(
            "/collecte/demandes/DP-2026-002/satisfaction", json={"piece": PIECE_DEJA_DEMANDEE}
        )
        assert propre.status_code == 409, "la pièce à rectifier s'est satisfaite elle-même"
        # ⚠️ Reçue en juillet, avant la demande du 3 août : elle ne peut pas y répondre.
        anterieure = comptable.post(
            "/collecte/demandes/DP-2026-002/satisfaction", json={"piece": PIECE_LIBRE}
        )
        assert anterieure.status_code == 409, anterieure.text
        assert "avant la demande" in anterieure.json()["detail"]
        for identifiant in ("PJ-2026-RECT", "PJ-2026-FAUSSE"):
            pieces_en_memoire().enregistrer(
                PieceJustificative(
                    identifiant=identifiant, entreprise=BATIMENT, canal=CanalDepot.PORTAIL,
                    depose_le=date(2026, 8, 9), recue_le=datetime(2026, 8, 9, 10, 0),
                    type=TypePiece.FACTURE_ACHAT, reference_document=f"{identifiant}-DOC",
                )
            )
        # ⚠️ Une pièce reçue après la demande, mais elle-même à rectifier, ne rectifie pas
        # une autre facture. Mesuré sur une pièce récente : sur PJ-2026-0019, reçue en
        # juillet, le garde de date parlait le premier et celui-ci ne se mesurait pas.
        visee = comptable.post(
            "/collecte/pieces/PJ-2026-FAUSSE/rectificative", json={"motif": MOTIF}
        ).json()["demande"]["identifiant"]
        a_rectifier = comptable.post(
            "/collecte/demandes/DP-2026-002/satisfaction", json={"piece": "PJ-2026-FAUSSE"}
        )
        assert a_rectifier.status_code == 409
        assert visee in a_rectifier.json()["detail"]

        satisfaite = comptable.post(
            "/collecte/demandes/DP-2026-002/satisfaction", json={"piece": "PJ-2026-RECT"}
        )
        assert satisfaite.status_code == 200, satisfaite.text
        assert satisfaite.json()["statut"] == "SATISFAITE"
        assert comptable.post(
            "/collecte/demandes/DP-2026-002/satisfaction", json={"piece": "PJ-2026-RECT"}
        ).status_code == 409, "une demande satisfaite s'est satisfaite une seconde fois"

    def test_le_charge_de_clientele_trace_et_classe_le_comptable_non(self, application):
        # Un chargé de clientèle n'existe pas dans la démonstration : on l'habilite.
        # ⚠️ Sur C-005, dont la portée est bornée. Le premier essai habilitait C-006,
        # fiscaliste transverse : il voyait tous les dossiers, et le refus hors
        # portefeuille ne se mesurait pas.
        atelier().habilitations.enregistrer(
            Habilitation(
                identifiant="H-CC", compte="C-005", role=Role.CHARGE_CLIENTELE,
                portee=frozenset({BATIMENT}), debut=date(2026, 1, 1),
                motif=MotifHabilitation.RECRUTEMENT, accordee_par="C-002",
            )
        )
        comptable = self._client(application, "l.fotso@cga-brcg.cm")
        assert comptable.post(
            "/collecte/demandes/DP-2026-010/relances", json={"canal": "WHATSAPP"}
        ).status_code == 403

        charge = self._client(application, "c.ndongo@cga-brcg.cm")
        tracee = charge.post("/collecte/demandes/DP-2026-010/relances", json={"canal": "WHATSAPP"})
        assert tracee.status_code == 201, tracee.text
        assert len(tracee.json()["relances"]) == 1
        assert charge.post(
            "/collecte/demandes/DP-2026-010/relances", json={"canal": "WHATSAPP"}
        ).status_code == 409

        hors_perimetre = charge.post(
            "/collecte/demandes/DP-2026-011/classement",
            json={"motif": "Bail résilié, la charge n'existe plus."},
        )
        # 404 et non 403 : la convention du projet, qui ne confirme pas l'existence d'un
        # dossier hors du périmètre de l'appelant.
        assert hors_perimetre.status_code == 404, "classée hors de son portefeuille"

        classee = charge.post(
            "/collecte/demandes/DP-2026-010/classement",
            json={"motif": "Compte bancaire clôturé en juin : aucun relevé de juillet."},
        )
        assert classee.status_code == 200, classee.text
        assert classee.json()["statut"] == "CLASSEE_SANS_SUITE"
        assert charge.post(
            "/collecte/demandes/DP-2026-010/relances", json={"canal": "COURRIEL"}
        ).status_code == 409, "une demande classée s'est relancée"
