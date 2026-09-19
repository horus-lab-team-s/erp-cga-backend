"""Relancer un adhérent des pièces qui manquent à son mois (pas 111).

Maquette « Parcours comptable », vue F.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **L'attente déduite** : relevé bancaire, mouvement sans pièce, série habituelle, obligation du
   mois, anomalie bloquante, demande ouverte ; le rattachement à une demande existante ; les
   origines inactives.
2. **Les réglages** qui mentiraient : un modèle sans date limite, un canal inactif sans motif ou
   coché d'office.
3. **La demande idempotente** de la collecte.
4. **Les routes** : l'aperçu rendu par le backend, l'envoi (demandes créées, relances tracées,
   adhérent prévenu dans son espace et par courriel), l'historique lu ou non lu, et les refus :
   pièce hors attente, canal inactif, dossier sans adhérent, double envoi le même jour.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.collecte.api import (
    DemandeRefusee,
    DepotDemandesMemoire,
    TypePiece,
    demander_une_piece,
    vider_les_magasins_de_la_collecte,
)
from app.contextes.comptabilite.api import vider_les_ecritures_en_memoire
from app.contextes.pilotage.adaptateurs.sortant.pieces_manquantes_yaml import (
    charger_les_reglages_des_pieces_manquantes,
)
from app.contextes.pilotage.domaine.pieces_manquantes import (
    AnomalieBloquante,
    DemandeObservee,
    LigneDeReleveOuverte,
    ModeleDeRelance,
    OrigineDAttente,
    PieceObservee,
    ReglageDeCanal,
    ReglagesDesPiecesManquantes,
    attentes_du_mois,
    code_d_attente,
    rendre_la_relance,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

JUILLET = (date(2026, 7, 1), date(2026, 7, 31))
REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


def _piece(identifiant, emetteur, jour, montant="1000"):
    return PieceObservee(
        identifiant=identifiant,
        reference=None,
        emetteur=emetteur,
        montant=Decimal(montant),
        date=jour,
    )


def _attentes(**faits):
    return attentes_du_mois(
        du=JUILLET[0],
        au=JUILLET[1],
        pieces=faits.get("pieces", []),
        demandes_ouvertes=faits.get("demandes", []),
        journaux_sans_releve=faits.get("journaux", []),
        lignes_ouvertes=faits.get("lignes", []),
        obligations_du_mois=faits.get("obligations", []),
        anomalies=faits.get("anomalies", []),
        reglages=faits.get(
            "reglages",
            ReglagesDesPiecesManquantes(
                pieces_par_obligation={"CNPS": "Bordereau de paiement CNPS"}
            ),
        ),
    )


class TestLAttenteDeduite:
    def test_le_code_est_stable_et_lisible(self):
        assert code_d_attente("serie", "QUINCAILLERIE DU WOURI") == "serie-quincaillerie-du-wouri"
        assert code_d_attente("serie", "SABLIÈRE DU MOUNGO") == "serie-sabliere-du-moungo"

    def test_chaque_origine(self):
        attentes = _attentes(
            journaux=["BQ"],
            lignes=[
                LigneDeReleveOuverte(
                    journal="BQ",
                    rang=3,
                    date=date(2026, 7, 26),
                    libelle="VIR ENEO",
                    montant=Decimal(318400),
                ),
                LigneDeReleveOuverte(
                    journal="BQ",
                    rang=4,
                    date=date(2026, 8, 2),
                    libelle="Hors mois",
                    montant=Decimal(1),
                ),
            ],
            obligations=["CNPS", "TVA"],
            anomalies=[
                AnomalieBloquante(
                    piece="PJ-9",
                    reference="F-9",
                    emetteur="NÉGOCE MOUNGO",
                    montant=Decimal(536625),
                    regle="FAC-ID-003",
                )
            ],
        )
        vues = [(a.origine, a.code, a.libelle, a.montant_estime) for a in attentes]
        assert attentes[-1].regle == "FAC-ID-003"
        assert vues == [
            (
                OrigineDAttente.RELEVE_BANCAIRE,
                "releve-bq",
                "Relevé bancaire du journal BQ, juillet 2026",
                None,
            ),
            (
                OrigineDAttente.MOUVEMENT_SANS_PIECE,
                "mouvement-bq-2026-07-26-3",
                "Justificatif du mouvement du 26/07 : VIR ENEO",
                Decimal(318400),
            ),
            (
                OrigineDAttente.OBLIGATION_DU_MOIS,
                "obligation-cnps",
                "Bordereau de paiement CNPS, juillet 2026",
                None,
            ),
            (
                OrigineDAttente.ANOMALIE_BLOQUANTE,
                "rectificative-pj-9",
                # Pas 112 : le code de la règle ne part plus chez l'adhérent ; il reste au cabinet.
                "Facture rectificative de NÉGOCE MOUNGO (facture F-9)",
                Decimal(536625),
            ),
        ]

    def test_la_serie_habituelle(self):
        habituel = [
            _piece(f"P{m}", "ENEO", date(2026, m, 10), montant=str(300 * m)) for m in (4, 5, 6)
        ]
        attentes = _attentes(pieces=habituel)
        assert [(a.code, a.montant_estime) for a in attentes] == [("serie-eneo", Decimal(1500))]
        # Présent ce mois-ci : rien ne manque.
        assert _attentes(pieces=[*habituel, _piece("P7", "eneo ", date(2026, 7, 12))]) == []
        # Vu deux mois sur trois : pas encore une série.
        assert _attentes(pieces=habituel[1:]) == []
        # Vu en janvier, février et mars : hors des trois mois observés (avril à juin).
        anciens = [_piece(f"Q{m}", "CAMWATER", date(2026, m, 10)) for m in (1, 2, 3)]
        assert _attentes(pieces=anciens) == []
        souple = ReglagesDesPiecesManquantes(presence_minimum=2)
        assert [a.code for a in _attentes(pieces=habituel[1:], reglages=souple)] == ["serie-eneo"]

    def test_les_demandes_ouvertes_se_rattachent_et_s_ajoutent(self):
        demandes = [
            DemandeObservee(
                identifiant="ATT-2026-07-releve-bq",
                motif="Relevé",
                type_attendu="RELEVE_BANCAIRE",
                demandee_le=date(2026, 8, 2),
            ),
            DemandeObservee(
                identifiant="DP-2",
                motif="Rectificative",
                type_attendu="FACTURE_ACHAT",
                demandee_le=date(2026, 7, 20),
                piece_a_rectifier="PJ-9",
            ),
            DemandeObservee(
                identifiant="DP-3",
                motif="Contrat de bail",
                type_attendu="CONTRAT",
                demandee_le=date(2026, 7, 5),
            ),
            DemandeObservee(
                identifiant="DP-4",
                motif="Demandée en août",
                type_attendu="AUTRE",
                demandee_le=date(2026, 8, 3),
            ),
        ]
        attentes = _attentes(
            journaux=["BQ"],
            demandes=demandes,
            anomalies=[
                AnomalieBloquante(
                    piece="PJ-9", reference="F-9", emetteur="X", montant=None, regle="R"
                )
            ],
        )
        assert [(a.code, a.demande) for a in attentes] == [
            ("releve-bq", "ATT-2026-07-releve-bq"),
            ("rectificative-pj-9", "DP-2"),
            ("demande-dp-3", "DP-3"),
        ]
        sans = ReglagesDesPiecesManquantes(origines={"RELEVE_BANCAIRE"})
        assert [a.code for a in _attentes(journaux=["BQ"], demandes=demandes, reglages=sans)] == [
            "releve-bq"
        ]
        seules = ReglagesDesPiecesManquantes(origines={"DEMANDE_OUVERTE"})
        assert [a.code for a in _attentes(journaux=["BQ"], demandes=demandes, reglages=seules)] == [
            "demande-dp-3",
            "demande-dp-2",
        ]


class TestLesReglages:
    def test_le_referentiel_et_les_refus(self):
        lus = charger_les_reglages_des_pieces_manquantes(REFERENTIEL)
        assert lus.source == "pilotage/pieces_manquantes.yaml"
        assert [m.code for m in lus.modeles] == ["amiable-pieces-du-mois", "ferme-avant-echeance"]
        assert not lus.canal("WHATSAPP").actif and lus.canal("WHATSAPP").motif
        assert (
            charger_les_reglages_des_pieces_manquantes(Path("/nulle/part")).source
            == "valeurs par défaut"
        )
        with pytest.raises(ValidationError, match="date limite"):
            ModeleDeRelance(
                code="muet", libelle="Muet", introduction="Il manque :", conclusion="Merci."
            )
        with pytest.raises(ValidationError, match="dire pourquoi"):
            ReglageDeCanal(canal="WHATSAPP", actif=False)
        with pytest.raises(ValidationError, match="coché d'office"):
            ReglageDeCanal(canal="WHATSAPP", actif=False, motif="fermé", par_defaut=True)
        with pytest.raises(ValidationError):
            ReglagesDesPiecesManquantes(mois_observes=2, presence_minimum=3)

    def test_le_rendu(self):
        rendu = rendre_la_relance(
            ReglagesDesPiecesManquantes().modeles[0],
            attentes=_attentes(journaux=["BQ"]),
            du=JUILLET[0],
            date_limite=date(2026, 8, 12),
            signataire="Léonard FOTSO",
            cabinet="CGA Broad Range",
        )
        assert rendu.texte.splitlines() == [
            "Pour terminer votre mois de juillet 2026, il nous manque encore :",
            "• Relevé bancaire du journal BQ, juillet 2026",
            "Vous pouvez les déposer depuis votre espace, en photo. Sans ces pièces avant le "
            "12/08/2026, votre déclaration sera établie sans elles.",
            "Léonard FOTSO, CGA Broad Range",
        ]


class TestLaDemandeIdempotente:
    def test_la_meme_attente_rend_la_meme_demande(self):
        with etabli("CGA-BRCG"):
            depot = DepotDemandesMemoire()
            valeurs = dict(
                identifiant="ATT-2026-07-releve-bq",
                type_attendu=TypePiece.RELEVE_BANCAIRE,
                motif="Relevé BQ",
                attendue_pour=None,
                le=date(2026, 8, 2),
                par="C-004",
                demandes=depot,
            )
            premiere = demander_une_piece(entreprise="M081234567890P", **valeurs)
            assert demander_une_piece(entreprise="M081234567890P", **valeurs) == premiere
            assert len(depot.ouvertes("M081234567890P")) == 1
            with pytest.raises(DemandeRefusee, match="autre dossier"):
                demander_une_piece(entreprise="M071122334455J", **valeurs)


# ── Les routes ────────────────────────────────────────────────────────────────

BATIMENT = "M081234567890P"
AGRO = "M065544332211L"
COMPTABLE = "l.fotso@cga-brcg.cm"
CHARGEE = "p.moukouri@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
AUTRE = "c.ndongo@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
RELANCE = f"/pilotage/dossiers/{BATIMENT}/relance"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    vider_les_ecritures_en_memoire()
    vider_les_magasins_de_la_collecte()
    # Le 5 août : juillet est fini, son échéance de TVA (15/08) n'est pas encore passée.
    with horloge_figee(datetime(2026, 8, 5, 9, 0)):
        yield
    vider_les_ecritures_en_memoire()
    vider_les_magasins_de_la_collecte()


def _client(courriel):
    client = TestClient(creer_application())
    assert (
        client.post(
            "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
        ).status_code
        == 200
    )
    return client


def _virement_de_juillet(client):
    """Une banque mouvementée en juillet, sans relevé importé : le relevé devient attendu."""
    saisie = client.post(
        f"/comptabilite/dossiers/{BATIMENT}/ecritures",
        json={
            "journal": "BQ",
            "exercice": "2026",
            "date_operation": "2026-07-28",
            "libelle": "Règlement QUINCAILLERIE",
            "piece_justificative": "VIR-0728",
            "lignes": [
                {"compte": "401", "libelle": "x", "sens": "DEBIT", "montant": "236000"},
                {"compte": "521", "libelle": "x", "sens": "CREDIT", "montant": "236000"},
            ],
        },
    ).json()
    assert (
        client.post(
            f"/comptabilite/dossiers/{BATIMENT}/ecritures/2026/BQ/{saisie['numero']}/validation"
        ).status_code
        == 200
    )


class TestLesRoutes:
    def test_l_apercu_puis_l_envoi_puis_la_lecture(self):
        from app.contextes.transverse.adaptateurs.entrant.dependances import service_de_notification

        comptable = _client(COMPTABLE)
        _virement_de_juillet(comptable)
        vue = comptable.get(RELANCE, params={"mois": "2026-07"}).json()
        codes = [a["code"] for a in vue["attentes"]]
        assert codes == ["releve-bq", "rectificative-pj-2026-0013"]
        assert vue["attentes"][1]["demande"] == "DP-2026-002"
        assert vue["date_limite"] == "2026-08-12" and vue["date_limite_estimee"] is False
        assert vue["destinataires"] == ["Jean-Pierre NKOA"]
        assert vue["selection"] == codes

        reduit = comptable.get(
            RELANCE,
            params={
                "mois": "2026-07",
                "attentes": "releve-bq,inconnue",
                "modele": "ferme-avant-echeance",
            },
        ).json()
        assert reduit["selection"] == ["releve-bq"] and reduit["modele"] == "ferme-avant-echeance"
        assert reduit["apercu"]["liste"] == ["Relevé bancaire du journal BQ, juillet 2026"]
        assert reduit["apercu"]["introduction"].startswith("Malgré nos précédentes demandes")

        envoi = comptable.post(
            RELANCE,
            json={
                "mois": "2026-07",
                "attentes": codes,
                "canaux": ["APPLICATION", "COURRIEL"],
                "modele": "amiable-pieces-du-mois",
            },
        )
        assert envoi.status_code == 201, envoi.text
        resultat = envoi.json()
        assert resultat["demandes_creees"] == ["ATT-2026-07-releve-bq"]
        assert resultat["demandes_relancees"] == ["ATT-2026-07-releve-bq", "DP-2026-002"]
        assert "• Relevé bancaire du journal BQ, juillet 2026" in resultat["message"]
        courriels = service_de_notification().derniers("piece.relance")
        assert courriels[0].destinataire == "jp.nkoa@batimentplus.cm"
        assert "12/08/2026" in courriels[0].contexte["conclusion"]

        demandes = {
            d["identifiant"]: d
            for d in comptable.get("/collecte/demandes", params={"entreprise": BATIMENT}).json()
        }
        assert [c for _, c in demandes["DP-2026-002"]["relances"]] == ["PORTAIL", "COURRIEL"]
        assert demandes["ATT-2026-07-releve-bq"]["attendue_pour"] == "2026-08-12"

        historique = comptable.get(RELANCE, params={"mois": "2026-07"}).json()["historique"]
        assert [(h["canal"], h["etat"]) for h in historique] == [
            ("APPLICATION", "Non lu"),
            ("COURRIEL", "Envoyé"),
        ]

        adherent = _client(ADHERENT)
        notifications = adherent.get("/transverse/notifications").json()["notifications"]
        relance = next(
            n for n in notifications if n["titre"] == "Pièces manquantes pour juillet 2026"
        )
        assert "2 pièce(s) attendue(s) avant le 12/08/2026" in relance["texte"]
        assert (
            adherent.post(
                "/transverse/notifications/lecture", json={"jusqu_au_rang": relance["rang"]}
            ).status_code
            == 200
        )
        lu = comptable.get(RELANCE, params={"mois": "2026-07"}).json()["historique"]
        assert lu[0]["etat"] == "Lu"

        # Le même jour, par le même canal : rien ne part, et rien n'est créé.
        refus = comptable.post(
            RELANCE,
            json={
                "mois": "2026-07",
                "attentes": codes,
                "canaux": ["APPLICATION"],
                "modele": "amiable-pieces-du-mois",
            },
        )
        assert refus.status_code == 409 and "déjà relancée aujourd'hui" in refus.json()["detail"]
        assert len(comptable.get(RELANCE, params={"mois": "2026-07"}).json()["historique"]) == 2

    def test_les_refus(self):
        comptable = _client(COMPTABLE)
        corps = {
            "mois": "2026-07",
            "attentes": ["rectificative-pj-2026-0013"],
            "canaux": ["APPLICATION"],
            "modele": "amiable-pieces-du-mois",
        }
        inconnue = comptable.post(RELANCE, json={**corps, "attentes": ["serie-inventee"]})
        assert inconnue.status_code == 422 and "hors de l'attente" in inconnue.json()["detail"]
        whatsapp = comptable.post(RELANCE, json={**corps, "canaux": ["WHATSAPP"]})
        assert (
            whatsapp.status_code == 422
            and "WHATSAPP inactif : Compte de la plateforme" in whatsapp.json()["detail"]
        )
        modele = comptable.post(RELANCE, json={**corps, "modele": "inconnu"})
        assert modele.status_code == 422
        agro = _client("c.ndongo@cga-brcg.cm")
        vue = agro.get(f"/pilotage/dossiers/{AGRO}/relance", params={"mois": "2026-07"})
        assert vue.status_code == 404 or vue.json()["destinataires"] == []
        sans = _client(CHARGEE).post(
            f"/pilotage/dossiers/{AGRO}/relance",
            json={**corps, "attentes": ["demande-dp-2026-011"]},
        )
        assert sans.status_code == 422 and "aucun compte adhérent actif" in sans.json()["detail"]

    def test_un_releve_importe_n_est_plus_attendu_mais_sa_ligne_ouverte_l_est(self):
        from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
            depot_des_rapprochements,
            vider_les_rapprochements,
        )
        from app.contextes.comptabilite.domaine.entites import Sens
        from app.contextes.comptabilite.domaine.rapprochement import (
            LigneDeReleve,
            RapprochementBancaire,
            StatutRapprochement,
        )

        comptable = _client(COMPTABLE)
        _virement_de_juillet(comptable)
        vider_les_rapprochements()
        with etabli("CGA-BRCG"):
            depot_des_rapprochements().enregistrer(
                RapprochementBancaire(
                    identifiant="RB-BQ-JUILLET",
                    dossier=BATIMENT,
                    journal="BQ",
                    compte="521",
                    exercice="2026",
                    du=date(2026, 7, 1),
                    au=date(2026, 7, 31),
                    solde_initial=Decimal(0),
                    solde_final=Decimal(-5000),
                    lignes=[
                        LigneDeReleve(
                            rang=1,
                            date=date(2026, 7, 30),
                            libelle="PRLV ENEO",
                            montant=Decimal(5000),
                            sens=Sens.CREDIT,
                        )
                    ],
                    source="saisie",
                    importe_par="C-004",
                    importe_le=datetime(2026, 8, 2, 9),
                    statut=StatutRapprochement.EN_COURS,
                )
            )
        try:
            codes = [
                a["code"]
                for a in comptable.get(RELANCE, params={"mois": "2026-07"}).json()["attentes"]
            ]
        finally:
            vider_les_rapprochements()
        assert "releve-bq" not in codes
        assert "mouvement-bq-2026-07-30-1" in codes

    def test_une_echeance_passee_reporte_la_date_annoncee(self):
        with horloge_figee(datetime(2026, 9, 17, 10, 0)):
            # La session s'ouvre à la même heure : ouverte le 5 août, elle aurait expiré.
            vue = _client(COMPTABLE).get(RELANCE, params={"mois": "2026-07"}).json()
        comptable = _client(COMPTABLE)
        assert vue["echeance_depassee"] is True and vue["date_limite"] == "2026-09-22"
        assert "avant le 22/09/2026" in vue["apercu"]["conclusion"]
        avant = comptable.get(RELANCE, params={"mois": "2026-07"}).json()
        assert avant["echeance_depassee"] is False and avant["date_limite"] == "2026-08-12"

    def test_les_acces(self):
        assert _client(CHARGEE).get(RELANCE, params={"mois": "2026-07"}).status_code == 200
        assert _client(DIRECTION).get(RELANCE, params={"mois": "2026-07"}).status_code == 403
        assert _client(AUTRE).get(RELANCE, params={"mois": "2026-07"}).status_code == 404
        assert _client(COMPTABLE).get(RELANCE, params={"mois": "2026-13"}).status_code == 422


class TestSurPostgresql:
    pytestmark = exige_postgresql

    def test_envoyer_et_relire_l_historique(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE)
        vue = client.get(RELANCE, params={"mois": "2026-07"}).json()
        codes = [a["code"] for a in vue["attentes"]]
        assert codes
        envoi = client.post(
            RELANCE,
            json={
                "mois": "2026-07",
                "attentes": codes,
                "canaux": ["APPLICATION"],
                "modele": "amiable-pieces-du-mois",
            },
        )
        assert envoi.status_code == 201, envoi.text
        historique = client.get(RELANCE, params={"mois": "2026-07"}).json()["historique"]
        assert [(h["canal"], h["etat"]) for h in historique] == [("APPLICATION", "Non lu")]
