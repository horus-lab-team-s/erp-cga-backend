"""Ce que toute réponse porte, et ce que les portes publiques refusent.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Mesuré sur la pile en service, en éprouvant la surface de sécurité :

  1. le témoin de session portait `secure=False` ÉCRIT EN DUR, avec un commentaire
     disant de le passer à `True` en production. Un commentaire n'est pas un
     mécanisme : le jour de la mise en ligne, le témoin serait parti en clair ;
  2. aucune réponse ne portait d'en-tête de sécurité — ni `nosniff`, ni politique
     de référent, ni refus d'encadrement — et les manifestes de déploiement n'en
     ajoutaient pas davantage ;
  3. les portes PUBLIQUES du parcours commercial n'avaient aucune limitation de
     débit, alors que la connexion et le devis en avaient une. Le trou s'est
     aggravé le jour où l'affectation est devenue automatique : un automate qui
     martèle le formulaire occupe désormais des gens, pas seulement une table.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contextes.transverse.adaptateurs.entrant.limitation import (
    REGLES_PAR_DEFAUT,
    Limiteur,
    vider_le_limiteur,
)
from app.infrastructure import config
from app.main import creer_application


@pytest.fixture(autouse=True)
def limiteur_neuf():
    vider_le_limiteur()
    yield
    vider_le_limiteur()


def test_toute_reponse_porte_les_entetes_de_securite():
    reponse = TestClient(creer_application()).get("/sante")
    assert reponse.headers["X-Content-Type-Options"] == "nosniff"
    assert reponse.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert reponse.headers["X-Frame-Options"] == "DENY"


def test_sans_https_aucun_hsts():
    """⚠️ Posé en développement, HSTS interdirait `http://localhost` au navigateur
    pendant six mois, pour TOUS les projets du poste : la directive porte sur l'hôte."""
    reponse = TestClient(creer_application()).get("/sante")
    assert "Strict-Transport-Security" not in reponse.headers


def test_le_temoin_devient_chiffre_quand_l_api_est_en_https(monkeypatch):
    """Le drapeau `secure` suit l'adresse publique, et rien d'autre."""
    monkeypatch.setenv("CGA_ADRESSE_PUBLIQUE", "https://api.cga-brcg.cm")
    config.configuration.cache_clear()
    try:
        assert config.configuration().temoin_securise is True
        reponse = TestClient(creer_application()).get("/sante")
        assert "Strict-Transport-Security" in reponse.headers
    finally:
        config.configuration.cache_clear()


@pytest.mark.parametrize(
    ("methode", "chemin"),
    [
        ("POST", "/acquisition/demandes"),
        ("POST", "/acquisition/devis/DV-quelconque/rappel"),
        ("POST", "/acquisition/proformas/PRO-2026-0001/acceptation"),
    ],
)
def test_les_portes_publiques_du_parcours_sont_bornees(methode, chemin):
    """⚠️ Le chemin porte une RÉFÉRENCE : une règle à correspondance exacte ne
    l'aurait jamais attrapé, et serait restée décorative."""
    limiteur = Limiteur(regles=REGLES_PAR_DEFAUT)
    regle = limiteur.regle_pour(methode, chemin)
    assert regle is not None, f"{methode} {chemin} n'est borné par aucune règle"
    assert regle.motif


def test_le_formulaire_public_refuse_le_martelement():
    """Au-delà du seuil, la porte se ferme — et le refus n'est pas compté, sans
    quoi un automate qui insiste bloquerait l'adresse indéfiniment."""
    client = TestClient(creer_application())
    corps = {
        "nom": "Automate Essai",
        "telephone": "699000111",
        "service_souhaite": "CREATION",
        "canal_prefere": "APPEL",
    }
    refuse = None
    for _ in range(40):
        reponse = client.post("/acquisition/demandes", json=corps)
        if reponse.status_code == 429:
            refuse = reponse
            break
    assert refuse is not None, "le formulaire public accepte un martèlement sans fin"
    assert "réessayer" in refuse.text.lower() or "trop" in refuse.text.lower()
