"""La recherche globale, fédérée entre les services qui la déclarent (pas 93).

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE FICHIER GARDE

- **La mécanique commune** : une seule normalisation (accents, casse, espaces), tous
  les termes exigés, les identifiants avant les libellés, une requête trop courte
  refusée une fois pour toutes.
- **La déclaration** : chaque source déclarée au registre a sa route, sous le préfixe de
  son service, qui accepte `q` et rend le contrat commun. Déclarer, c'est brancher :
  une déclaration fausse casserait l'écran de recherche sans que rien ne le dise.
- **Le périmètre** : chaque source cherche dans ce que la session voit déjà, et rien
  d'autre. Un résultat hors portefeuille révélerait qu'un dossier existe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
    reinitialiser_dossiers,
)
from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, Permission
from app.infrastructure.config import configuration
from app.main import creer_application
from app.partage.recherche import (
    ReglagesDeRecherche,
    RequeteTropCourte,
    ResultatDeRecherche,
    charger_les_reglages_de_recherche,
    classer,
    normaliser,
    pertinence,
)
from app.registre.services import SERVICES
from tests.conftest import exige_postgresql, ouvrir_une_session

COMPTABLE_INDUSTRIE = "l.fotso@cga-brcg.cm"  # BATIMENT, COLOMBE, AGRO
COMPTABLE_SERVICES = "c.ndongo@cga-brcg.cm"  # TCHOUMBA, NGUEMA
ADMINISTRATEUR = "s.onana@cga-brcg.cm"
DIRECTION = "b.mballa@cga-brcg.cm"
REVISEUR = "a.bouba@cga-brcg.cm"
ADHERENT_BATIMENT = "jp.nkoa@batimentplus.cm"
BATIMENT = "M081234567890P"


@pytest.fixture(autouse=True)
def prospects_neufs():
    reinitialiser_dossiers()
    yield
    reinitialiser_dossiers()


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _resultat(identifiant: str, titre: str, score: int) -> ResultatDeRecherche:
    return ResultatDeRecherche(nature="X", identifiant=identifiant, titre=titre, pertinence=score)


# ── La mécanique commune ──────────────────────────────────────────────────────


class TestLaMecanique:
    def test_accents_casse_et_espaces_ne_comptent_pas(self):
        assert normaliser("  Société BÂTIMENT   Plus ") == "societe batiment plus"
        assert normaliser(None) == ""

    def test_tous_les_termes_sont_exiges(self):
        """Ajouter un mot précise la recherche ; il ne l'élargit jamais."""
        libelles = ("SARL BATIMENT PLUS", "Douala")
        assert pertinence("batiment douala", libelles=libelles) is not None
        assert pertinence("batiment yaounde", libelles=libelles) is None

    def test_les_identifiants_passent_devant_les_libelles(self):
        assert pertinence("m081234567890p", identifiants=("M081234567890P",)) == 100
        assert pertinence("M0812", identifiants=("M081234567890P",)) == 80
        assert (
            pertinence("sarl", identifiants=("M081234567890P",), libelles=("SARL BATIMENT",)) == 60
        )
        assert pertinence("plus", libelles=("SARL BATIMENT PLUS",)) == 40

    @pytest.mark.parametrize("requete", ["a", "  ", "-- .", "ab"])
    def test_une_requete_trop_courte_est_refusee(self, requete):
        with pytest.raises(RequeteTropCourte):
            pertinence(requete, libelles=("abc",))

    def test_les_caracteres_significatifs_se_comptent_sur_tous_les_termes(self):
        assert pertinence("ab c", libelles=("ab cd",)) is not None

    def test_classer_trie_puis_borne_et_le_dit(self):
        reglages = ReglagesDeRecherche(resultats_par_source=2)
        reponse = classer(
            "s",
            [_resultat("3", "Zèbre", 40), _resultat("1", "beta", 100), _resultat("2", "Alpha", 40)],
            reglages,
        )
        assert [r.identifiant for r in reponse.resultats] == ["1", "2"]
        assert reponse.tronque is True


class TestLesReglagesViennentDuReferentiel:
    def test_sans_fichier_les_valeurs_sobres(self, tmp_path: Path):
        assert charger_les_reglages_de_recherche(tmp_path) == ReglagesDeRecherche(
            longueur_minimale=3, resultats_par_source=10
        )

    def test_une_cle_mal_orthographiee_fait_echouer_le_chargement(self, tmp_path: Path):
        (tmp_path / "recherche").mkdir()
        (tmp_path / "recherche" / "reglages.yaml").write_text(
            yaml.safe_dump({"longeur_minimale": 2}), encoding="utf-8"
        )
        with pytest.raises(ValidationError):
            charger_les_reglages_de_recherche(tmp_path)

    def test_le_fichier_livre_se_charge(self):
        charger_les_reglages_de_recherche(configuration().dossier_referentiel)


# ── La déclaration au registre ────────────────────────────────────────────────


class TestDeclarerCEstBrancher:
    @pytest.fixture(scope="class")
    def schema(self):
        return creer_application().openapi()

    @pytest.mark.parametrize("service", [s for s in SERVICES if s.recherche], ids=lambda s: s.nom)
    def test_chaque_source_declaree_existe_et_rend_le_contrat_commun(self, service, schema):
        source = service.recherche
        assert source.chemin.startswith(service.prefixes), (
            f"{service.nom} déclare {source.chemin} hors de ses préfixes {service.prefixes}"
        )
        operation = schema["paths"].get(source.chemin, {}).get("get")
        assert operation is not None, f"{source.chemin} déclarée et non montée"
        assert any(p["name"] == "q" for p in operation["parameters"])
        reponse = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert reponse["$ref"].endswith("/ReponseDeRecherche")
        assert source.permission in Permission.__members__

    def test_les_quatre_premieres_sources(self):
        assert {s.nom for s in SERVICES if s.recherche} == {
            "portefeuille",
            "collecte",
            "transverse",
            "souscription",
        }


class TestLesSourcesOuvertesALaSession:
    @pytest.mark.parametrize(
        ("courriel", "attendues"),
        [
            (COMPTABLE_INDUSTRIE, {"portefeuille", "collecte"}),
            # L'administrateur affecte les demandes entrantes : il les voit, donc les cherche.
            (ADMINISTRATEUR, {"transverse", "souscription"}),
            (DIRECTION, {"portefeuille", "collecte", "souscription"}),
            (ADHERENT_BATIMENT, {"portefeuille", "collecte"}),
        ],
    )
    def test_on_ne_propose_que_ce_que_la_session_peut_appeler(self, courriel, attendues):
        sources = _client(courriel).get("/transverse/services/recherche")
        assert sources.status_code == 200, sources.text
        assert {s["service"] for s in sources.json()} == attendues

    def test_une_source_declaree_mais_non_montee_n_est_pas_proposee(self, monkeypatch):
        """Un service retiré du déploiement garde sa déclaration : l'écran ne doit pas
        l'interroger, et afficher une erreur sur chaque recherche."""
        from app.contextes.transverse.adaptateurs.entrant import routes_registre
        from app.registre.services import Service, SourceDeRecherche

        fantome = Service(
            nom="fantome",
            lettre="Z",
            libelle="Fantôme",
            plan="Essai",
            objet="n'existe pas",
            prefixes=("/fantome",),
            recherche=SourceDeRecherche(
                chemin="/fantome/recherche", libelle="Fantômes", permission="LIRE_DOSSIER"
            ),
        )
        monkeypatch.setattr(routes_registre, "SERVICES", (*SERVICES, fantome))
        sources = _client(COMPTABLE_INDUSTRIE).get("/transverse/services/recherche").json()
        assert "fantome" not in {s["service"] for s in sources}

    def test_sans_session_rien(self):
        assert (
            TestClient(creer_application()).get("/transverse/services/recherche").status_code == 401
        )


# ── Par source, le périmètre ──────────────────────────────────────────────────


class TestLePerimetreEstCeluiDeLaListe:
    def test_un_dossier_hors_portefeuille_n_est_pas_trouve(self):
        trouve = _client(COMPTABLE_INDUSTRIE).get(
            "/portefeuille/recherche", params={"q": "bâtiment"}
        )
        assert [r["identifiant"] for r in trouve.json()["resultats"]] == [BATIMENT]
        assert trouve.json()["resultats"][0]["lien"] == f"/portefeuille/{BATIMENT}"
        ailleurs = _client(COMPTABLE_SERVICES).get(
            "/portefeuille/recherche", params={"q": "batiment"}
        )
        assert ailleurs.json()["resultats"] == []

    def test_l_adherent_ne_trouve_que_ses_pieces(self):
        pieces = _client(ADHERENT_BATIMENT).get("/collecte/recherche", params={"q": "2026"}).json()
        assert pieces["resultats"]
        assert {r["dossier"] for r in pieces["resultats"]} == {BATIMENT}

    def test_une_facture_se_trouve_par_sa_reference_et_ouvre_son_ecran(self):
        pieces = _client(COMPTABLE_INDUSTRIE).get(
            "/collecte/recherche", params={"q": "F-2026-0412"}
        )
        [premier, *_] = pieces.json()["resultats"]
        assert premier["pertinence"] == 100
        assert premier["lien"] == "/pieces/F-2026-0412"

    def test_au_dela_du_reglage_la_liste_se_dit_tronquee(self):
        reponse = _client(REVISEUR).get("/collecte/recherche", params={"q": "2026"}).json()
        assert len(reponse["resultats"]) == 10
        assert reponse["tronque"] is True

    @pytest.mark.parametrize(
        ("courriel", "chemin"),
        [
            (COMPTABLE_INDUSTRIE, "/portefeuille/recherche"),
            (COMPTABLE_INDUSTRIE, "/collecte/recherche"),
            (ADMINISTRATEUR, "/transverse/recherche"),
            (DIRECTION, "/acquisition/recherche"),
        ],
    )
    def test_une_requete_trop_courte_rend_422_partout(self, courriel, chemin):
        reponse = _client(courriel).get(chemin, params={"q": "a"})
        assert reponse.status_code == 422
        assert "3 caractères" in reponse.json()["detail"]

    def test_chaque_source_garde_sa_permission(self):
        comptable = _client(COMPTABLE_INDUSTRIE)
        assert comptable.get("/transverse/recherche", params={"q": "fotso"}).status_code == 403
        assert comptable.get("/acquisition/recherche", params={"q": "abena"}).status_code == 403


class TestLesComptesEtLesDemandes:
    def test_un_compte_se_trouve_sans_accent_et_par_son_courriel(self):
        administrateur = _client(ADMINISTRATEUR)
        par_nom = administrateur.get("/transverse/recherche", params={"q": "leonard"}).json()
        assert [r["identifiant"] for r in par_nom["resultats"]] == ["C-004"]
        exact = administrateur.get(
            "/transverse/recherche", params={"q": COMPTABLE_INDUSTRIE}
        ).json()
        assert exact["resultats"][0]["pertinence"] == 100

    def test_une_demande_se_trouve_par_un_telephone_ecrit_autrement(self):
        """Le prospect rappelle depuis « 699 11 22 33 » ; le dossier porte « +237699112233 »."""
        public = TestClient(creer_application())
        depot = public.post(
            "/acquisition/demandes",
            json={
                "nom": "Abéna Ndzana",
                "telephone": "699112233",
                "service_souhaite": "creation-sarl",
                "canal_prefere": "APPEL",
            },
        )
        assert depot.status_code in (200, 201, 202), depot.text
        direction = _client(DIRECTION)
        par_telephone = direction.get("/acquisition/recherche", params={"q": "699 11 22 33"}).json()
        assert [r["titre"] for r in par_telephone["resultats"]] == ["Abéna Ndzana"]
        assert par_telephone["resultats"][0]["pertinence"] == 100
        par_nom = direction.get("/acquisition/recherche", params={"q": "abena"}).json()
        assert par_nom["resultats"][0]["lien"].startswith("/acquisition/")


class TestSurUneVraieBase:
    pytestmark = exige_postgresql

    def test_les_pieces_et_les_dossiers_se_cherchent_en_base(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, COMPTABLE_INDUSTRIE)
        dossiers = client.get("/portefeuille/recherche", params={"q": "batiment"})
        assert dossiers.status_code == 200, dossiers.text
        assert [r["identifiant"] for r in dossiers.json()["resultats"]] == [BATIMENT]
        pieces = client.get("/collecte/recherche", params={"q": "2026"})
        assert pieces.status_code == 200, pieces.text
