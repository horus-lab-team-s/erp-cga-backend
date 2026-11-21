"""Le NIU et le RCCM au format du référentiel, vérifiés **à la saisie** (21 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE SEULE FONCTION

Recette en ligne du 20 novembre 2026 : un NIU à treize chiffres et un RCCM
« RC/DLA/2026/2/2026 » ont été acceptés à la création, puis le portefeuille les a
marqués bloquants (`validation_identifiants.py`). La revue qui a suivi a trouvé la
même brèche à l'inscription au portefeuille, au devis et à l'engagement en ligne, et
au registre des tiers. Chaque saisie lit désormais la même règle, au même endroit :
les codes `FORMAT_NIU` et `FORMAT_RCCM` du référentiel, à une date.

⚠️ Le NIU devient la clé du dossier et ne se corrige plus ensuite : un refus à la
saisie, avec le modèle à suivre, coûte une seconde ; un NIU faux au portefeuille
coûte une reprise en base.

⚠️ Un format absent du référentiel ne bloque pas la saisie : on ne devine pas. La
revue du portefeuille, elle, le signale comme « non vérifiable ».
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from datetime import date

from app.contextes.referentiel.application.service_parametres import (
    AucuneVersionApplicable,
    ParametreInconnu,
    ServiceParametres,
)

__all__ = ["IdentifiantHorsFormat", "identifiant_au_format"]

_FORMATS = {
    "NIU": ("FORMAT_NIU", "une lettre, douze chiffres, une lettre : M112600090017R"),
    "RCCM": ("FORMAT_RCCM", "RC/DLA/2026/B/9001"),
}


class IdentifiantHorsFormat(ValueError):
    """La valeur ne suit pas le format en vigueur ; le message donne le modèle."""


def identifiant_au_format(
    nature: str, valeur: str, a_la_date: date, parametres: ServiceParametres
) -> str:
    """La valeur remise au format d'écriture (majuscules, sans espaces), ou un refus.

    `nature` vaut « NIU » ou « RCCM ».
    """
    propre = re.sub(r"\s+", "", valeur).upper()
    code, exemple = _FORMATS[nature]
    try:
        motif = parametres.valeur_texte(code, a_la_date)
    except (ParametreInconnu, AucuneVersionApplicable):
        return propre
    if not re.match(motif, propre):
        raise IdentifiantHorsFormat(
            f"{nature} « {valeur.strip()} » : il ne suit pas le format attendu ({exemple}). "
            "Recopiez-le tel qu'il figure sur le document délivré."
        )
    return propre
