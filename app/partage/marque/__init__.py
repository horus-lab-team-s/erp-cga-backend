"""La marque du cabinet, en un seul endroit.

Le logo sert au document de la proforma et à l'en-tête des courriels. Deux
contextes le lisent ; le ranger dans l'un obligerait l'autre à franchir une
frontière que le graphe des contextes interdit, ou à en garder une copie qui
finirait par ne plus être la même image.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["LOGO", "logo_disponible"]

#: Le logo en couleur, 288 x 162 points, sur fond blanc.
LOGO = Path(__file__).parent / "cga-logo-couleur.jpg"


def logo_disponible() -> bool:
    """⚠️ Son absence ne doit rien faire échouer : une image mal construite émet
    encore des proformas et des courriels, sans la marque."""
    return LOGO.is_file()
