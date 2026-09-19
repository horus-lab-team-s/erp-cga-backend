"""« Qui a accès à ce dossier » ne doit pas dire quels autres dossiers le cabinet suit.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE (pas 86)

`GET /transverse/dossiers/{niu}/acces` rendait chaque habilitation entière, portée
comprise. Essai avant correction, sur les données de démonstration : l'adhérent de SARL
BATIMENT PLUS (A-001), qui détient `LIRE_DOSSIER` sur son propre dossier, lisait la portée
du chargé de clientèle C-007, soit **les NIU de six autres clients du cabinet**. Un
comptable lisait de même les dossiers confiés à ses collègues.

Or le projet s'interdit précisément cela : un dossier hors périmètre rend 404 plutôt que
403, pour ne rien apprendre de son existence. Une liste de NIU dans une réponse ouverte
aux adhérents annulait la règle d'un coup.

La réponse garde tout ce qui répond à la question (qui, quel rôle, depuis quand, par qui
accordé), et ramène la portée au seul dossier demandé. Une habilitation transverse garde
sa portée nulle, qui ne nomme aucun dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.api import MOT_DE_PASSE_DEMO, NIU_DEMO, reinitialiser_atelier
from app.main import creer_application

BATIMENT = NIU_DEMO["BATIMENT"]
JOUR = {"a_la_date": "2026-09-15"}


@pytest.fixture
def application() -> Iterator[None]:
    reinitialiser_atelier()
    yield


def _client(courriel: str) -> TestClient:
    client = TestClient(creer_application())
    reponse = client.post(
        "/transverse/session", json={"courriel": courriel, "mot_de_passe": MOT_DE_PASSE_DEMO}
    )
    assert reponse.status_code == 200, reponse.text
    return client


def _nius_cites(reponse) -> set[str]:
    return {niu for h in reponse.json() for niu in (h["portee"] or [])}


class TestAucunAutreDossierNeFuit:
    @pytest.mark.parametrize(
        "courriel", ["jp.nkoa@batimentplus.cm", "l.fotso@cga-brcg.cm", "b.mballa@cga-brcg.cm"]
    )
    def test_la_reponse_ne_cite_que_le_dossier_demande(self, application, courriel):
        reponse = _client(courriel).get(f"/transverse/dossiers/{BATIMENT}/acces", params=JOUR)
        assert reponse.status_code == 200, reponse.text
        assert _nius_cites(reponse) == {BATIMENT}, (
            f"d'autres dossiers du cabinet sont nommés : {_nius_cites(reponse) - {BATIMENT}}"
        )

    def test_la_reponse_repond_toujours_a_la_question(self, application):
        """Ramener la portée ne doit rien retirer d'autre : qui, quel rôle, et les
        habilitations transverses, qui couvrent ce dossier aussi."""
        habilitations = (
            _client("jp.nkoa@batimentplus.cm")
            .get(f"/transverse/dossiers/{BATIMENT}/acces", params=JOUR)
            .json()
        )
        par_compte = {(h["compte"], h["role"]): h for h in habilitations}
        assert ("C-007", "CHARGE_CLIENTELE") in par_compte
        assert ("C-004", "COMPTABLE") in par_compte
        assert ("A-001", "ADHERENT") in par_compte
        direction = par_compte[("C-001", "DIRECTION")]
        assert direction["transverse"] is True
        assert direction["portee"] is None

    def test_la_portee_enregistree_n_est_pas_modifiee(self, application):
        """La réponse est une vue : l'habilitation en base garde tous ses dossiers."""
        from app.contextes.transverse.api import atelier

        adherent = _client("jp.nkoa@batimentplus.cm")
        adherent.get(f"/transverse/dossiers/{BATIMENT}/acces", params=JOUR)
        chargee = atelier().habilitations.lire("H-007")
        assert len(chargee.portee) > 1

    def test_un_dossier_hors_perimetre_reste_refuse(self, application):
        reponse = _client("jp.nkoa@batimentplus.cm").get(
            f"/transverse/dossiers/{NIU_DEMO['COLOMBE']}/acces", params=JOUR
        )
        assert reponse.status_code == 403
