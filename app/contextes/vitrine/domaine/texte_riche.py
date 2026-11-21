"""Le texte mis en forme d'un mini-éditeur, en blocs que le site rend sans HTML (16 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN SOUS-ENSEMBLE DE MARKDOWN, ET PAS DU HTML

Le cabinet rédige les qualifications d'un poste dans la console : il veut du gras, des
listes, des titres. Le candidat les lit sur le site. Accepter du HTML saisi ouvrirait la
porte à l'injection de script sur une page publique ; la vitrine s'interdit d'ailleurs
tout `dangerouslySetInnerHTML`.

Le texte est donc conservé tel que l'éditeur l'écrit, dans un Markdown réduit, et c'est
ici qu'il devient des blocs typés :

    ### Titre              → un intertitre
    - point                → un point de liste (les points consécutifs font une liste)
    paragraphe             → un paragraphe ; une ligne vide en sépare deux
    **gras**, *italique*   → dans le texte de chacun

Rien d'autre n'est interprété : un chevron, un lien, une balise restent du texte, et le
site les affiche comme tels.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BlocDeTexte",
    "IntertitreDeTexte",
    "ListeDeTexte",
    "ParagrapheDeTexte",
    "Segment",
    "en_blocs",
]


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True)

    texte: str
    gras: bool = False
    italique: bool = False


class IntertitreDeTexte(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["intertitre"] = "intertitre"
    texte: str


class ParagrapheDeTexte(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["paragraphe"] = "paragraphe"
    segments: list[Segment]


class ListeDeTexte(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["liste"] = "liste"
    points: list[list[Segment]]


BlocDeTexte = Annotated[
    IntertitreDeTexte | ParagrapheDeTexte | ListeDeTexte, Field(discriminator="type")
]

_TITRE = re.compile(r"^#{1,6}\s+(.*)$")
_POINT = re.compile(r"^(?:[-•]|\d+[.)])\s+(.*)$")
# Le gras d'abord : `**x**` ne doit pas se lire comme deux italiques vides.
_EMPHASE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_")


def _segments(ligne: str) -> list[Segment]:
    segments: list[Segment] = []
    position = 0
    for m in _EMPHASE.finditer(ligne):
        if m.start() > position:
            segments.append(Segment(texte=ligne[position : m.start()]))
        if m.group(1) is not None:
            segments.append(Segment(texte=m.group(1), gras=True))
        else:
            segments.append(Segment(texte=m.group(2) or m.group(3), italique=True))
        position = m.end()
    if position < len(ligne):
        segments.append(Segment(texte=ligne[position:]))
    return segments


def en_blocs(texte: str) -> list[BlocDeTexte]:
    """Les blocs du texte, dans l'ordre. Un texte vide n'en rend aucun."""
    blocs: list[BlocDeTexte] = []
    paragraphe: list[str] = []
    points: list[list[Segment]] = []

    def clore() -> None:
        if paragraphe:
            blocs.append(ParagrapheDeTexte(segments=_segments(" ".join(paragraphe))))
            paragraphe.clear()
        if points:
            blocs.append(ListeDeTexte(points=list(points)))
            points.clear()

    for brute in texte.replace("\r\n", "\n").split("\n"):
        ligne = brute.strip()
        if not ligne:
            clore()
            continue
        titre = _TITRE.match(ligne)
        point = _POINT.match(ligne)
        if titre and titre.group(1).strip():
            clore()
            blocs.append(IntertitreDeTexte(texte=titre.group(1).strip().strip("*").strip()))
        elif point and point.group(1).strip():
            if paragraphe:
                clore()
            points.append(_segments(point.group(1).strip()))
        else:
            if points:
                clore()
            paragraphe.append(ligne)
    clore()
    return blocs
