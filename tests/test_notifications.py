"""Les notifications, lues au journal d'audit à travers les abonnements (pas 94).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **Les abonnements sont déclarés**, et chargés prudemment : sans fichier, rien ; une clé
  inconnue ou un abonnement sans destinataire refusés au chargement ; une action abonnée
  qu'aucun code n'écrit signalée.
- **Le moteur de destinataires** : permission, périmètre du dossier, compte nommé ; jamais
  l'avis de son propre geste ; le premier abonnement qui correspond, pas le premier qui
  concerne ; le motif d'une entrée jamais lisible.
- **La position de lecture** : un rang, qui n'avance que vers l'avant, borné à la tête.
- **Par l'API**, sur des gestes réels : l'écart qui attend un second regard, le dossier
  confié à un comptable.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.conformite.api import vider_les_ecarts
from app.contextes.transverse.adaptateurs.sortant.abonnements_yaml import (
    charger_les_abonnements,
)
from app.contextes.transverse.adaptateurs.sortant.lectures_des_notifications import (
    vider_les_lectures,
)
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    NIU_DEMO,
    JournalAuditMemoire,
    reinitialiser_atelier,
)
from app.contextes.transverse.domaine.notifications import (
    Abonnement,
    Destinataires,
    PolitiqueDeNotification,
    champs_du_gabarit,
    notifications_pour,
)
from app.infrastructure.config import RACINE_DEPOT, configuration
from app.main import creer_application
from tests.conftest import exige_postgresql, ouvrir_une_session

REVISEUR = "a.bouba@cga-brcg.cm"
FISCALISTE = "r.ebolo@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
COMPTABLE_INDUSTRIE = "l.fotso@cga-brcg.cm"
COMPTABLE_SERVICES = "c.ndongo@cga-brcg.cm"
LE = datetime(2026, 9, 15, 9, 0)


@pytest.fixture(autouse=True)
def journal_neuf():
    reinitialiser_atelier()
    vider_les_lectures()
    vider_les_ecarts()
    yield
    vider_les_lectures()
    vider_les_ecarts()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


class _Lecteur:
    def __init__(self, compte: str, dossiers: set[str] | None = None) -> None:
        self.compte = compte
        self._dossiers = dossiers

    def voit(self, dossier: str) -> bool:
        return self._dossiers is None or dossier in self._dossiers


def _journal(*entrees: tuple[str, str, dict, str | None]) -> list:
    journal = JournalAuditMemoire(locataire="cga")
    for acteur, action, apres, motif in entrees:
        journal.ajouter(
            horodatage=LE,
            acteur=acteur,
            action=action,
            objet_type="essai",
            apres=apres,
            motif=motif,
        )
    return journal.lister()


def _politique(*abonnements: Abonnement) -> PolitiqueDeNotification:
    return PolitiqueDeNotification(abonnements=abonnements, source="essai")


def _abonnement(**surcharges) -> Abonnement:
    valeurs = {
        "action": "essai.fait",
        "destinataires": Destinataires(permissions=("LIRE_DOSSIER",), dossier="dossier"),
        "titre": "Fait sur {dossier}",
        "texte": "texte",
    }
    return Abonnement(**{**valeurs, **surcharges})


# ── Les abonnements ───────────────────────────────────────────────────────────


class TestLesAbonnementsSontDeclares:
    def test_sans_fichier_aucune_notification(self, tmp_path: Path):
        politique = charger_les_abonnements(tmp_path)
        assert politique.abonnements == ()
        assert politique.fenetre_jours == 30
        assert "aucun fichier" in politique.source

    def test_un_abonnement_sans_destinataire_est_refuse(self):
        """Sans règle, il notifierait tout le cabinet de tout."""
        with pytest.raises(ValidationError, match="tout le monde"):
            Destinataires(dossier="dossier")

    def test_une_cle_mal_orthographiee_fait_echouer_le_chargement(self, tmp_path: Path):
        (tmp_path / "notifications").mkdir()
        (tmp_path / "notifications" / "abonnements.yaml").write_text(
            yaml.safe_dump({"fenetre_jour": 10}), encoding="utf-8"
        )
        with pytest.raises(ValidationError):
            charger_les_abonnements(tmp_path)

    def test_chaque_action_abonnee_est_ecrite_par_le_code(self):
        """Une action abonnée qu'aucun code n'écrit ne notifierait jamais, en silence."""
        politique = charger_les_abonnements(configuration().dossier_referentiel)
        assert politique.abonnements
        code = "\n".join(
            f.read_text(encoding="utf-8")
            for f in (RACINE_DEPOT / "Backend_erp_cga" / "app").rglob("*.py")
        )
        orphelines = [a.action for a in politique.abonnements if f'"{a.action}"' not in code]
        assert not orphelines

    def test_aucun_gabarit_ne_lit_le_motif(self):
        politique = charger_les_abonnements(configuration().dossier_referentiel)
        for abonnement in politique.abonnements:
            champs = (
                champs_du_gabarit(abonnement.titre)
                | champs_du_gabarit(abonnement.texte)
                | champs_du_gabarit(abonnement.lien)
            )
            assert "motif" not in champs, abonnement.action


# ── Le moteur de destinataires ────────────────────────────────────────────────


class TestQuiRecoit:
    def test_la_permission_et_le_perimetre_se_cumulent(self):
        entrees = _journal(("C-003", "essai.fait", {"dossier": "N1"}, None))
        politique = _politique(_abonnement())
        recu = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-004", {"N1"}),
            permissions=["LIRE_DOSSIER"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        assert [n.titre for n in recu] == ["Fait sur N1"]
        hors_perimetre = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-005", {"N2"}),
            permissions=["LIRE_DOSSIER"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        sans_permission = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-004", {"N1"}),
            permissions=["GERER_COMPTES"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        assert hors_perimetre == [] and sans_permission == []

    def test_une_entree_sans_le_champ_du_dossier_n_est_recue_par_personne(self):
        entrees = _journal(("C-003", "essai.fait", {}, None))
        assert (
            notifications_pour(
                entrees,
                lecteur=_Lecteur("C-001"),
                permissions=["LIRE_DOSSIER"],
                politique=_politique(_abonnement()),
                lu_jusqu_au_rang=None,
            )
            == []
        )

    def test_on_ne_recoit_pas_l_avis_de_son_propre_geste(self):
        entrees = _journal(("C-003", "essai.fait", {"dossier": "N1"}, None))
        assert (
            notifications_pour(
                entrees,
                lecteur=_Lecteur("C-003"),
                permissions=["LIRE_DOSSIER"],
                politique=_politique(_abonnement()),
                lu_jusqu_au_rang=None,
            )
            == []
        )

    def test_un_compte_nomme_ne_notifie_que_lui(self):
        entrees = _journal(("C-002", "essai.fait", {"compte": "C-004"}, None))
        politique = _politique(_abonnement(destinataires=Destinataires(compte="compte")))
        for compte, attendu in (("C-004", 1), ("C-005", 0)):
            recu = notifications_pour(
                entrees,
                lecteur=_Lecteur(compte),
                permissions=[],
                politique=politique,
                lu_jusqu_au_rang=None,
            )
            assert len(recu) == attendu

    def test_le_premier_abonnement_qui_correspond_decide_pas_le_premier_qui_concerne(self):
        """Exclu du premier, on ne doit pas recevoir le second, écrit pour un autre cas."""
        entrees = _journal(("C-003", "essai.fait", {"dossier": "N1", "statut": "A"}, None))
        politique = _politique(
            _abonnement(
                si={"statut": "A"},
                titre="cas A",
                destinataires=Destinataires(permissions=("MODIFIER_REGLE",)),
            ),
            _abonnement(titre="cas général"),
        )
        recu = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-004"),
            permissions=["LIRE_DOSSIER"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        assert recu == []
        autre_statut = _journal(("C-003", "essai.fait", {"dossier": "N1", "statut": "B"}, None))
        recu = notifications_pour(
            autre_statut,
            lecteur=_Lecteur("C-004"),
            permissions=["LIRE_DOSSIER"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        assert [n.titre for n in recu] == ["cas général"]

    def test_le_motif_n_est_jamais_lisible_par_un_gabarit(self):
        entrees = _journal(("C-003", "essai.fait", {"dossier": "N1"}, "note interne du réviseur"))
        politique = _politique(_abonnement(texte="motif : {motif}"))
        [notification] = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-004"),
            permissions=["LIRE_DOSSIER"],
            politique=politique,
            lu_jusqu_au_rang=None,
        )
        assert notification.texte == "motif : ?"

    def test_lue_se_decide_au_rang_et_la_plus_recente_vient_d_abord(self):
        entrees = _journal(
            ("C-003", "essai.fait", {"dossier": "N1"}, None),
            ("C-003", "essai.fait", {"dossier": "N2"}, None),
        )
        recu = notifications_pour(
            entrees,
            lecteur=_Lecteur("C-004"),
            permissions=["LIRE_DOSSIER"],
            politique=_politique(_abonnement()),
            lu_jusqu_au_rang=1,
        )
        assert [(n.rang, n.lue) for n in recu] == [(2, False), (1, True)]


# ── Par l'API ─────────────────────────────────────────────────────────────────


def _ecarter_en_attente(reviseur: TestClient) -> None:
    reponse = reviseur.post(
        "/conformite/pieces/F-2026-0412/ecarts",
        json={"code_regle": "FAC-ACH-007", "motif": "Règlement par virement, relevé déposé."},
    )
    assert reponse.status_code == 200, reponse.text


class TestUnEcartEnAttenteSAnnonce:
    def test_a_qui_peut_trancher_et_seulement_a_lui(self):
        reviseur = _client(REVISEUR)
        _ecarter_en_attente(reviseur)

        fiscaliste = _client(FISCALISTE).get("/transverse/notifications").json()
        assert fiscaliste["non_lues"] == 1
        [avis] = fiscaliste["notifications"]
        assert avis["titre"] == "Écart à trancher sur F-2026-0412"
        assert avis["lien"] == "/pieces/F-2026-0412"
        assert "?" not in avis["titre"] + avis["texte"]
        assert "virement" not in avis["texte"], "le motif du réviseur a fui"

        assert reviseur.get("/transverse/notifications").json()["non_lues"] == 0
        assert _client(DIRECTION).get("/transverse/notifications").json()["non_lues"] == 0
        assert _client(COMPTABLE_SERVICES).get("/transverse/notifications").json()["non_lues"] == 0

    def test_un_avertissement_ecarte_sans_second_regard_n_appelle_pas_le_fiscaliste(self):
        reponse = _client(REVISEUR).post(
            "/conformite/pieces/F-2026-0415/ecarts",
            json={"code_regle": "FAC-DOC-011", "motif": "Mention présente au bon de commande."},
        )
        assert reponse.status_code == 200, reponse.text
        assert _client(FISCALISTE).get("/transverse/notifications").json()["notifications"] == []


class TestUnDossierConfieSAnnonceASonComptable:
    def test_le_comptable_apprend_qu_on_lui_confie_la_clinique(self):
        niu = NIU_DEMO["CLINIQUE"]
        direction = _client(DIRECTION)
        assert direction.post(f"/transverse/habilitations/H-004/dossiers/{niu}").status_code == 200
        [avis] = (
            _client(COMPTABLE_INDUSTRIE).get("/transverse/notifications").json()["notifications"]
        )
        assert avis["titre"] == f"Le dossier {niu} vous est confié"
        assert avis["lien"] == f"/portefeuille/{niu}"
        assert (
            _client(COMPTABLE_SERVICES).get("/transverse/notifications").json()["notifications"]
            == []
        )


class TestLaPositionDeLecture:
    def test_elle_avance_ne_recule_jamais_et_s_arrete_a_la_tete(self):
        _ecarter_en_attente(_client(REVISEUR))
        fiscaliste = _client(FISCALISTE)
        [avis] = fiscaliste.get("/transverse/notifications").json()["notifications"]

        lue = fiscaliste.post(
            "/transverse/notifications/lecture", json={"jusqu_au_rang": avis["rang"]}
        )
        assert lue.json()["lu_jusqu_au_rang"] == avis["rang"]
        apres = fiscaliste.get("/transverse/notifications").json()
        assert apres["non_lues"] == 0 and apres["notifications"][0]["lue"] is True

        recul = fiscaliste.post("/transverse/notifications/lecture", json={"jusqu_au_rang": 1})
        assert recul.json()["lu_jusqu_au_rang"] == avis["rang"]

        au_dela = fiscaliste.post(
            "/transverse/notifications/lecture", json={"jusqu_au_rang": 10**9}
        )
        assert au_dela.json()["lu_jusqu_au_rang"] < 10**9

        # Un écart proposé ensuite reste non lu : la position s'est arrêtée à la tête.
        vider_les_ecarts()
        _ecarter_en_attente(_client(REVISEUR))
        assert fiscaliste.get("/transverse/notifications").json()["non_lues"] == 1

    def test_sans_session_rien(self):
        assert TestClient(creer_application()).get("/transverse/notifications").status_code == 401


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_la_position_de_lecture_se_conserve_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        _ecarter_en_attente(client)
        ouvrir_une_session(client, FISCALISTE)
        [avis] = client.get("/transverse/notifications").json()["notifications"]
        client.post("/transverse/notifications/lecture", json={"jusqu_au_rang": avis["rang"]})
        assert client.get("/transverse/notifications").json()["non_lues"] == 0
        assert re.match(r"^Écart à trancher", avis["titre"])
