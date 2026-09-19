"""« Mon entreprise » et « Mes documents » : la vue E de l'espace adhérent (pas 114).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **L'interlocuteur** : le chargé de clientèle avant le comptable, l'habilitation nommée sur le
   dossier avant la transverse, la plus récente ; personne plutôt qu'un nom inventé.
2. **Les réglages** : les natures de la maquette toujours présentes, les doublons et la validation
   sans nom refusés, le fichier livré qui nomme toutes les formes, tous les centres, tous les régimes.
3. **Le signalement** : daté au journal, annoncé au chargé de clientèle, refusé en double, au
   collaborateur, hors du dossier ; il ne change rien au dossier.
4. **Les documents** : les accusés du seul dossier, avec leur titre, leur période et leur montant.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.portefeuille.adaptateurs.sortant.espace_adherent_yaml import (
    charger_la_fiche_adherent,
)
from app.contextes.portefeuille.api import CentreRattachement, RegimeFiscal
from app.contextes.portefeuille.domaine.entites import FormeJuridique
from app.contextes.portefeuille.domaine.espace_adherent import (
    CandidatInterlocuteur,
    NatureDeChangement,
    ReglagesDeLaFicheAdherent,
    SignalementDeChangement,
    choisir_l_interlocuteur,
    signalement_en_double,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


def _candidat(compte, role, nommee=True, debut=date(2024, 1, 1)):
    return CandidatInterlocuteur(
        compte=compte,
        role=role,
        nommee_sur_le_dossier=nommee,
        debut=debut,
        nom=f"Nom {compte}",
        courriel=f"{compte}@cga.cm",
    )


class TestLInterlocuteur:
    def test_le_charge_de_clientele_avant_le_comptable(self):
        choisi = choisir_l_interlocuteur(
            [_candidat("C-4", "COMPTABLE"), _candidat("C-7", "CHARGE_CLIENTELE"), _candidat("C-3", "REVISEUR")]
        )
        assert choisi.nom == "Nom C-7" and choisi.role == "CHARGE_CLIENTELE"

    def test_nomme_sur_le_dossier_puis_le_plus_recent(self):
        choisi = choisir_l_interlocuteur(
            [
                _candidat("C-1", "CHARGE_CLIENTELE", nommee=False, debut=date(2026, 1, 1)),
                _candidat("C-2", "CHARGE_CLIENTELE", debut=date(2023, 1, 1)),
                _candidat("C-9", "CHARGE_CLIENTELE", debut=date(2025, 1, 1)),
            ]
        )
        assert choisi.nom == "Nom C-9"

    def test_sans_interlocuteur_personne(self):
        assert choisir_l_interlocuteur([_candidat("C-3", "REVISEUR"), _candidat("C-1", "DIRECTION")]) is None
        # Le comptable, faute de chargé de clientèle.
        assert choisir_l_interlocuteur([_candidat("C-4", "COMPTABLE")]).role == "COMPTABLE"


class TestLesReglages:
    def test_les_natures_de_la_maquette_restent(self):
        reglages = ReglagesDeLaFicheAdherent(
            natures=[{"nature": "ADRESSE", "libelle": "Déménagement", "aide": "Le cabinet vérifie le centre."}]
        )
        assert {n.nature for n in reglages.natures} == set(NatureDeChangement)
        assert reglages.nature(NatureDeChangement.ADRESSE).libelle == "Déménagement"

    def test_ce_qui_mentirait(self):
        with pytest.raises(ValidationError, match="réglée deux fois"):
            ReglagesDeLaFicheAdherent(
                natures=[
                    {"nature": "ADRESSE", "libelle": "Adresse", "aide": "Une aide assez longue."},
                    {"nature": "ADRESSE", "libelle": "Adresse bis", "aide": "Une aide assez longue."},
                ]
            )
        regime = {"regime": "IGS", "titre": "Impôt", "explication": "Une explication."}
        with pytest.raises(ValidationError, match="expliqué deux fois"):
            ReglagesDeLaFicheAdherent(regimes=[regime, regime])
        with pytest.raises(ValidationError, match="nomment qui les a validés"):
            ReglagesDeLaFicheAdherent(statut="VALIDE")

    def test_le_fichier_livre_nomme_toutes_les_formes_centres_et_regimes(self):
        reglages = charger_la_fiche_adherent(REFERENTIEL)
        assert reglages.source == "portefeuille/espace_adherent.yaml" and reglages.statut == "A_VALIDER"
        assert set(reglages.formes) == {f.value for f in FormeJuridique}
        assert set(reglages.centres) == {c.value for c in CentreRattachement}
        assert {r.regime for r in reglages.regimes} == {r.value for r in RegimeFiscal}
        assert charger_la_fiche_adherent(Path("/nulle/part")).formes == {}


class TestLeDoubleAppui:
    def test_meme_nature_meme_message_meme_jour_meme_compte(self):
        premier = SignalementDeChangement(
            nature=NatureDeChangement.ADRESSE, message="Bonamoussadi", le=datetime(2026, 8, 20, 9), par="A-001"
        )
        assert signalement_en_double(premier.model_copy(update={"message": " Bonamoussadi "}), [premier])
        for autre in (
            {"message": "Akwa"},
            {"nature": NatureDeChangement.TELEPHONE},
            {"le": datetime(2026, 8, 21, 9)},
            {"par": "A-009"},
        ):
            assert not signalement_en_double(premier.model_copy(update=autre), [premier])


# ── Les routes ────────────────────────────────────────────────────────────────

BATIMENT = "M081234567890P"
ADHERENT = "jp.nkoa@batimentplus.cm"
AUTRE_ADHERENT = "mc.essomba@lacolombe.cm"
CHARGEE = "p.moukouri@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
FICHE = f"/portefeuille/entreprises/{BATIMENT}/mon-entreprise"
SIGNALER = f"/portefeuille/entreprises/{BATIMENT}/signalements"
DOCUMENTS = f"/obligations/dossiers/{BATIMENT}/mes-documents"


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


class TestLesRoutes:
    def test_la_fiche_dans_les_mots_de_l_adherent(self):
        fiche = _client(ADHERENT).get(FICHE).json()
        assert fiche["forme"] == "Société à responsabilité limitée"
        assert fiche["centre"] == "Centre divisionnaire des impôts"
        assert fiche["regime"]["titre"] == "Régime du réel" and fiche["regime"]["depuis"] == "2023-01-01"
        assert fiche["adhesion_numero"] == "ADH-2022-014"
        assert fiche["dirigeants"] == [{"nom": "NKOA Jean-Pierre", "qualite": "Gérant"}]
        assert fiche["interlocuteur"]["nom"] == "Patricia MOUKOURI"
        assert fiche["explications_validees"] is False
        assert _client(AUTRE_ADHERENT).get(FICHE).status_code == 404

    def test_l_interlocuteur_du_dossier_actif_et_nomme(self):
        from app.contextes.transverse.api import Habilitation, MotifHabilitation, Role, atelier

        boutique = atelier()
        # Une chargée de clientèle transverse, plus récente : la chargée nommée sur le dossier reste.
        boutique.habilitations.enregistrer(
            Habilitation(
                identifiant="H-CC-TRANSVERSE", compte="C-001", role=Role.CHARGE_CLIENTELE,
                portee=None, debut=date(2026, 3, 1),
                motif=MotifHabilitation.RECRUTEMENT, accordee_par="C-002",
            )
        )
        adherent = _client(ADHERENT)
        assert adherent.get(FICHE).json()["interlocuteur"]["nom"] == "Patricia MOUKOURI"
        # Son compte suspendu, elle n'est plus l'interlocutrice : la transverse prend le relais.
        boutique.comptes.enregistrer(boutique.comptes.lire("C-007").suspendre())
        relais = adherent.get(FICHE).json()["interlocuteur"]
        assert relais["nom"] == "Bernadette MBALLA" and relais["telephone"] == "+237699112233"

    def test_seuls_les_dirigeants_en_fonction(self, monkeypatch):
        from app.contextes.portefeuille.adaptateurs.entrant import routes_espace_adherent
        from app.contextes.portefeuille.adaptateurs.entrant.routes_http import _lire
        from app.contextes.portefeuille.domaine.entites import Dirigeant

        def avec_un_ancien(niu):
            dossier = _lire(niu)
            ancien = Dirigeant(nom="ANCIEN Gérant", qualite="Gérant", depuis=date(2019, 1, 1), jusqu_a=date(2021, 12, 31))
            futur = Dirigeant(nom="FUTUR Cogérant", qualite="Cogérant", depuis=date(2027, 1, 1))
            return dossier.model_copy(update={"dirigeants": [*dossier.dirigeants, ancien, futur]})

        monkeypatch.setattr(routes_espace_adherent, "_lire", avec_un_ancien)
        fiche = _client(ADHERENT).get(FICHE).json()
        assert [d["nom"] for d in fiche["dirigeants"]] == ["NKOA Jean-Pierre"]

    def test_le_signalement_arrive_au_charge_de_clientele_et_ne_change_rien(self):
        adherent = _client(ADHERENT)
        avant = adherent.get(FICHE).json()
        corps = {"nature": "ADRESSE", "message": "Nous déménageons à Bonamoussadi le 1er septembre."}
        envoi = adherent.post(SIGNALER, json=corps)
        assert envoi.status_code == 201, envoi.text
        assert envoi.json()["libelle"] == "Adresse"
        double = adherent.post(SIGNALER, json=corps)
        assert double.status_code == 409 and "déjà été signalé" in double.json()["detail"]

        apres = adherent.get(FICHE).json()
        assert [s["message"] for s in apres["signalements"]] == [corps["message"]]
        assert apres["siege"] == avant["siege"]

        avis = _client(CHARGEE).get("/transverse/notifications").json()["notifications"]
        recu = next(n for n in avis if n["titre"].startswith("Changement signalé"))
        assert recu["titre"] == "Changement signalé : Adresse, SARL BATIMENT PLUS"
        assert corps["message"] in recu["texte"] and recu["lien"] == f"/portefeuille/{BATIMENT}"

    def test_les_refus(self):
        corps = {"nature": "TELEPHONE", "message": "699 00 00 00"}
        assert _client(COMPTABLE).post(SIGNALER, json=corps).status_code == 403
        assert _client(CHARGEE).post(SIGNALER, json=corps).status_code == 403
        assert _client(AUTRE_ADHERENT).post(SIGNALER, json=corps).status_code == 404
        assert _client(ADHERENT).post(SIGNALER, json={**corps, "message": "      "}).status_code == 422

    def test_les_signalements_montres_sont_les_plus_recents(self, monkeypatch):
        from app.contextes.portefeuille.adaptateurs.entrant import routes_espace_adherent

        monkeypatch.setattr(
            routes_espace_adherent,
            "_reglages",
            lambda: ReglagesDeLaFicheAdherent(signalements_montres=2),
        )
        adherent = _client(ADHERENT)
        for rang in range(3):
            assert adherent.post(SIGNALER, json={"nature": "AUTRE", "message": f"Changement {rang}"}).status_code == 201
        fiche = adherent.get(FICHE).json()
        assert [s["message"] for s in fiche["signalements"]] == ["Changement 2", "Changement 1"]
        # Sans fichier : les codes, pas une traduction inventée.
        assert fiche["forme"] == "SARL" and fiche["regime"]["titre"] == "REEL"
        assert fiche["regime"]["explication"] is None

    def test_les_documents_sont_les_accuses_du_seul_dossier(self):
        from tests.test_teledeclaration import _renforcer

        adherent = _client(ADHERENT)
        assert adherent.get(DOCUMENTS).json()["documents"] == []
        reviseur = _client("a.bouba@cga-brcg.cm")
        _renforcer(reviseur)
        depot = reviseur.post(
            f"/obligations/dossiers/{BATIMENT}/depots",
            json={
                "code_obligation": "CNPS",
                "periode_debut": "2026-07-01",
                "periode_fin": "2026-07-31",
                "numero": "CNPS-2026-DIPE-00418",
                "depose_le": "2026-08-12T09:30:00",
                "montant_constate": "184500",
            },
        )
        assert depot.status_code in (200, 201), depot.text
        # Le dépôt d'un autre dossier ne figure pas parmi les documents de celui-ci.
        autre = reviseur.post(
            "/obligations/dossiers/M071122334455J/depots",
            json={
                "code_obligation": "DSF",
                "periode_debut": "2025-01-01",
                "periode_fin": "2025-12-31",
                "numero": "DSF-COLOMBE-2025",
                "depose_le": "2026-03-10T10:00:00",
            },
        )
        assert autre.status_code in (200, 201), autre.text
        (document,) = adherent.get(DOCUMENTS).json()["documents"]
        assert document["numero"] == "CNPS-2026-DIPE-00418"
        assert document["titre"] == "Cotisations sociales CNPS" and document["periode"] == "juillet 2026"
        assert document["guichet"] == "CNPS" and Decimal(document["montant_constate"]) == Decimal(184500)
        assert document["verifiable"] is False
        assert _client(AUTRE_ADHERENT).get(DOCUMENTS).status_code == 404
