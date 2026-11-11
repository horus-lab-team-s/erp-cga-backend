"""Le jeu de la recette en présentiel, versé depuis un fichier (20 octobre 2026)."""

from __future__ import annotations

import pytest

from app.jeu_de_presentation import JeuRefuse, verser_le_jeu
from tests.conftest import exige_postgresql, ouvrir_une_session

JEU = """
mot_de_passe: cabinet brcg douala 2026
equipe:
  - {prenom: Julienne, nom: ATEBA, courriel: j.ateba@cga-brcg.cm,
     role: CHARGE_FORMALITES, portee: tout}
clients:
  - niu: M041122334455Q
    denomination: QUINCAILLERIE MODERNE DE BONABERI SARL
    forme: SARL
    date_creation: 2019-03-14
    regime: REEL
    adherent: {prenom: Samuel, nom: NDJOCK, courriel: s.ndjock@quincaillerie-moderne.cm}
    suivi_par:
      - {courriel: l.fotso@cga-brcg.cm, role: COMPTABLE}
"""


def test_un_environnement_expose_est_refuse(tmp_path, monkeypatch):
    from app import jeu_de_presentation
    from app.infrastructure import config

    reglee = config.configuration().model_copy(update={"environnement": "production"})
    monkeypatch.setattr(jeu_de_presentation, "configuration", lambda: reglee)
    (tmp_path / "jeu.yaml").write_text(JEU)
    with pytest.raises(JeuRefuse, match="production"):
        verser_le_jeu(tmp_path / "jeu.yaml")


@exige_postgresql
class TestSurUneBaseDeDemonstration:
    def test_le_jeu_se_verse_puis_se_relance_sans_rien_recreer(self, plateforme, tmp_path):
        (tmp_path / "jeu.yaml").write_text(JEU)
        premier = verser_le_jeu(tmp_path / "jeu.yaml")
        second = verser_le_jeu(tmp_path / "jeu.yaml")

        assert any(ligne.startswith("entreprise M041122334455Q") for ligne in premier["crees"])
        assert "compte s.ndjock@quincaillerie-moderne.cm" in premier["crees"]
        assert second["crees"] == []

    def test_l_adherent_et_le_comptable_voient_le_dossier_et_lui_seul(self, plateforme, tmp_path):
        (tmp_path / "jeu.yaml").write_text(JEU)
        verser_le_jeu(tmp_path / "jeu.yaml")
        client = plateforme
        ouvrir_une_session(client, "s.ndjock@quincaillerie-moderne.cm")
        assert client.get("/obligations/dossiers/M041122334455Q/mes-echeances").status_code == 200
        assert client.get("/obligations/dossiers/M081234567890P/mes-echeances").status_code == 404
        client.cookies.clear()
        ouvrir_une_session(client, "c.ndongo@cga-brcg.cm")
        assert client.get("/obligations/dossiers/M041122334455Q/mes-echeances").status_code == 404
        client.cookies.clear()
        ouvrir_une_session(client, "l.fotso@cga-brcg.cm")
        assert client.get("/obligations/dossiers/M041122334455Q/mes-echeances").status_code == 200
