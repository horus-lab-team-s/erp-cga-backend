"""La pièce d'appui d'une dérogation (pas 118).

Maquettes « Parcours réviseur », vue B (« Pièce d'appui : 4 attestations jointes ») et « Pilotage
direction », vue E (« 3 dérogations sans pièce d'appui : à régulariser avant la revue trimestrielle »).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **La transition** : joindre, remplacer, refuser sur un écart levé ou refusé, refuser une
   référence vide.
2. **La politique** : quelle sévérité exige une preuve, et à partir de quand un écart sans preuve
   est « à régulariser » (le jour même ne l'est pas).
3. **Les routes** : la pièce donnée à la proposition, jointe après coup, journalisée à son auteur,
   et le journal des dérogations qui compte ce qui reste à régulariser.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.contextes.conformite.api import politique_d_ecart, vider_les_ecarts
from app.contextes.conformite.domaine.ecarts import (
    EcartDeConstat,
    EcartRefuse,
    PolitiqueDEcart,
    RegleDEcart,
    StatutEcart,
    a_regulariser,
    piece_appui_exigee_pour,
)
from app.contextes.conformite.domaine.entites import Severite
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, atelier, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

LE_9_AOUT = datetime(2026, 8, 9, 10, 0)


def _ecart(**champs) -> EcartDeConstat:
    valeurs = {
        "identifiant": "F-2026-0412:FAC-ACH-007:1",
        "dossier": "M081234567890P",
        "reference_document": "F-2026-0412",
        "code_regle": "FAC-ACH-007",
        "severite": Severite.MAJEUR,
        "empreinte": "e" * 24,
        "motif": "Attestation obtenue du fournisseur et vérifiée au fichier DGI.",
        "propose_par": "C-003",
        "propose_le": LE_9_AOUT,
        "second_regard_requis": True,
        "statut": StatutEcart.EN_ATTENTE,
    }
    return EcartDeConstat(**{**valeurs, **champs})


class TestLaTransition:
    def test_joindre_puis_remplacer(self):
        jointe = _ecart().joindre_une_piece_d_appui("PJ-2026-0042", par="C-003", le=LE_9_AOUT)
        assert jointe.piece_appui == "PJ-2026-0042"
        assert jointe.piece_appui_par == "C-003" and jointe.piece_appui_le == LE_9_AOUT
        remplacee = jointe.joindre_une_piece_d_appui(
            "  Attestation DGI du 09/08  ", par="C-006", le=datetime(2026, 8, 10, 9, 0)
        )
        assert remplacee.piece_appui == "Attestation DGI du 09/08" and remplacee.piece_appui_par == "C-006"

    def test_refusee_sur_un_ecart_sans_effet_ou_sans_reference(self):
        for statut in (StatutEcart.LEVE, StatutEcart.REFUSE):
            with pytest.raises(EcartRefuse, match="n'a plus d'effet"):
                _ecart(statut=statut).joindre_une_piece_d_appui("PJ-1", par="C-003", le=LE_9_AOUT)
        with pytest.raises(EcartRefuse, match="se désigne"):
            _ecart().joindre_une_piece_d_appui("  ", par="C-003", le=LE_9_AOUT)

    def test_un_ecart_effectif_accepte_encore_sa_preuve(self):
        effectif = _ecart(statut=StatutEcart.EFFECTIF)
        assert effectif.joindre_une_piece_d_appui("PJ-9", par="C-003", le=LE_9_AOUT).piece_appui == "PJ-9"


class TestLaPolitique:
    def test_ce_qui_exige_une_preuve(self):
        politique = PolitiqueDEcart(
            par_severite={
                Severite.MAJEUR: RegleDEcart(ecartable=True, piece_appui_exigee=True),
                Severite.AVERTISSEMENT: RegleDEcart(ecartable=True, piece_appui_exigee=False),
            }
        )
        assert piece_appui_exigee_pour(_ecart(), politique) is True
        assert piece_appui_exigee_pour(_ecart(severite=Severite.AVERTISSEMENT), politique) is False
        # Une sévérité que la politique n'a pas nommée exige une preuve : le défaut est prudent.
        assert piece_appui_exigee_pour(_ecart(severite=Severite.BLOQUANT), politique) is True
        # Un écart sans effet n'exige plus rien.
        assert piece_appui_exigee_pour(_ecart(statut=StatutEcart.LEVE), politique) is False

    def test_a_regulariser_seulement_apres_le_delai(self):
        politique = PolitiqueDEcart(
            par_severite={Severite.MAJEUR: RegleDEcart(ecartable=True)},
            delai_de_regularisation_jours=30,
        )
        sans = _ecart()
        assert a_regulariser(sans, politique, date(2026, 8, 9)) is False
        assert a_regulariser(sans, politique, date(2026, 9, 8)) is False
        assert a_regulariser(sans, politique, date(2026, 9, 9)) is True
        avec = sans.joindre_une_piece_d_appui("PJ-1", par="C-003", le=LE_9_AOUT)
        assert a_regulariser(avec, politique, date(2027, 1, 1)) is False

    def test_le_fichier_livre_exige_la_preuve_de_ce_qui_coute(self):
        politique = politique_d_ecart()
        assert politique.source == "ecarts/politique.yaml"
        assert politique.piece_appui_exigee(Severite.MAJEUR) is True
        assert politique.piece_appui_exigee(Severite.AVERTISSEMENT) is False
        assert politique.delai_de_regularisation_jours == 30


# ── Les routes ────────────────────────────────────────────────────────────────

REVISEUR = "a.bouba@cga-brcg.cm"
FISCALISTE = "r.ebolo@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
MOTIF = "Attestation d'immatriculation obtenue du fournisseur et vérifiée au fichier DGI."


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    # ⚠️ Le magasin des écarts vit hors de l'atelier : sans cela, l'écart d'un cas se retrouve
    # dans le suivant, et le second essai reçoit « déjà écarté ».
    vider_les_ecarts()
    with horloge_figee(LE_9_AOUT):
        yield
    vider_les_ecarts()


def _client(courriel=REVISEUR):
    client = TestClient(creer_application())
    reponse = client.post("/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO})
    assert reponse.status_code == 200, reponse.text
    return client


def _un_constat_majeur(client):
    """Une pièce du périmètre et son constat majeur, écartable selon la politique livrée."""
    file = client.get("/pilotage/file-d-anomalies", params={"gravite": "MAJEUR"}).json()
    ligne = next(l_ for l_ in file["lignes"] if l_["enjeu"])
    return ligne["piece"], ligne["code_regle"]


class TestLesRoutes:
    def test_la_piece_donnee_a_la_proposition(self):
        client = _client()
        piece, regle = _un_constat_majeur(client)
        pose = client.post(
            f"/conformite/pieces/{piece}/ecarts",
            json={"code_regle": regle, "motif": MOTIF, "piece_appui": "PJ-2026-0042"},
        )
        assert pose.status_code in (200, 201), pose.text
        ecart = pose.json()["ecart"]
        assert ecart["piece_appui"] == "PJ-2026-0042" and ecart["piece_appui_par"] == "C-003"
        entrees = [
            e.action
            for e in atelier().journal.lister(objet_type="ecart_de_constat", objet_id=ecart["identifiant"])
        ]
        assert entrees == ["conformite.ecart_propose", "conformite.piece_d_appui_jointe"]

    def test_la_piece_jointe_apres_coup_et_ses_refus(self):
        client = _client()
        piece, regle = _un_constat_majeur(client)
        ecart = client.post(
            f"/conformite/pieces/{piece}/ecarts", json={"code_regle": regle, "motif": MOTIF}
        ).json()["ecart"]
        assert ecart["piece_appui"] is None

        chemin = f"/conformite/pieces/{piece}/ecarts/{ecart['identifiant']}/piece-appui"
        courte = client.post(chemin, json={"piece": "ok"})
        assert courte.status_code == 422, courte.text
        jointe = client.post(chemin, json={"piece": "Attestation DGI du 09/08/2026"})
        assert jointe.status_code in (200, 201), jointe.text
        assert jointe.json()["ecart"]["piece_appui"] == "Attestation DGI du 09/08/2026"

        inconnu = client.post(
            f"/conformite/pieces/{piece}/ecarts/{ecart['identifiant']}-x/piece-appui",
            json={"piece": "PJ-2026-0042"},
        )
        assert inconnu.status_code == 404
        # Le comptable contrôle mais n'écarte pas : il ne joint pas non plus de preuve.
        assert _client(COMPTABLE).post(chemin, json={"piece": "PJ-2026-0042"}).status_code == 403

    def test_le_journal_compte_ce_qui_reste_a_regulariser(self):
        client = _client()
        piece, regle = _un_constat_majeur(client)
        client.post(f"/conformite/pieces/{piece}/ecarts", json={"code_regle": regle, "motif": MOTIF})

        journal = client.get("/conformite/derogations").json()
        assert journal["delai_de_regularisation_jours"] == 30
        assert journal["a_regulariser"] == [], "le jour même n'est pas un retard"

        with horloge_figee(datetime(2026, 9, 20, 9, 0)):
            tardif = _client().get("/conformite/derogations").json()
        (retard,) = [r for r in tardif["a_regulariser"] if r["reference_document"] == piece]
        assert retard["code_regle"] == regle and retard["jours"] == 42
        assert retard["propose_par"] == "Aïcha BOUBA"

        with horloge_figee(datetime(2026, 9, 20, 9, 30)):
            apres = _client()
            identifiant = retard["identifiant"]
            jointe = apres.post(
                f"/conformite/pieces/{piece}/ecarts/{identifiant}/piece-appui",
                json={"piece": "Attestation DGI du 20/09/2026"},
            )
            assert jointe.status_code in (200, 201), jointe.text
            assert apres.get("/conformite/derogations").json()["a_regulariser"] == []

    def test_un_avertissement_n_exige_pas_de_preuve(self):
        client = _client()
        file = client.get("/pilotage/file-d-anomalies", params={"gravite": "AVERTISSEMENT"}).json()
        ligne = file["lignes"][0]
        client.post(
            f"/conformite/pieces/{ligne['piece']}/ecarts",
            json={"code_regle": ligne["code_regle"], "motif": MOTIF},
        )
        with horloge_figee(datetime(2027, 1, 1, 9, 0)):
            journal = _client().get("/conformite/derogations").json()
        assert not any(r["reference_document"] == ligne["piece"] for r in journal["a_regulariser"])
