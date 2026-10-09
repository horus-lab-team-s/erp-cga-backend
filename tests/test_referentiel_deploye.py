"""Le référentiel d'acquisition se lit là où la configuration le dit.

─────────────────────────────────────────────────────────────────────────────────
⚠️ LA PANNE QUE CE CAS TIENT

Le dossier du référentiel d'acquisition était écrit en dur, relatif au dépôt. Les
tests tournent depuis le dépôt : ils passaient. L'image Docker range le référentiel
ailleurs, `/application/donnees/referentiel`, et le dit par `CGA_DOSSIER_REFERENTIEL`.
Sur la pile déployée, toute la surface d'acquisition répondait donc 500 — la demande
de contact publique comprise, seule porte par laquelle un prospect demande à être
rappelé.

Le cas sert le référentiel depuis une COPIE, modifiée : WhatsApp y est actif, alors
qu'il est inactif dans le dépôt. Si la route lit la copie, elle voit WhatsApp ; si
elle lit encore le dépôt, elle ne le voit pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contextes.souscription.adaptateurs.entrant import routes_acquisition as R
from app.infrastructure import config
from app.main import creer_application

DEPOT = config.RACINE_DEPOT / "Docs" / "referentiel"


@pytest.fixture
def referentiel_deplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copie = tmp_path / "donnees" / "referentiel"
    shutil.copytree(DEPOT, copie)
    canaux = copie / "messagerie" / "canaux.yaml"
    texte = canaux.read_text(encoding="utf-8")
    assert "canal: WHATSAPP\n    actif: false" in texte
    canaux.write_text(
        texte.replace("canal: WHATSAPP\n    actif: false", "canal: WHATSAPP\n    actif: true"),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGA_DOSSIER_REFERENTIEL", str(copie))
    config.configuration.cache_clear()
    R._referentiel_d_acquisition.cache_clear()
    yield copie
    config.configuration.cache_clear()
    R._referentiel_d_acquisition.cache_clear()


def test_le_referentiel_se_lit_dans_le_dossier_configure(referentiel_deplace: Path):
    reponse = TestClient(creer_application()).get("/acquisition/canaux")
    assert reponse.status_code == 200, reponse.text
    assert "WHATSAPP" in reponse.json()["actifs"]


def test_sans_le_depot_la_demande_de_contact_repond(
    referentiel_deplace: Path, monkeypatch: pytest.MonkeyPatch
):
    # ⚠️ Le dépôt rendu introuvable : c'est la situation exacte de l'image, où le
    # référentiel n'est que dans le dossier configuré.
    monkeypatch.setattr(config, "RACINE_DEPOT", Path("/nulle-part"))
    reponse = TestClient(creer_application()).post(
        "/acquisition/demandes",
        json={
            "nom": "Essai Déploiement",
            "telephone": "677000555",
            "service_souhaite": "CREATION",
            "canal_prefere": "APPEL",
            "consentement_whatsapp": False,
            "version_du_texte": "v1",
        },
    )
    assert reponse.status_code in (200, 201), reponse.text
