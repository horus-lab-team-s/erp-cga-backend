"""Le tronc commun des agents Claude (16 octobre 2026) : `app/partage/agent_claude.py`.

⚠️ CE QUI EST GARDÉ
* aucune panne ne lève : chaque erreur de l'API devient un `Incident` nommé ;
* une réponse refusée, tronquée ou qui n'est pas du JSON est un incident, pas une donnée ;
* l'appel en flux porte la pensée adaptative, l'appel simple non ;
* un format que l'API ne lit pas ne part pas.

Aucun appel réel : le client est un faux.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.partage.agent_claude import Incident, bloc_du_document, demander_en_json

PDF = b"%PDF-1.4\n%fin"
DEMANDE = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _reponse(texte: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=texte)]
    )


class _Flux:
    def __init__(self, reponse):
        self._reponse = reponse

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._reponse


class _Faux:
    def __init__(self, reponse=None, erreur=None):
        self.reponse, self.erreur, self.appels = reponse, erreur, []
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=self._create, stream=self._stream)
        )

    def _create(self, **demande):
        self.appels.append(("create", demande))
        if self.erreur:
            raise self.erreur
        return self.reponse

    def _stream(self, **demande):
        self.appels.append(("stream", demande))
        if self.erreur:
            raise self.erreur
        return _Flux(self.reponse)


def _demander(client, *, en_flux=False):
    return demander_en_json(
        client,
        modele="claude-opus-5-5",
        consigne="consigne",
        contenu="question",
        schema={"type": "object"},
        effort="medium",
        max_tokens=1000,
        en_flux=en_flux,
        objet="Essai",
    )


def _statut(classe, code):
    return classe("refus", response=httpx2.Response(code, request=DEMANDE), body=None)


class TestLaReponse:
    def test_le_json_lu_est_rendu(self):
        assert _demander(_Faux(_reponse(json.dumps({"points": [1]})))) == {"points": [1]}

    def test_l_appel_en_flux_pense_l_appel_simple_non(self):
        simple, flux = _Faux(_reponse("{}")), _Faux(_reponse("{}"))
        _demander(simple)
        _demander(flux, en_flux=True)
        ((genre_s, demande_s),) = simple.appels
        ((genre_f, demande_f),) = flux.appels
        assert (genre_s, "thinking" in demande_s) == ("create", False)
        assert (genre_f, demande_f["thinking"]) == ("stream", {"type": "adaptive"})
        assert demande_f["output_config"]["format"] == {
            "type": "json_schema",
            "schema": {"type": "object"},
        }

    @pytest.mark.parametrize(
        ("reponse", "code"),
        [
            (_reponse("{}", "refusal"), "REFUS"),
            (_reponse('{"tron', "max_tokens"), "LONGUE"),
            (_reponse("pas du json"), "ILLISIBLE"),
        ],
    )
    def test_une_reponse_inutilisable_est_un_incident(self, reponse, code):
        assert _demander(_Faux(reponse)) == Incident(code)


class TestAucunePanneNeLeve:
    @pytest.mark.parametrize(
        ("erreur", "attendu"),
        [
            (_statut(anthropic.RateLimitError, 429), Incident("SATURE")),
            (anthropic.APITimeoutError(request=DEMANDE), Incident("DELAI")),
            (anthropic.APIConnectionError(request=DEMANDE), Incident("RESEAU")),
            (_statut(anthropic.InternalServerError, 500), Incident("INDISPONIBLE", 500)),
        ],
    )
    @pytest.mark.parametrize("en_flux", [False, True])
    def test_chaque_erreur_a_son_incident(self, erreur, attendu, en_flux):
        assert _demander(_Faux(erreur=erreur), en_flux=en_flux) == attendu


class TestLeDocument:
    def test_un_pdf_part_comme_document_une_image_comme_image(self):
        assert bloc_du_document(PDF)["type"] == "document"
        jpeg = bloc_du_document(b"\xff\xd8\xff\xe0" + b"0" * 20)
        assert (jpeg["type"], jpeg["source"]["media_type"]) == ("image", "image/jpeg")

    def test_un_autre_format_ne_part_pas(self):
        assert bloc_du_document(b"II*\x00tiff") is None
        assert bloc_du_document(b"GIF89a....") is None
