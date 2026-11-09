"""Le code de dossier : ce qui rattache une seconde demande à la première (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

Le client qui a pris rendez-vous, ou payé ses frais de dossier, revient plus tard remplir
le formulaire. « Il doit dire qu'il a déjà payé, et le système fait le rapprochement. »
Le code, envoyé par courriel à la demande, désigne son dossier : la nouvelle demande s'y
rattache, avec tous les frais déjà versés.

⚠️ COURT, ET SANS CARACTÈRE AMBIGU. La référence interne (`dos-cf776b…`) ne se dicte pas au
téléphone et ne se recopie pas sans faute — l'accusé de dépôt l'a appris. Huit caractères,
en deux groupes, sans 0/O ni 1/I/L : « K7M4-Q2XP ».

⚠️ IL NE VAUT QUE POUR RATTACHER. Il n'ouvre aucune lecture du dossier : un code deviné
ne ferait qu'ajouter une demande au dossier de quelqu'un, et le cabinet la verrait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import secrets
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ALPHABET", "CodeDeDossier", "nouveau_code", "normaliser_le_code"]

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class CodeDeDossier(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Normalisé : majuscules, sans tiret ni espace (`K7M4Q2XP`).
    code: str = Field(min_length=8, max_length=8)
    dossier: str = Field(min_length=1)
    cree_le: datetime

    @property
    def lisible(self) -> str:
        return f"{self.code[:4]}-{self.code[4:]}"


def nouveau_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def normaliser_le_code(saisi: str) -> str:
    """« k7m4 - q2xp » → « K7M4Q2XP » : majuscules, sans tiret ni espace."""
    return "".join(c for c in saisi.upper() if c.isalnum())
