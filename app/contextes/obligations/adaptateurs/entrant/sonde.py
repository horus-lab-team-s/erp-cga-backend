"""La sonde du contexte F : l'échéancier a-t-il de quoi être bâti ?"""

from __future__ import annotations

from app.contextes.obligations.adaptateurs.sortant.catalogue_obligations import (
    CATALOGUE_OBLIGATIONS,
)
from app.registre.etat import Verdict

__all__ = ["sonde_des_obligations"]


def sonde_des_obligations() -> Verdict | None:
    """Le catalogue des obligations est-il chargé ?

    ⚠️ Sans lui, aucune échéance n'est connue. **Le silence est ici plus coûteux
    qu'une erreur** : un échéancier vide ressemble à un adhérent sans obligation,
    et les pénalités courent pendant qu'on croit que tout va bien.
    """
    if not CATALOGUE_OBLIGATIONS:
        return Verdict.panne(
            "le catalogue des obligations est vide : aucune échéance ne serait "
            "connue, et les pénalités courraient sans que rien ne le signale"
        )
    return None
