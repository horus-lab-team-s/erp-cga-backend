"""ChatGPT, l'autre fournisseur des agents (30 octobre 2026) : `app/partage/agent_claude.py`.

⚠️ CE QUI EST GARDÉ
* le choix du fournisseur : celui qui est nommé, ou celui dont la clé est posée — jamais une
  bascule silencieuse chez l'autre quand la clé du fournisseur nommé manque ;
* la demande de l'agent, écrite au format de Claude, arrive chez OpenAI sans perte : la
  consigne, l'effort, le schéma, et le document (PDF ou photo, lu par le modèle : l'OCR) ;
* les mêmes incidents que pour Claude : rien ne lève, une réponse tronquée, refusée ou qui
  n'est pas du JSON n'est jamais une donnée.

Aucun appel réel : le client est un faux.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx2
import openai
import pytest

from app.infrastructure.config import Configuration, moteur_ia
from app.partage.agent_claude import (
    ClientOpenAI,
    Incident,
    bloc_du_document,
    client_du_fournisseur,
    demander_en_json,
)

PDF = b"%PDF-1.4\n%fin"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
DEMANDE = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _reponse(
    texte: str = "", *, status: str = "completed", raison: str | None = None, refus: bool = False
):
    contenu = (
        [SimpleNamespace(type="refusal", refusal="non")]
        if refus
        else [SimpleNamespace(type="output_text", text=texte)]
    )
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=raison) if raison else None,
        output=[SimpleNamespace(type="message", content=contenu)],
        output_text="" if refus else texte,
    )


class _Faux:
    def __init__(self, reponse=None, erreur=None):
        self.reponse, self.erreur, self.appels = reponse, erreur, []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **demande):
        self.appels.append(demande)
        if self.erreur:
            raise self.erreur
        return self.reponse


def _demander(faux: _Faux, contenu="Lis cette pièce.", **reste):
    return demander_en_json(
        ClientOpenAI(faux),
        modele="gpt-5.5",
        consigne="Tu es l'agent comptable.",
        contenu=contenu,
        schema={"type": "object", "properties": {"montant": {"type": "integer"}}},
        effort=reste.pop("effort", "medium"),
        max_tokens=2000,
        en_flux=reste.pop("en_flux", False),
        objet="la lecture",
    )


# ── Le choix du fournisseur ──────────────────────────────────────────────────


def test_sans_aucune_cle_aucun_moteur():
    assert moteur_ia(Configuration()) is None


def test_la_seule_cle_posee_designe_le_fournisseur():
    moteur = moteur_ia(Configuration(openai_cle_api="sk-x"))
    assert (moteur.fournisseur, moteur.modele) == ("openai", "gpt-5.5")


def test_les_deux_cles_sans_choix_claude_comme_avant():
    assert (
        moteur_ia(Configuration(openai_cle_api="sk-x", anthropic_cle_api="a")).fournisseur
        == "anthropic"
    )


def test_le_fournisseur_nomme_l_emporte():
    reglages = Configuration(openai_cle_api="sk-x", anthropic_cle_api="a", ia_fournisseur="openai")
    assert moteur_ia(reglages).fournisseur == "openai"


def test_le_fournisseur_nomme_sans_sa_cle_coupe_les_agents():
    # Pas de bascule silencieuse chez Claude : le cabinet a choisi OpenAI.
    assert moteur_ia(Configuration(anthropic_cle_api="a", ia_fournisseur="openai")) is None


def test_le_client_d_openai_est_reconnu():
    assert isinstance(client_du_fournisseur("openai", "sk-x", delai_s=5.0), ClientOpenAI)


# ── La demande, traduite ─────────────────────────────────────────────────────


def test_la_demande_arrive_entiere_chez_openai():
    faux = _Faux(_reponse('{"montant": 119250}'))
    assert _demander(faux) == {"montant": 119250}
    demande = faux.appels[0]
    assert demande["model"] == "gpt-5.5"
    assert demande["instructions"] == "Tu es l'agent comptable."
    assert demande["reasoning"] == {"effort": "medium"}
    assert demande["max_output_tokens"] == 2000
    assert demande["text"]["format"]["type"] == "json_schema"
    assert demande["text"]["format"]["schema"]["properties"]["montant"] == {"type": "integer"}
    assert demande["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Lis cette pièce."}]}
    ]


def test_le_pdf_part_en_fichier_et_la_photo_en_image():
    faux = _Faux(_reponse("{}"))
    _demander(
        faux,
        contenu=[bloc_du_document(PDF), bloc_du_document(PNG), {"type": "text", "text": "Lis."}],
    )
    pdf, photo, texte = faux.appels[0]["input"][0]["content"]
    assert pdf["type"] == "input_file" and pdf["filename"].endswith(".pdf")
    assert base64.b64decode(pdf["file_data"].split(",", 1)[1]) == PDF
    assert pdf["file_data"].startswith("data:application/pdf;base64,")
    assert photo["type"] == "input_image" and photo["image_url"].startswith(
        "data:image/png;base64,"
    )
    assert texte == {"type": "input_text", "text": "Lis."}


def test_l_appel_long_passe_aussi():
    faux = _Faux(_reponse(json.dumps({"montant": 1})))
    assert _demander(faux, en_flux=True, effort="high") == {"montant": 1}
    assert faux.appels[0]["reasoning"] == {"effort": "high"}


# ── Les incidents ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reponse", "code"),
    [
        (_reponse(status="incomplete", raison="max_output_tokens"), "LONGUE"),
        (_reponse(status="incomplete", raison="content_filter"), "REFUS"),
        (_reponse(refus=True), "REFUS"),
        (_reponse("ce n'est pas du JSON"), "ILLISIBLE"),
    ],
)
def test_une_reponse_inutilisable_est_un_incident(reponse, code):
    assert _demander(_Faux(reponse)) == Incident(code)


@pytest.mark.parametrize(
    ("erreur", "attendu"),
    [
        (
            openai.RateLimitError(
                "trop", response=httpx2.Response(429, request=DEMANDE), body=None
            ),
            Incident("SATURE"),
        ),
        (openai.APITimeoutError(request=DEMANDE), Incident("DELAI")),
        (openai.APIConnectionError(request=DEMANDE), Incident("RESEAU")),
        (
            openai.InternalServerError(
                "panne", response=httpx2.Response(503, request=DEMANDE), body=None
            ),
            Incident("INDISPONIBLE", 503),
        ),
    ],
)
def test_aucune_panne_ne_leve(erreur, attendu):
    assert _demander(_Faux(erreur=erreur)) == attendu


def test_l_agent_de_lecture_signe_avec_chatgpt():
    from app.contextes.collecte.adaptateurs.sortant.extraction_claude import ExtractionClaude

    lecteur = ExtractionClaude(
        cle_api="sk-x", modele="gpt-5.5", fournisseur="openai", client=ClientOpenAI(_Faux())
    )
    assert lecteur.MOTEUR == "chatgpt"
    assert ExtractionClaude(cle_api="a", modele="m", client=object()).MOTEUR == "claude"
