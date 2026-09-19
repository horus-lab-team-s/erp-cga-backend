"""La file d'anomalies du réviseur (pas 117).

Maquette « Parcours réviseur CGA », vue A : tout le portefeuille, par gravité et par enjeu.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

1. **L'ordre de la file** : gravité, ce qui dort, enjeu décroissant, ancienneté ; un constat qui
   dort passe devant **dans sa gravité** et jamais au-delà ; un constat sans enjeu passe après ceux
   qui en ont un.
2. **Les compteurs et les groupes** : comptés avant filtre, la règle la plus fournie d'abord, la
   gravité la plus forte du groupe, les dossiers concernés.
3. **Les réglages** qui mentiraient : une gravité inconnue, une file sans gravité, une file sans
   les bloquants.
4. **La route** : le périmètre, les filtres, l'ancienneté au jour, le constat écarté qui sort de la
   file, et le rôle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.pilotage.adaptateurs.sortant.file_d_anomalies_yaml import (
    charger_les_reglages_de_la_file,
)
from app.contextes.pilotage.domaine.file_d_anomalies import (
    LigneDAnomalie,
    ReglagesDeLaFile,
    compter_par_gravite,
    grouper_par_regle,
    ordonner_la_file,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, reinitialiser_atelier
from app.main import creer_application
from app.partage.horloge import horloge_figee

REFERENTIEL = Path(__file__).resolve().parents[2] / "Docs" / "referentiel"


def _ligne(piece, gravite, enjeu, anciennete, *, dort=False, regle="R1", dossier="N1"):
    return LigneDAnomalie(
        piece=piece,
        dossier=dossier,
        denomination=f"Dossier {dossier}",
        date_piece=date(2026, 7, 1),
        fournisseur="FOURNISSEUR",
        code_regle=regle,
        libelle_regle=f"Libellé {regle}",
        gravite=gravite,
        message="message",
        enjeu=None if enjeu is None else Decimal(enjeu),
        anciennete=anciennete,
        dort=dort,
        depose_par=None,
    )


class TestLOrdreDeLaFile:
    def test_gravite_puis_enjeu_puis_anciennete(self):
        lignes = [
            _ligne("P1", "MAJEUR", 900, 2),
            _ligne("P2", "BLOQUANT", 100, 1),
            _ligne("P3", "BLOQUANT", 500, 30),
            _ligne("P4", "AVERTISSEMENT", 10_000, 1),
        ]
        assert [l_.piece for l_ in ordonner_la_file(lignes)] == ["P3", "P2", "P1", "P4"]

    def test_un_constat_sans_enjeu_passe_apres(self):
        lignes = [_ligne("SANS", "MAJEUR", None, 40), _ligne("AVEC", "MAJEUR", 1, 1)]
        assert [l_.piece for l_ in ordonner_la_file(lignes)] == ["AVEC", "SANS"]

    def test_celui_qui_dort_passe_devant_dans_sa_gravite_et_pas_au_dela(self):
        qui_dort = _ligne("VIEUX", "MAJEUR", 10, 60, dort=True)
        lignes = [_ligne("NEUF", "BLOQUANT", 5, 1), qui_dort, _ligne("RICHE", "MAJEUR", 900, 1)]
        ordre = ordonner_la_file(lignes)
        # Le bloquant du jour reste en tête ; le majeur endormi passe devant le majeur plus coûteux.
        assert [l_.piece for l_ in ordre] == ["NEUF", "VIEUX", "RICHE"]
        assert ordre[1].gravite == "MAJEUR", "la gravité affichée ne se surclasse pas"
        endormi = _ligne("OUBLI", "AVERTISSEMENT", None, 90, dort=True)
        assert [l_.piece for l_ in ordonner_la_file([endormi, _ligne("BLOC", "BLOQUANT", None, 1)])] == [
            "BLOC",
            "OUBLI",
        ]


class TestLesComptesEtLesGroupes:
    def test_les_compteurs_par_gravite(self):
        lignes = [_ligne("A", "BLOQUANT", 1, 1), _ligne("B", "BLOQUANT", 1, 1), _ligne("C", "MAJEUR", 1, 1)]
        assert compter_par_gravite(lignes) == {"BLOQUANT": 2, "MAJEUR": 1}

    def test_les_groupes_la_regle_la_plus_fournie_d_abord(self):
        lignes = [
            _ligne("A", "AVERTISSEMENT", 100, 1, regle="R-DOC", dossier="N1"),
            _ligne("B", "AVERTISSEMENT", 200, 1, regle="R-DOC", dossier="N2"),
            _ligne("C", "MAJEUR", 50, 1, regle="R-ID", dossier="N1"),
            _ligne("D", "BLOQUANT", None, 1, regle="R-ID", dossier="N1"),
        ]
        (identite, documents) = grouper_par_regle(lignes)
        # R-ID passe devant : la gravité la plus forte du groupe l'emporte sur le nombre.
        assert identite.code_regle == "R-ID" and identite.gravite == "BLOQUANT"
        assert identite.constats == 2 and identite.enjeu_cumule == Decimal(50) and identite.dossiers == 1
        assert documents.constats == 2 and documents.enjeu_cumule == Decimal(300) and documents.dossiers == 2


class TestLesReglages:
    def test_ce_qui_mentirait(self):
        with pytest.raises(ValidationError, match="inconnue"):
            ReglagesDeLaFile(gravites_traitees=("BLOQUANT", "URGENT"))
        with pytest.raises(ValidationError, match="toujours vide"):
            ReglagesDeLaFile(gravites_traitees=())
        with pytest.raises(ValidationError, match="ne se retirent pas"):
            ReglagesDeLaFile(gravites_traitees=("MAJEUR",))

    def test_le_fichier_livre(self):
        reglages = charger_les_reglages_de_la_file(REFERENTIEL)
        assert reglages.source == "pilotage/file_d_anomalies.yaml"
        assert reglages.seuil_anciennete_jours == 15
        assert reglages.gravites_traitees == ("BLOQUANT", "MAJEUR", "AVERTISSEMENT")
        assert charger_les_reglages_de_la_file(Path("/nulle/part")).source == "valeurs par défaut"


# ── La route ──────────────────────────────────────────────────────────────────

FILE = "/pilotage/file-d-anomalies"
REVISEUR = "a.bouba@cga-brcg.cm"
COMPTABLE = "l.fotso@cga-brcg.cm"
ADHERENT = "jp.nkoa@batimentplus.cm"
BATIMENT = "M081234567890P"


@pytest.fixture(autouse=True)
def neuf():
    reinitialiser_atelier()
    # Le 9 août : les pièces de juillet ont entre 9 et 40 jours, le seuil de sommeil est à 15.
    with horloge_figee(datetime(2026, 8, 9, 9, 0)):
        yield


def _client(courriel):
    client = TestClient(creer_application())
    reponse = client.post("/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO})
    assert reponse.status_code == 200, reponse.text
    return client


class TestLaRoute:
    def test_la_file_du_reviseur_et_son_ordre(self):
        vue = _client(REVISEUR).get(FILE).json()
        assert vue["seuil_anciennete_jours"] == 15
        assert vue["par_gravite"]["BLOQUANT"] >= 1
        # Aucune ligne d'information : la file ne montre que ce qui appelle une décision.
        assert "INFORMATION" not in vue["par_gravite"]
        gravites = [l_["gravite"] for l_ in vue["lignes"]]
        assert gravites == sorted(gravites, key=lambda g: ["BLOQUANT", "MAJEUR", "AVERTISSEMENT"].index(g))
        premiere = vue["lignes"][0]
        assert premiere["gravite"] == "BLOQUANT" and Decimal(premiere["enjeu"]) > 0
        assert premiere["anciennete"] > 0 and premiere["dort"] is True
        # Les bloquants sont rangés du plus coûteux au moins coûteux.
        enjeux = [Decimal(l_["enjeu"]) for l_ in vue["lignes"] if l_["gravite"] == "BLOQUANT"]
        assert enjeux == sorted(enjeux, reverse=True)
        # Les avertissements du jeu n'ont pas d'enjeu chiffré : c'est l'ancienneté qui les range,
        # du plus vieux au plus récent.
        ages = [l_["anciennete"] for l_ in vue["lignes"] if l_["gravite"] == "AVERTISSEMENT"]
        assert ages == sorted(ages, reverse=True) and len(ages) > 2

    def test_le_perimetre_et_les_filtres(self):
        reviseur = _client(REVISEUR).get(FILE).json()
        comptable = _client(COMPTABLE).get(FILE).json()
        dossiers_du_comptable = {l_["dossier"] for l_ in comptable["lignes"]}
        assert dossiers_du_comptable <= {l_["dossier"] for l_ in reviseur["lignes"]}
        assert len(comptable["lignes"]) < len(reviseur["lignes"]), "le comptable voit tout le cabinet"

        client = _client(REVISEUR)
        bloquantes = client.get(FILE, params={"gravite": "BLOQUANT"}).json()
        assert {l_["gravite"] for l_ in bloquantes["lignes"]} == {"BLOQUANT"}
        # Les compteurs de l'en-tête ne bougent pas quand on filtre.
        assert bloquantes["par_gravite"] == reviseur["par_gravite"]

        un_dossier = client.get(FILE, params={"entreprise": BATIMENT}).json()
        assert {l_["dossier"] for l_ in un_dossier["lignes"]} == {BATIMENT}

        regle = reviseur["groupes"][0]["code_regle"]
        par_regle = client.get(FILE, params={"regle": regle}).json()
        assert {l_["code_regle"] for l_ in par_regle["lignes"]} == {regle}
        assert par_regle["groupes"][0]["constats"] == len(par_regle["lignes"])

        deposant = reviseur["deposants"][0]
        par_deposant = client.get(FILE, params={"depose_par": deposant}).json()
        assert {l_["depose_par"] for l_ in par_deposant["lignes"]} == {deposant}

        # Une période de pièces, bornes incluses.
        juillet = client.get(FILE, params={"du": "2026-07-01", "au": "2026-07-31"}).json()
        assert all(l_["date_piece"].startswith("2026-07") for l_ in juillet["lignes"])
        # Bornes **incluses** : une pièce datée du jour de la borne reste dans la file.
        derniere = max(l_["date_piece"] for l_ in reviseur["lignes"])
        borne = client.get(FILE, params={"au": derniere}).json()
        assert any(l_["date_piece"] == derniere for l_ in borne["lignes"])
        assert client.get(FILE, params={"du": "2026-09-01"}).json()["lignes"] == []

    def test_les_gravites_traitees_viennent_du_referentiel(self, monkeypatch):
        from app.contextes.pilotage.adaptateurs.entrant import routes_file_d_anomalies

        monkeypatch.setattr(
            routes_file_d_anomalies,
            "_reglages",
            lambda: ReglagesDeLaFile(gravites_traitees=("BLOQUANT",)),
        )
        vue = _client(REVISEUR).get(FILE).json()
        assert {l_["gravite"] for l_ in vue["lignes"]} == {"BLOQUANT"}
        assert list(vue["par_gravite"]) == ["BLOQUANT"]

    def test_un_constat_ecarte_sort_de_la_file(self):
        client = _client(REVISEUR)
        avant = client.get(FILE).json()
        # Un avertissement : la politique du cabinet l'écarte sans second regard, et l'écart est
        # donc effectif tout de suite. Un majeur resterait « en attente », donc dans la file.
        vise = next(l_ for l_ in avant["lignes"] if l_["gravite"] == "AVERTISSEMENT")
        ecart = client.post(
            f"/conformite/pieces/{vise['piece']}/ecarts",
            json={
                "code_regle": vise["code_regle"],
                "motif": "Pièce d'appui obtenue du fournisseur et vérifiée au dossier du client.",
            },
        )
        assert ecart.status_code in (200, 201), ecart.text
        apres = client.get(FILE).json()
        assert not any(
            l_["piece"] == vise["piece"] and l_["code_regle"] == vise["code_regle"] for l_ in apres["lignes"]
        )
        assert len(apres["lignes"]) == len(avant["lignes"]) - 1

    def test_le_role(self):
        assert _client(ADHERENT).get(FILE).status_code == 403
        assert TestClient(creer_application()).get(FILE).status_code == 401
