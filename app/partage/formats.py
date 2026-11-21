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

from app.partage.horloge import a_douala

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
    # ⚠️ 16 octobre 2026 : l'instant UTC était affiché tel quel, une heure trop tôt.
    local = a_douala(instant)
    return f"{local.day} {MOIS[local.month - 1]} {local.year} à {local.hour:02d}h{local.minute:02d}"


# ── Le montant en toutes lettres ─────────────────────────────────────────────
#
# ⚠️ POURQUOI UN DOCUMENT COMMERCIAL LE PORTE
#
# « Arrête la présente pro-forma à la somme de : Cinq cent cinq mille francs
# FCFA. » Les deux proformas que le cabinet emploie portent cette ligne, et ce
# n'est pas un ornement : elle est la seule protection contre un chiffre altéré
# après signature. Un « 5 » devenu « 8 » ne se voit pas ; « cinq cent » contre
# « 850 000 » se voit.
#
# ⚠️ LES PIÈGES DU FRANÇAIS, ET ILS SONT TOUS DANS LES CAS
#
# « quatre-vingts » prend un s, « quatre-vingt-un » non. « cent » prend un s au
# pluriel sauf suivi d'un autre nombre : « deux cents », « deux cent trois ».
# « mille » est invariable. Et « soixante et onze » s'écrit ainsi, pas
# « soixante-onze » au sens de 71 en français de référence — la réforme de 1990
# admet les traits d'union partout, et c'est ce que nous suivons, un document
# commercial gagnant à être lisible plutôt qu'érudit.

_UNITES = (
    "zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
    "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
)
_DIZAINES = {
    20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante",
    60: "soixante", 80: "quatre-vingt",
}


def _sous_cent(n: int, *, final: bool = True) -> str:
    """⚠️ `final` dit si ce nombre TERMINE l'écriture, et il commande un accord.

    « quatre-vingts » prend un s quand rien ne le suit ; « quatre-vingt mille »
    n'en prend pas. L'appelant est seul à savoir ce qui vient après, d'où ce
    paramètre plutôt qu'une règle devinée ici.
    """
    if n < 17:
        return _UNITES[n]
    if n < 20:
        return f"dix-{_UNITES[n - 10]}"
    # ⚠️ 70 et 90 n'ont pas de nom propre : ils se disent « soixante-dix » et
    # « quatre-vingt-dix », c'est-à-dire sur la dizaine du dessous.
    base = 60 if 70 <= n < 80 else 80 if n >= 80 else n - n % 10
    reste = n - base
    mot = _DIZAINES[base]
    if reste == 0:
        return f"{mot}s" if base == 80 and final else mot
    if reste == 1 and base in (20, 30, 40, 50, 60):
        return f"{mot}-et-un"
    # ⚠️ `final` se PROPAGE au reste : dans « cent quatre-vingt mille », c'est le
    # « vingt » final du groupe qui doit perdre son s, pas le « cent » de tête.
    # Sans cette propagation, 180 000 rendait « cent-quatre-vingts-mille ».
    return f"{mot}-{_sous_cent(reste, final=final)}"


def _sous_mille(n: int, *, final: bool = True) -> str:
    """⚠️ Même accord que `_sous_cent`, et il a été pris en défaut.

    Le premier jet rendait « trois-cents-mille » et « cinq-cents-mille ». C'est
    faux : *cent* ne prend l's que s'il termine le nombre, et « mille » le suit.
    Vu en déroulant la table des cas, pas en relisant le code.
    """
    if n < 100:
        return _sous_cent(n, final=final)
    centaines, reste = divmod(n, 100)
    tete = "cent" if centaines == 1 else f"{_sous_cent(centaines)}-cent"
    if reste == 0:
        return f"{tete}s" if centaines > 1 and final else tete
    return f"{tete}-{_sous_cent(reste, final=final)}"


def en_toutes_lettres(valeur: Decimal | int) -> str:
    """Un montant entier en lettres, pour un document commercial.

    ⚠️ **ENTIER, ET C'EST LE FRANC CFA QUI LE VEUT** : il n'a pas de subdivision
    en circulation. Recevoir un montant à décimales est donc le signe d'un calcul
    qui a dérapé ailleurs, et l'arrondir ici masquerait ce dérapage : on lève.
    """
    entier = Decimal(valeur)
    if entier != entier.to_integral_value():
        raise ValueError(
            f"montant à décimales en toutes lettres : {valeur}. Le franc CFA n'a "
            "pas de subdivision ; un montant non entier vient d'un calcul à "
            "corriger, pas d'un arrondi à faire ici."
        )
    n = int(entier)
    if n < 0:
        return f"moins {en_toutes_lettres(-n)}"
    if n < 1000:
        return _sous_mille(n)
    if n < 1_000_000:
        milliers, reste = divmod(n, 1000)
        # ⚠️ « mille » est invariable, et « un mille » ne se dit pas.
        # ⚠️ `final=False` : ce qui précède « mille » ne termine pas le nombre.
        tete = "mille" if milliers == 1 else f"{_sous_mille(milliers, final=False)}-mille"
        return tete if reste == 0 else f"{tete}-{_sous_mille(reste)}"
    millions, reste = divmod(n, 1_000_000)
    # ⚠️ « million » est un nom : il prend un s, contrairement à « mille ».
    tete = (
        "un million" if millions == 1 else f"{_sous_mille(millions, final=False)} millions"
    )
    return tete if reste == 0 else f"{tete} {en_toutes_lettres(reste)}"
