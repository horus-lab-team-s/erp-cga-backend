"""Le papier à en-tête du cabinet, pour tous les documents (19 octobre 2026, points 19 et 20).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI ICI

La proforma, la note d'honoraires, la facture et le reçu de dépôt portaient le logo et la
maison en tête ; le bulletin de paie, les livres légaux, le livrable d'une mission et la
DSF, non : une page blanche, un titre en gras, et rien qui dise de quel cabinet elle venait.
Le cabinet l'a relevé : à côté d'une facture soignée, un bulletin nu se lit comme un
brouillon.

L'en-tête vivait dans le contexte de la souscription ; les autres contextes ne peuvent pas
l'y prendre sans franchir le graphe des dépendances. Il vit donc ici, à côté du logo, et
chaque document l'appelle depuis sa propre fonction de page.

⚠️ L'ABSENCE DU LOGO NE FAIT RIEN ÉCHOUER : la raison sociale tient sa place, et le
document s'émet quand même (même règle que la proforma).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from reportlab.lib.colors import HexColor
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app.partage.marque import LOGO

__all__ = ["HAUTEUR_DU_BANDEAU", "INDIGO", "bandeau_de_la_maison"]

INDIGO = HexColor("#2E1B4D")
_GRIS = HexColor("#6B6480")
_FILET = HexColor("#C9C4D6")

#: Ce que le bandeau occupe en haut de page : à ajouter à la marge haute du document.
HAUTEUR_DU_BANDEAU = 15 * mm


def bandeau_de_la_maison(toile: Canvas, *, largeur: float, hauteur: float, marge: float) -> float:
    """Le logo et la maison à gauche, le site à droite, un filet fin dessous.

    Rend l'ordonnée sous le filet : le document y place son propre titre.
    """
    toile.saveState()
    haut = hauteur - 8 * mm
    gauche = marge
    if LOGO.is_file():
        hauteur_logo = 9 * mm
        toile.drawImage(
            ImageReader(str(LOGO)),
            marge,
            haut - hauteur_logo,
            width=hauteur_logo * 288 / 162,
            height=hauteur_logo,
            preserveAspectRatio=True,
            mask=None,
        )
        gauche = marge + hauteur_logo * 288 / 162 + 4 * mm
    toile.setFillColor(INDIGO)
    toile.setFont("Helvetica-Bold", 9.5)
    toile.drawString(gauche, haut - 3.6 * mm, "CGA BROAD RANGE CONSULTING")
    toile.setFillColor(_GRIS)
    toile.setFont("Helvetica", 7)
    toile.drawString(
        gauche, haut - 7.4 * mm, "Centre de gestion agréé  ·  Comptabilité, fiscalité, juridique"
    )
    toile.drawRightString(largeur - marge, haut - 3.6 * mm, "www.cga-brcgroup.com")
    bas = haut - 11 * mm
    toile.setStrokeColor(_FILET)
    toile.setLineWidth(0.6)
    toile.line(marge, bas, largeur - marge, bas)
    toile.restoreState()
    return bas
