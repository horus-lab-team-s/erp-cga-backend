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
        # 18 octobre 2026 : le cabinet encaisse ; le nom du collaborateur reste interne.
        assert "Encaissé par" not in texte
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


@exige_postgresql
class TestLaNoteDHonoraires:
    """18 octobre 2026 (points 19 et 20) : la note d'une échéance, avant comme après règlement."""

    def test_l_adherent_lit_la_note_de_ce_qu_il_doit(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "jp.nkoa@batimentplus.cm")
        vue = client.get(f"/souscription/honoraires/{BATIMENT}").json()
        due = next(e for e in vue["echeances"] if e["etat"] in ("A_REGLER", "EN_RETARD"))

        note = client.get(f"/souscription/honoraires/{BATIMENT}/echeances/{due['debut']}/note")

        assert note.status_code == 200, note.text
        texte = _texte(note.content)
        assert "Note d'honoraires" in texte and "NET" in texte and "SARL BATIMENT PLUS" in texte
        montant = int(float(due["montant"]))
        assert f"{montant:,}".replace(",", " ") in texte
        inconnue = client.get(f"/souscription/honoraires/{BATIMENT}/echeances/1999-01-01/note")
        assert inconnue.status_code == 404

    def test_un_autre_adherent_n_y_accede_pas(self, plateforme):
        client = plateforme
        ouvrir_une_session(client, "mc.essomba@lacolombe.cm")
        refus = client.get(f"/souscription/honoraires/{BATIMENT}/echeances/2026-01-01/note")
        assert refus.status_code in (403, 404)
