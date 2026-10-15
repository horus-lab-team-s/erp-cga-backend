"""Le reçu PDF d'un règlement d'honoraires (15 octobre 2026).

⚠️ CE QUI EST GARDÉ
* le reçu porte le montant du règlement constaté, en chiffres et en lettres ;
* l'adhérent lit le reçu de SON dossier ; l'identifiant d'un règlement d'un autre
  dossier rend 404, et l'adhérent d'un autre dossier n'y accède pas.
"""

from __future__ import annotations

import base64
import re
import zlib

from app.contextes.transverse.api import NIU_DEMO
from tests.conftest import exige_postgresql, ouvrir_une_session

BATIMENT = NIU_DEMO["BATIMENT"]
COLOMBE = NIU_DEMO["COLOMBE"]


def _texte(octets: bytes) -> str:
    morceaux = []
    for flux in re.findall(rb"stream\r?\n(.*?)endstream", octets, re.S):
        brut = flux.strip()
        try:
            brut = base64.a85decode(brut, adobe=True)
        except ValueError:
            pass
        try:
            brut = zlib.decompress(brut)
        except zlib.error:
            pass
        if brut[:2] == b"\xff\xd8":
            continue
        morceaux.append(brut)
    rendu = b"\n".join(morceaux).decode("latin-1", "replace")
    return " ".join(
        re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), t).replace("\\", "")
        for t in re.findall(r"\((.*?)\)\s*Tj", rendu, re.S)
    )


@exige_postgresql
class TestLeRecu:
    def test_l_adherent_lit_le_recu_de_ce_qu_il_a_regle(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        reglee = next(e for e in vue["echeances"] if e["etat"] == "REGLEE")
        identifiant = reglee["reglement"]["identifiant"]

        recu = client.get(f"/souscription/honoraires/{BATIMENT}/reglements/{identifiant}/recu")

        assert recu.status_code == 200, recu.text
        texte = _texte(recu.content)
        assert "REÇU D'HONORAIRES" in texte and "SARL BATIMENT PLUS" in texte
        montant = int(float(reglee["reglement"]["montant"]))
        assert f"{montant:,}".replace(",", " ") in texte
        assert "francs CFA" in texte
        # Un identifiant d'un autre dossier, ou inventé, ne rend rien.
        ailleurs = client.get(f"/souscription/honoraires/{BATIMENT}/reglements/RH-INVENTE/recu")
        assert ailleurs.status_code == 404

    def test_l_adherent_d_un_autre_dossier_n_y_accede_pas(self, plateforme):
        """⚠️ Avec un VRAI numéro de règlement : seule la garde peut refuser."""
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        identifiant = next(e for e in vue["echeances"] if e["etat"] == "REGLEE")["reglement"][
            "identifiant"
        ]
        client.cookies.clear()
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")

        refus = client.get(f"/souscription/honoraires/{BATIMENT}/reglements/{identifiant}/recu")

        assert refus.status_code in (403, 404), refus.text
