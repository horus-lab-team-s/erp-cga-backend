"""Le reçu de dépôt d'une déclaration, tel que l'adhérent le garde (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DIT : « dans Documents, je ne vois pas la preview des documents, je
vois seulement les références ». L'adhérent avait un numéro d'accusé et une date ; il
a désormais une pièce : le relevé de l'accusé que le cabinet a consigné à son dossier,
à la charte du cabinet, qui se lit, s'imprime et se transmet.

⚠️ CE N'EST PAS L'ACCUSÉ DE L'ADMINISTRATION, et le document le dit. Il relève ce que le
cabinet a consigné : le numéro que le guichet a rendu, la date opposable, le montant
repris. Quand un justificatif est archivé (la quittance, la capture), le reçu le dit ;
sinon, il dit aussi que le numéro repose sur la saisie du cabinet.

⚠️ RIEN D'INTERNE (point 21) : ni qui a déposé, ni qui a saisi.

La palette est celle des autres documents (`document_proforma.py`, `document_dsf.py`) ;
chaque contexte rend ses propres documents, `app.partage` ne prête que le logo.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.partage.formats import ESPACE_FINE
from app.partage.formats import montant as _montant_ecran
from app.partage.horloge import a_douala
from app.partage.marque import LOGO

__all__ = ["rendre_le_recu_de_depot"]

_LARGEUR, _HAUTEUR = A4
_MARGE = 20 * mm
_INDIGO = HexColor("#2E1B4D")
_GRIS = HexColor("#5B556B")
_FILET = HexColor("#E2DDEA")
_FOND = HexColor("#F6F4FB")
_VERT = HexColor("#1E7A4C")


def _montant(valeur: Decimal) -> str:
    # ReportLab imprime en Latin-1 : l'espace fine de l'écran devient une espace simple.
    return _montant_ecran(valeur).replace(ESPACE_FINE, " ")


def _entete(page: canvas.Canvas) -> float:
    haut = _HAUTEUR - 16 * mm
    gauche = _MARGE
    if LOGO.is_file():
        hauteur = 13 * mm
        page.drawImage(
            ImageReader(str(LOGO)),
            _MARGE,
            haut - hauteur,
            width=hauteur * 288 / 162,
            height=hauteur,
            preserveAspectRatio=True,
            mask=None,
        )
        gauche = _MARGE + hauteur * 288 / 162 + 5 * mm
    page.setFillColor(_INDIGO)
    page.setFont("Helvetica-Bold", 11)
    page.drawString(gauche, haut - 5 * mm, "CGA BROAD RANGE CONSULTING")
    page.setFillColor(_GRIS)
    page.setFont("Helvetica", 7.5)
    page.drawString(
        gauche, haut - 9.5 * mm, "Centre de gestion agréé  ·  Comptabilité, fiscalité, juridique"
    )
    page.drawRightString(_LARGEUR - _MARGE, haut - 5 * mm, "www.cga-brcgroup.com")
    page.setStrokeColor(_FILET)
    page.setLineWidth(0.8)
    page.line(_MARGE, haut - 16 * mm, _LARGEUR - _MARGE, haut - 16 * mm)
    return haut - 26 * mm


def _pied(page: canvas.Canvas) -> None:
    page.setStrokeColor(_FILET)
    page.line(_MARGE, 22 * mm, _LARGEUR - _MARGE, 22 * mm)
    page.setFillColor(_GRIS)
    page.setFont("Helvetica", 7)
    page.drawCentredString(
        _LARGEUR / 2, 16 * mm, "CGA Broad Range Consulting Group · Douala Akwa, B.P. 7761"
    )
    page.drawCentredString(
        _LARGEUR / 2, 12 * mm, "Agrément n° 00000048/MINFI/DGI du 28 janvier 2020"
    )


def rendre_le_recu_de_depot(
    *,
    denomination: str,
    niu: str,
    titre: str,
    periode: str,
    guichet: str,
    numero: str,
    depose_le: datetime,
    montant: Decimal | None,
    verifiable: bool,
) -> bytes:
    tampon = BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    page.setTitle(f"Reçu de dépôt {numero}")
    page.setAuthor("CGA Broad Range Consulting Group")

    y = _entete(page)
    page.setFillColor(_INDIGO)
    page.setFont("Helvetica-Bold", 17)
    page.drawString(_MARGE, y, "Reçu de dépôt")
    y -= 7 * mm
    page.setFillColor(_GRIS)
    page.setFont("Helvetica", 9.5)
    page.drawString(_MARGE, y, f"{titre} · {periode}")
    y -= 14 * mm

    # Le cartouche : l'accusé, en grand, ce qu'on cite à l'administration.
    page.setFillColor(_FOND)
    page.roundRect(_MARGE, y - 16 * mm, _LARGEUR - 2 * _MARGE, 22 * mm, 3 * mm, stroke=0, fill=1)
    page.setFillColor(_GRIS)
    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(_MARGE + 6 * mm, y, "N° D'ACCUSÉ DE RÉCEPTION")
    page.setFillColor(_INDIGO)
    page.setFont("Helvetica-Bold", 16)
    page.drawString(_MARGE + 6 * mm, y - 9 * mm, numero[:48])
    page.setFillColor(_VERT)
    page.setFont("Helvetica-Bold", 9)
    page.drawRightString(_LARGEUR - _MARGE - 6 * mm, y - 9 * mm, "DÉPOSÉE")
    y -= 30 * mm

    lignes = [
        ("Entreprise", f"{denomination} · NIU {niu}"),
        ("Déclaration", f"{titre}, {periode}"),
        ("Guichet", guichet),
        ("Déposée le", f"{a_douala(depose_le):%d/%m/%Y à %H:%M}"),
    ]
    if montant is not None:
        lignes.append(("Montant repris par le guichet", f"{_montant(montant)} FCFA"))
    for libelle, valeur in lignes:
        page.setFillColor(_GRIS)
        page.setFont("Helvetica", 8.5)
        page.drawString(_MARGE, y, libelle)
        page.setFillColor(black)
        page.setFont("Helvetica-Bold", 10)
        page.drawString(_MARGE + 55 * mm, y, valeur[:70])
        page.setStrokeColor(_FILET)
        page.line(_MARGE, y - 3.5 * mm, _LARGEUR - _MARGE, y - 3.5 * mm)
        y -= 10 * mm

    y -= 6 * mm
    page.setFillColor(_GRIS)
    page.setFont("Helvetica-Oblique", 8.5)
    phrase = (
        "Relevé de l'accusé consigné par le cabinet à votre dossier. Il ne remplace pas l'accusé "
        "délivré par l'administration"
        + (
            ", dont le justificatif est archivé au dossier."
            if verifiable
            else " ; le numéro repose sur la saisie du cabinet, aucun justificatif n'est "
            "encore archivé."
        )
    )
    mots, ligne = phrase.split(), ""
    for mot in mots:
        essai = f"{ligne} {mot}".strip()
        if page.stringWidth(essai, "Helvetica-Oblique", 8.5) > _LARGEUR - 2 * _MARGE:
            page.drawString(_MARGE, y, ligne)
            y -= 4.5 * mm
            ligne = mot
        else:
            ligne = essai
    page.drawString(_MARGE, y, ligne)

    _pied(page)
    page.showPage()
    page.save()
    return tampon.getvalue()
