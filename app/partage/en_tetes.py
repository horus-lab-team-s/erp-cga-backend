"""Les en-têtes HTTP des fichiers servis (15 octobre 2026)."""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

__all__ = ["disposition_en_ligne"]


def disposition_en_ligne(nom: str) -> str:
    """`Content-Disposition: inline` pour un nom quelconque (RFC 6266).

    ⚠️ Un nom choisi par un utilisateur (« Bail d’Akwa.pdf », un guillemet) partait tel
    quel : hors Latin-1, le serveur levait une erreur 500 ; un guillemet cassait l'en-tête.
    Le nom exact voyage encodé, un repli ASCII l'accompagne.
    """
    ascii_ = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode().replace('"', "")
    ascii_ = "".join(c for c in ascii_ if c.isprintable() and c != "\\") or "document"
    return f"inline; filename=\"{ascii_}\"; filename*=UTF-8''{quote(nom, safe='')}"
