"""Formats de restitution — § 4 du dossier de design.

Ces règles ne sont pas cosmétiques : une balance dont les montants ne sont pas alignés et
tabulaires est illisible, et un montant négatif rendu par un signe moins isolé se confond
avec un tiret dans une colonne dense.

Le séparateur de milliers est une **espace insécable étroite** (U+202F) : elle empêche un
montant de se couper en fin de ligne. Les tests de rendu la normalisent avant comparaison.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "ESPACE_FINE",
    "MOIS",
    "date_courte",
    "date_longue",
    "moment_long",
    "montant",
    "montant_fcfa",
    "taux",
]

#: Espace insécable étroite, U+202F.
ESPACE_FINE = " "

MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def montant(valeur: Decimal | int | float) -> str:
    """Séparateur espace, aucune décimale, négatif entre parenthèses et jamais signé.

    2350000 → « 2 350 000 » · -450000 → « (450 000) »
    """
    entier = Decimal(str(valeur)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    groupes = f"{abs(entier):,}".replace(",", ESPACE_FINE)
    return f"({groupes})" if entier < 0 else groupes


def montant_fcfa(valeur: Decimal | int | float) -> str:
    """2350000 → « 2 350 000 FCFA »."""
    return f"{montant(valeur)}{ESPACE_FINE}FCFA"


def taux(valeur: Decimal | int | float, decimales: int = 2) -> str:
    """Virgule décimale, symbole séparé par une espace fine. 19.25 → « 19,25 % »."""
    rendu = f"{Decimal(str(valeur)):.{decimales}f}"
    if "." in rendu:
        rendu = rendu.rstrip("0").rstrip(".")
    return f"{rendu.replace('.', ',')}{ESPACE_FINE}%"


def date_longue(jour: date) -> str:
    """Forme lisible. 2026-08-15 → « 15 août 2026 »."""
    return f"{jour.day} {MOIS[jour.month - 1]} {jour.year}"


def date_courte(jour: date) -> str:
    """Forme compacte réservée aux tableaux denses. 2026-08-15 → « 15/08/2026 »."""
    return f"{jour.day:02d}/{jour.month:02d}/{jour.year}"


def moment_long(instant: datetime) -> str:
    """Un instant, lisible par un destinataire. « 30 août 2026 à 14h22 ».

    ⚠️ Réservé à ce qui est **lu par un humain** — un courriel, un écran. Une
    date d'expiration rendue en ISO (`2026-08-30T14:22:11`) oblige le lecteur à
    décoder un format machine pour savoir s'il lui reste une heure ou trois
    jours, et cette hésitation-là fait rater des délais.

    Sans fuseau affiché : le cabinet et ses adhérents sont tous en WAT, et
    ajouter « UTC+1 » sur chaque échéance ferait du bruit pour un lecteur qui
    n'a rien à convertir. ⚠️ Le jour où un adhérent de la diaspora existe, ce
    silence devient un défaut.
    """
    return f"{date_longue(instant.date())} à {instant.hour:02d}h{instant.minute:02d}"
