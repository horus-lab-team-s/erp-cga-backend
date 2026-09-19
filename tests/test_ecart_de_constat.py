"""Écarter un constat : motif, second regard, politique du cabinet (pas 92).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

Le geste était sur l'écran E02 depuis le premier jour, sous la forme d'un bouton qui ne
faisait rien. Il ne pouvait pas se brancher simplement, parce qu'écarter un constat est
**le seul contournement légitime** d'un contrôle de conformité. Ces cas vérifient les
garde-fous qui le rendent défendable :

- la politique vient du référentiel, et son absence **ferme** tout ;
- un MAJEUR attend un second regard, qui ne vient pas de l'auteur ;
- un écart ne vaut que pour le constat examiné : si l'enjeu change, il est caduc ;
- durcir la politique suspend les écarts qui ne la respectent plus ;
- la proposition d'écriture lit le rapport arbitré, et non le rapport brut ;
- l'adhérent voit qu'un constat est écarté, jamais le motif du réviseur.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.conformite.adaptateurs.sortant.depots_ecarts import DepotEcartsMemoire
from app.contextes.conformite.adaptateurs.sortant.politique_ecarts_yaml import (
    charger_la_politique_d_ecart,
)
from app.contextes.conformite.api import (
    FACTURES_DEMO,
    EcartRefuse,
    MotifInsuffisant,
    PolitiqueDEcart,
    Severite,
    StatutEcart,
    appliquer_les_ecarts,
    etat_des_ecarts,
    moteur_par_defaut,
    proposer_un_ecart,
    vider_les_ecarts,
)
from app.contextes.conformite.domaine.ecarts import RegleDEcart
from app.contextes.transverse.api import (
    MOT_DE_PASSE_DEMO,
    Permission,
    atelier,
    reinitialiser_atelier,
)
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.locataire import etabli
from tests.conftest import exige_postgresql, ouvrir_une_session

#: MAJEUR (FAC-ACH-007), dossier SARL BATIMENT PLUS.
MAJEUR_BATIMENT = "F-2026-0412"
#: AVERTISSEMENT seul (FAC-DOC-011), dossier AGRO.
AVERTISSEMENT_AGRO = "F-2026-0415"
#: BLOQUANT (FAC-ID-003) et AVERTISSEMENT, dossier LA COLOMBE.
BLOQUANT_COLOMBE = "F-2026-0414"

REVISEUR = "a.bouba@cga-brcg.cm"
FISCALISTE = "r.ebolo@cga-brcg.cm"
COMPTABLE_BATIMENT = "l.fotso@cga-brcg.cm"
ADHERENT_BATIMENT = "jp.nkoa@batimentplus.cm"

MOTIF = "Règlement par virement, attesté par le relevé bancaire déposé le 02/07."
LE = datetime(2026, 9, 15, 10, 0)


@pytest.fixture(autouse=True)
def ecarts_neufs():
    reinitialiser_atelier()
    vider_les_ecarts()
    yield
    vider_les_ecarts()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _rapport(reference: str):
    return moteur_par_defaut().controler(FACTURES_DEMO[reference])


def _ecarter(client: TestClient, reference: str, code: str, motif: str = MOTIF):
    return client.post(
        f"/conformite/pieces/{reference}/ecarts", json={"code_regle": code, "motif": motif}
    )


POLITIQUE_OUVERTE = PolitiqueDEcart(
    par_severite={
        Severite.MAJEUR: RegleDEcart(ecartable=True, second_regard=False),
        Severite.AVERTISSEMENT: RegleDEcart(ecartable=True, second_regard=False),
    },
    source="test",
)


# ── La politique ──────────────────────────────────────────────────────────────


class TestLaPolitiqueVientDuReferentiel:
    @pytest.fixture(autouse=True)
    def locataire(self):
        with etabli(configuration().locataire_par_defaut):
            yield

    def test_sans_fichier_rien_n_est_ecartable(self, tmp_path: Path):
        """Le sens prudent : une installation incomplète perd une indulgence, rien d'autre."""
        politique = charger_la_politique_d_ecart(tmp_path)
        assert politique == PolitiqueDEcart.prudente()
        with pytest.raises(EcartRefuse, match="ne permet pas"):
            proposer_un_ecart(
                _rapport(AVERTISSEMENT_AGRO),
                dossier="M065544332211L",
                code_regle="FAC-DOC-011",
                motif=MOTIF,
                par="C-003",
                le=LE,
                politique=politique,
                depot=DepotEcartsMemoire(),
            )

    def test_une_cle_mal_orthographiee_fait_echouer_le_chargement(self, tmp_path: Path):
        """Ignorée, elle laisserait croire au cabinet que son réglage est en vigueur."""
        (tmp_path / "ecarts").mkdir()
        (tmp_path / "ecarts" / "politique.yaml").write_text(
            yaml.safe_dump(
                {"par_severite": {"MAJEUR": {"ecartable": True, "second_regart": False}}}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            charger_la_politique_d_ecart(tmp_path)

    def test_une_severite_non_nommee_reste_fermee_et_exigerait_un_second_regard(self):
        assert RegleDEcart() == RegleDEcart(ecartable=False, second_regard=True)

    def test_la_politique_livree_nomme_des_permissions_qui_existent(self):
        """Une coquille fermerait le second regard, en silence, à qui le cabinet désignait."""
        politique = charger_la_politique_d_ecart(configuration().dossier_referentiel)
        assert politique.source == "ecarts/politique.yaml"
        inconnues = [
            p for p in politique.permissions_du_second_regard if p not in Permission.__members__
        ]
        assert not inconnues
        assert politique.par_severite[Severite.BLOQUANT].ecartable is False


# ── Le domaine ────────────────────────────────────────────────────────────────


class TestUnEcartVautPourLeConstatExamine:
    @pytest.fixture(autouse=True)
    def locataire(self):
        """Le dépôt en mémoire range par locataire : hors requête, il faut l'établir."""
        with etabli(configuration().locataire_par_defaut):
            yield

    def test_si_l_enjeu_change_l_ecart_est_caduc_et_le_constat_revient(self):
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        ecarts = depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT)
        assert appliquer_les_ecarts(rapport, ecarts, POLITIQUE_OUVERTE).constats == []

        [constat] = rapport.constats
        change = rapport.model_copy(
            update={"constats": [constat.model_copy(update={"enjeu": constat.enjeu + Decimal(1)})]}
        )
        arbitre = appliquer_les_ecarts(change, ecarts, POLITIQUE_OUVERTE)
        assert [c.code_regle for c in arbitre.constats] == ["FAC-ACH-007"]
        [etat] = etat_des_ecarts(change, ecarts, POLITIQUE_OUVERTE)
        assert etat.applique is False and etat.raison.startswith("caduc")

    def test_un_arrondi_different_du_meme_montant_ne_rend_pas_caduc(self):
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        [constat] = rapport.constats
        meme = rapport.model_copy(
            update={
                "constats": [
                    constat.model_copy(update={"enjeu": constat.enjeu.quantize(Decimal("0.01"))})
                ]
            }
        )
        ecarts = depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT)
        assert appliquer_les_ecarts(meme, ecarts, POLITIQUE_OUVERTE).constats == []

    def test_durcir_la_politique_suspend_les_ecarts_qui_ne_la_respectent_plus(self):
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        durcie = POLITIQUE_OUVERTE.model_copy(
            update={
                "par_severite": {Severite.MAJEUR: RegleDEcart(ecartable=True, second_regard=True)}
            }
        )
        ecarts = depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT)
        assert [c.code_regle for c in appliquer_les_ecarts(rapport, ecarts, durcie).constats] == [
            "FAC-ACH-007"
        ]
        [etat] = etat_des_ecarts(rapport, ecarts, durcie)
        assert "second regard" in etat.raison
        assert etat.ecart.statut is StatutEcart.EFFECTIF

    def test_fermer_une_severite_suspend_les_ecarts_deja_effectifs(self):
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        fermee = POLITIQUE_OUVERTE.model_copy(update={"par_severite": {}})
        ecarts = depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT)
        assert len(appliquer_les_ecarts(rapport, ecarts, fermee).constats) == 1
        [etat] = etat_des_ecarts(rapport, ecarts, fermee)
        assert "ne permet plus" in etat.raison

    def test_assouplir_la_politique_ne_confirme_pas_ce_qui_attend(self):
        """Un écart proposé sous second regard n'a été vu que par son auteur. Retirer
        l'exigence ensuite ne remplace pas le regard qui n'a pas eu lieu."""
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        exigeante = POLITIQUE_OUVERTE.model_copy(
            update={"par_severite": {Severite.MAJEUR: RegleDEcart(ecartable=True)}}
        )
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=exigeante,
            depot=depot,
        )
        ecarts = depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT)
        assert len(appliquer_les_ecarts(rapport, ecarts, POLITIQUE_OUVERTE).constats) == 1

    def test_un_cabinet_ne_voit_pas_les_ecarts_d_un_autre(self):
        """Même référence de facture, même NIU, deux cabinets : le dépôt mémoire est
        partagé par le processus, et c'est le locataire qui sépare."""
        depot = DepotEcartsMemoire()
        proposer_un_ecart(
            _rapport(MAJEUR_BATIMENT),
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        with etabli("un-autre-cabinet"):
            assert depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT) == []
            assert depot.en_attente() == []

    def test_un_motif_d_espaces_n_est_pas_un_motif(self):
        with pytest.raises(MotifInsuffisant):
            proposer_un_ecart(
                _rapport(MAJEUR_BATIMENT),
                dossier="M081234567890P",
                code_regle="FAC-ACH-007",
                motif="   ok" + " " * 40,
                par="C-003",
                le=LE,
                politique=POLITIQUE_OUVERTE,
                depot=DepotEcartsMemoire(),
            )

    def test_le_rapport_d_origine_n_est_pas_modifie(self):
        depot = DepotEcartsMemoire()
        rapport = _rapport(MAJEUR_BATIMENT)
        proposer_un_ecart(
            rapport,
            dossier="M081234567890P",
            code_regle="FAC-ACH-007",
            motif=MOTIF,
            par="C-003",
            le=LE,
            politique=POLITIQUE_OUVERTE,
            depot=depot,
        )
        appliquer_les_ecarts(
            rapport, depot.pour_la_piece("M081234567890P", MAJEUR_BATIMENT), POLITIQUE_OUVERTE
        )
        assert len(rapport.constats) == 1 and rapport.constats_ecartes == []


# ── Par l'API ─────────────────────────────────────────────────────────────────


class TestUnAvertissementSEcarteSansSecondRegard:
    def test_il_sort_du_rapport_et_le_journal_nomme_l_auteur(self):
        reviseur = _client(REVISEUR)
        reponse = _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011")
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["ecart"]["statut"] == "EFFECTIF"
        assert reponse.json()["applique"] is True

        piece = reviseur.get(f"/conformite/demonstration/{AVERTISSEMENT_AGRO}").json()
        assert piece["rapport"]["constats"] == []
        assert [c["constat"]["code_regle"] for c in piece["rapport"]["constats_ecartes"]] == [
            "FAC-DOC-011"
        ]
        assert piece["ecarts"][0]["applique"] is True

        [entree] = [e for e in atelier().journal.lister() if e.action == "conformite.ecart_propose"]
        assert entree.acteur == "C-003"
        assert entree.motif == MOTIF

    def test_la_boite_de_reception_rend_le_meme_verdict_que_le_detail(self):
        reviseur = _client(REVISEUR)
        _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011")
        [ligne] = [
            r
            for r in reviseur.get("/conformite/demonstration/rapports").json()
            if r["facture"]["document"]["reference"] == AVERTISSEMENT_AGRO
        ]
        assert ligne["rapport"]["constats"] == []

    def test_deux_fois_le_meme_ecart_est_refuse(self):
        reviseur = _client(REVISEUR)
        assert _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011").status_code == 200
        assert _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011").status_code == 409

    def test_lever_fait_revenir_le_constat_et_ne_se_repete_pas(self):
        reviseur = _client(REVISEUR)
        identifiant = _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011").json()["ecart"][
            "identifiant"
        ]
        adresse = f"/conformite/pieces/{AVERTISSEMENT_AGRO}/ecarts/{identifiant}/levee"
        levee = reviseur.post(
            adresse, json={"motif": "Pièce justificative finalement non probante."}
        )
        assert levee.status_code == 200, levee.text
        assert levee.json()["ecart"]["statut"] == "LEVE"
        piece = reviseur.get(f"/conformite/demonstration/{AVERTISSEMENT_AGRO}").json()
        assert [c["code_regle"] for c in piece["rapport"]["constats"]] == ["FAC-DOC-011"]
        assert (
            reviseur.post(
                adresse, json={"motif": "Pièce justificative finalement non probante."}
            ).status_code
            == 409
        )
        # Une nouvelle proposition prend le rang suivant : la levée reste lisible.
        suivant = _ecarter(reviseur, AVERTISSEMENT_AGRO, "FAC-DOC-011").json()["ecart"][
            "identifiant"
        ]
        assert suivant.endswith(":2")


class TestUnMajeurAttendLeSecondRegard:
    def test_il_compte_tant_que_personne_d_autre_n_a_confirme(self):
        reviseur = _client(REVISEUR)
        reponse = _ecarter(reviseur, MAJEUR_BATIMENT, "FAC-ACH-007")
        assert reponse.status_code == 200, reponse.text
        ecart = reponse.json()["ecart"]
        assert ecart["statut"] == "EN_ATTENTE"
        assert reponse.json()["applique"] is False
        assert reponse.json()["raison"].startswith("en attente du second regard")

        piece = reviseur.get(f"/conformite/demonstration/{MAJEUR_BATIMENT}").json()
        assert [c["code_regle"] for c in piece["rapport"]["constats"]] == ["FAC-ACH-007"]

        adresse = (
            f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{ecart['identifiant']}/second-regard"
        )
        decision = {"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."}
        soi_meme = reviseur.post(adresse, json=decision)
        assert soi_meme.status_code == 409 and "autre personne" in soi_meme.json()["detail"]

        fiscaliste = _client(FISCALISTE)
        assert [
            e["identifiant"] for e in fiscaliste.get("/conformite/ecarts/en-attente").json()
        ] == [ecart["identifiant"]]
        confirme = fiscaliste.post(adresse, json=decision)
        assert confirme.status_code == 200, confirme.text
        assert confirme.json()["ecart"]["statut"] == "EFFECTIF"
        assert confirme.json()["ecart"]["tranche_par"] == "C-006"
        assert fiscaliste.get("/conformite/ecarts/en-attente").json() == []

        piece = reviseur.get(f"/conformite/demonstration/{MAJEUR_BATIMENT}").json()
        assert piece["rapport"]["constats"] == []
        # « Conforme — aucun constat » serait faux : le moteur a réagi, le cabinet a écarté.
        assert piece["verdict"]["titre"] == "Conforme après écart — 1 constat écarté"
        assert [
            e.action for e in atelier().journal.lister() if e.action.startswith("conformite.")
        ] == [
            "conformite.ecart_propose",
            "conformite.ecart_confirme",
        ]

    def test_la_proposition_d_ecriture_lit_le_rapport_arbitre(self):
        """Sans cela, l'écran dirait « écarté » et l'écriture rejetterait quand même."""
        comptable = _client(COMPTABLE_BATIMENT)
        corps = {"facture": FACTURES_DEMO[MAJEUR_BATIMENT].model_dump(mode="json"), "journal": "AC"}
        adresse = "/comptabilite/dossiers/M081234567890P/propositions"
        avant = comptable.post(adresse, json=corps)
        assert avant.status_code == 200, avant.text
        assert [c["code_regle"] for c in avant.json()["rapport"]["constats"]] == ["FAC-ACH-007"]
        assert any(ligne.get("attribut_fiscal") for ligne in avant.json()["saisie"]["lignes"])

        reviseur = _client(REVISEUR)
        identifiant = _ecarter(reviseur, MAJEUR_BATIMENT, "FAC-ACH-007").json()["ecart"][
            "identifiant"
        ]
        _client(FISCALISTE).post(
            f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{identifiant}/second-regard",
            json={"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."},
        )

        apres = comptable.post(adresse, json=corps)
        assert apres.json()["rapport"]["constats"] == []
        assert apres.json()["comptabilisable"] is True
        lignes = apres.json()["saisie"]["lignes"]
        assert not any(ligne.get("attribut_fiscal") for ligne in lignes)

    def test_un_refus_laisse_le_constat_et_se_conserve(self):
        reviseur = _client(REVISEUR)
        identifiant = _ecarter(reviseur, MAJEUR_BATIMENT, "FAC-ACH-007").json()["ecart"][
            "identifiant"
        ]
        refus = _client(FISCALISTE).post(
            f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{identifiant}/second-regard",
            json={"decision": "REFUSER", "motif": "Le relevé ne mentionne pas ce fournisseur."},
        )
        assert refus.json()["ecart"]["statut"] == "REFUSE"
        piece = reviseur.get(f"/conformite/demonstration/{MAJEUR_BATIMENT}").json()
        assert [c["code_regle"] for c in piece["rapport"]["constats"]] == ["FAC-ACH-007"]
        assert piece["ecarts"][0]["ecart"]["statut"] == "REFUSE"


class TestCeQuiEstRefuse:
    def test_un_bloquant_ne_s_ecarte_pas(self):
        reponse = _ecarter(_client(REVISEUR), BLOQUANT_COLOMBE, "FAC-ID-003")
        assert reponse.status_code == 409
        assert "rectificative" in reponse.json()["detail"]

    def test_un_constat_que_le_rapport_ne_porte_pas(self):
        assert _ecarter(_client(REVISEUR), AVERTISSEMENT_AGRO, "FAC-ACH-007").status_code == 409

    def test_un_motif_trop_court_pour_la_politique_est_une_demande_incomplete(self):
        reponse = _ecarter(
            _client(REVISEUR), AVERTISSEMENT_AGRO, "FAC-DOC-011", motif="Vu au dossier."
        )
        assert reponse.status_code == 422
        assert "20 caractères" in reponse.json()["detail"]

    def test_le_comptable_et_l_adherent_n_ecartent_pas(self):
        for courriel in (COMPTABLE_BATIMENT, ADHERENT_BATIMENT):
            assert _ecarter(_client(courriel), MAJEUR_BATIMENT, "FAC-ACH-007").status_code == 403

    def test_le_comptable_ne_donne_pas_le_second_regard_et_ne_voit_pas_la_file(self):
        reviseur = _client(REVISEUR)
        identifiant = _ecarter(reviseur, MAJEUR_BATIMENT, "FAC-ACH-007").json()["ecart"][
            "identifiant"
        ]
        comptable = _client(COMPTABLE_BATIMENT)
        assert comptable.get("/conformite/ecarts/en-attente").status_code == 403
        assert (
            comptable.post(
                f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{identifiant}/second-regard",
                json={
                    "decision": "CONFIRMER",
                    "motif": "Relevé bancaire relu : virement du 30/06.",
                },
            ).status_code
            == 403
        )

    def test_une_piece_inconnue_et_un_ecart_inconnu_rendent_404(self):
        reviseur = _client(REVISEUR)
        assert _ecarter(reviseur, "F-2099-0001", "FAC-ACH-007").status_code == 404
        assert (
            reviseur.post(
                f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{MAJEUR_BATIMENT}:FAC-ACH-007:9/levee",
                json={"motif": "Pièce justificative finalement non probante."},
            ).status_code
            == 404
        )

    def test_la_politique_est_reservee_au_cabinet(self):
        assert _client(ADHERENT_BATIMENT).get("/conformite/ecarts/politique").status_code == 403
        politique = _client(COMPTABLE_BATIMENT).get("/conformite/ecarts/politique")
        assert politique.status_code == 200
        assert politique.json()["par_severite"]["BLOQUANT"]["ecartable"] is False


class TestLAdherentVoitLEcartPasLeMotif:
    def test_le_constat_ecarte_est_visible_la_note_du_reviseur_non(self):
        reviseur = _client(REVISEUR)
        identifiant = _ecarter(reviseur, MAJEUR_BATIMENT, "FAC-ACH-007").json()["ecart"][
            "identifiant"
        ]
        _client(FISCALISTE).post(
            f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{identifiant}/second-regard",
            json={"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."},
        )
        piece = _client(ADHERENT_BATIMENT).get(f"/conformite/demonstration/{MAJEUR_BATIMENT}")
        assert piece.status_code == 200, piece.text
        assert piece.json()["ecarts"] == []
        assert [c["identifiant_ecart"] for c in piece.json()["rapport"]["constats_ecartes"]] == [
            identifiant
        ]
        assert MOTIF not in piece.text and "Relevé bancaire relu" not in piece.text


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_proposer_confirmer_puis_relire_d_une_requete_a_l_autre(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, REVISEUR)
        reponse = _ecarter(client, MAJEUR_BATIMENT, "FAC-ACH-007")
        assert reponse.status_code == 200, reponse.text
        identifiant = reponse.json()["ecart"]["identifiant"]

        ouvrir_une_session(client, FISCALISTE)
        assert [e["identifiant"] for e in client.get("/conformite/ecarts/en-attente").json()] == [
            identifiant
        ]
        confirme = client.post(
            f"/conformite/pieces/{MAJEUR_BATIMENT}/ecarts/{identifiant}/second-regard",
            json={"decision": "CONFIRMER", "motif": "Relevé bancaire relu : virement du 30/06."},
        )
        assert confirme.status_code == 200, confirme.text

        piece = client.get(f"/conformite/demonstration/{MAJEUR_BATIMENT}").json()
        assert piece["rapport"]["constats"] == []
        assert piece["ecarts"][0]["ecart"]["statut"] == "EFFECTIF"
