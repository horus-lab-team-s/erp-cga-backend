"""Le reçu de dépôt d'une déclaration, en PDF (18 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le reçu se rend de la même lecture que la liste : même numéro, même déclaration ;
* il dit qu'il ne remplace pas l'accusé de l'administration ;
* un accusé inconnu, ou d'un autre dossier, rend 404.
"""

from __future__ import annotations

from tests.conftest import exige_postgresql, ouvrir_une_session
from tests.test_recu_honoraires import _texte

BATIMENT = "M081234567890P"
DOCUMENTS = f"/obligations/dossiers/{BATIMENT}/mes-documents"


@exige_postgresql
class TestLeRecuDeDepot:
    def test_il_reprend_la_ligne_de_la_liste(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        (premier, *_) = client.get(DOCUMENTS).json()["documents"]

        recu = client.get(f"{DOCUMENTS}/{premier['numero']}/recu")

        assert recu.status_code == 200, recu.text
        assert recu.headers["content-type"] == "application/pdf"
        texte = _texte(recu.content)
        assert premier["numero"] in texte and "Reçu de dépôt" in texte
        assert "SARL BATIMENT PLUS" in texte and "ne remplace pas" in texte
        assert client.get(f"{DOCUMENTS}/ACCUSE-INVENTE/recu").status_code == 404

    def test_un_autre_adherent_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        (premier, *_) = client.get(DOCUMENTS).json()["documents"]
        client.cookies.clear()
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        assert client.get(f"{DOCUMENTS}/{premier['numero']}/recu").status_code == 404
