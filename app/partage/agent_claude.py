"""L'appel à Claude, en un seul endroit, pour les agents qui PROPOSENT (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE

Trois agents (la lecture des factures, l'analyse et la rédaction des contrats) recopiaient
le même tronc : créer le client, joindre le document, demander une réponse au schéma JSON,
traduire chaque panne en incident. Trois copies finissent par diverger : un délai corrigé
dans l'une, une erreur oubliée dans l'autre. Le tronc est ici ; chaque agent garde sa
consigne, son schéma, et ses mots pour dire à l'utilisateur ce qu'il fait d'une panne.

⚠️ RIEN N'Y LÈVE. Réseau coupé, limite de débit, refus, réponse tronquée ou illisible :
`demander_en_json` rend un `Incident`, et l'agent rend la main à l'humain. L'agent est
optionnel ; sa panne n'empêche jamais de traiter un dossier.

⚠️ `anthropic` s'importe à l'appel, jamais au chargement : sans clé, le produit tourne sans
que le paquet soit même lu.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.partage.magasin_de_fichiers import detecter_le_type

__all__ = ["Incident", "bloc_du_document", "client_anthropic", "demander_en_json"]

_journal = logging.getLogger(__name__)

#: Ce que l'API lit. Le TIFF n'y est pas : il se traite à la main.
_IMAGES = {"image/jpeg", "image/png"}


@dataclass(frozen=True)
class Incident:
    """Pourquoi il n'y a pas de réponse. `code` : SATURE, DELAI, RESEAU, INDISPONIBLE
    (avec `statut`), REFUS, LONGUE ou ILLISIBLE."""

    code: str
    statut: int | None = None


def client_anthropic(cle_api: str, *, delai_s: float) -> Any:
    """Le client de l'API, avec le délai propre à l'agent et deux nouvelles tentatives."""
    import anthropic

    return anthropic.Anthropic(
        api_key=cle_api, timeout=anthropic.Timeout(delai_s, connect=10.0), max_retries=2
    )


def bloc_du_document(contenu: bytes) -> dict | None:
    """Le document joint à la demande : un PDF ou une image. `None` pour un autre format,
    qui ne part pas."""
    type_mime = detecter_le_type(contenu)
    if type_mime == "application/pdf":
        genre = "document"
    elif type_mime in _IMAGES:
        genre = "image"
    else:
        return None
    donnees = base64.standard_b64encode(contenu).decode()
    return {"type": genre, "source": {"type": "base64", "media_type": type_mime, "data": donnees}}


def demander_en_json(
    client: Any,
    *,
    modele: str,
    consigne: str,
    contenu: str | list[dict],
    schema: dict,
    effort: str,
    max_tokens: int,
    en_flux: bool,
    objet: str,
) -> Any:
    """La réponse lue selon `schema`, ou un `Incident`.

    `en_flux` pour une réponse longue (un contrat) : un appel simple dépasserait les délais.
    La pensée adaptative l'accompagne : une analyse est un raisonnement, une lecture de
    facture n'en est pas un. `objet` nomme la demande dans le journal technique.
    """
    import anthropic

    demande: dict[str, Any] = {
        "model": modele,
        "max_tokens": max_tokens,
        # Un refus d'un filtre relance la demande sur le modèle de repli, dans le même appel.
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "system": consigne,
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        "messages": [{"role": "user", "content": contenu}],
    }
    try:
        if en_flux:
            with client.beta.messages.stream(**demande, thinking={"type": "adaptive"}) as flux:
                reponse = flux.get_final_message()
        else:
            reponse = client.beta.messages.create(**demande)
    except anthropic.RateLimitError:
        return Incident("SATURE")
    except anthropic.APITimeoutError:
        return Incident("DELAI")
    except anthropic.APIConnectionError:
        return Incident("RESEAU")
    except anthropic.APIStatusError as echec:
        _journal.warning("%s refusée par l'API : %s %s", objet, echec.status_code, echec.message)
        return Incident("INDISPONIBLE", echec.status_code)

    if reponse.stop_reason == "refusal":
        return Incident("REFUS")
    if reponse.stop_reason == "max_tokens":
        return Incident("LONGUE")
    texte = next((b.text for b in reponse.content if getattr(b, "type", None) == "text"), None)
    try:
        return json.loads(texte or "")
    except json.JSONDecodeError:
        return Incident("ILLISIBLE")
