"""L'attestation d'adhésion au Centre de gestion agréé (11 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE DOCUMENT

L'adhésion à un CGA ouvre des avantages fiscaux (CGI art. 119 (1)) que l'adhérent doit
pouvoir faire valoir : auprès de son centre des impôts, d'un fournisseur qui applique le
précompte réduit, d'une banque. Le cabinet n'avait rien à lui remettre.

⚠️ CE QU'ELLE NE REMPLACE PAS. L'arrêté du MINFI du 15 janvier 2020 (art. 2) fait de
l'appartenance au fichier des adhérents publié par la DGI LA preuve de l'adhésion pour le
précompte réduit. L'attestation le dit en toutes lettres : elle constate ce que le centre
sait, au jour où elle est délivrée, et renvoie à ce fichier.

CE QU'ELLE PORTE : le centre (en-tête et agrément, depuis le référentiel), l'adhérent
(dénomination, NIU, RCCM, régime), le numéro d'adhésion et sa date d'effet, la date de
délivrance, une référence unique, et l'empreinte qui permet de la vérifier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.contextes.souscription.adaptateurs.sortant.document_proforma import (
    FILET,
    FOND,
    GRIS,
    INDIGO,
    MARGE,
    entete_de_document,
    pied_de_document,
)
from app.contextes.souscription.adaptateurs.sortant.identite_des_documents import (
    identite_des_documents,
)

__all__ = ["AttestationDAdhesion", "empreinte_de_l_attestation", "rendre_l_attestation"]

_LARGEUR, _HAUTEUR = A4


@dataclass(frozen=True)
class AttestationDAdhesion:
    denomination: str
    niu: str
    rccm: str | None
    regime: str
    numero_d_adhesion: str
    adherent_depuis: date
    delivree_le: datetime
    reference: str


def empreinte_de_l_attestation(a: AttestationDAdhesion) -> str:
    """Ce qui rend l'attestation vérifiable : le cabinet la recalcule sur ses données."""
    contenu = "|".join(
        [
            a.reference,
            a.niu,
            a.numero_d_adhesion,
            a.adherent_depuis.isoformat(),
            a.delivree_le.isoformat(),
        ]
    )
    return hashlib.sha256(contenu.encode()).hexdigest()[:16].upper()


def _date_longue(jour: date) -> str:
    mois = (
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
    return f"{jour.day} {mois[jour.month - 1]} {jour.year}"


def _paragraphe(
    page: canvas.Canvas, texte: str, y: float, *, taille: float = 10.5, gras: bool = False
) -> float:
    """Un paragraphe justifié à gauche, coupé à la largeur utile."""
    page.setFont("Helvetica-Bold" if gras else "Helvetica", taille)
    largeur = _LARGEUR - 2 * MARGE
    ligne = ""
    for mot in texte.split():
        essai = f"{ligne} {mot}".strip()
        if page.stringWidth(essai, "Helvetica-Bold" if gras else "Helvetica", taille) > largeur:
            page.drawString(MARGE, y, ligne)
            y -= taille * 1.55
            ligne = mot
        else:
            ligne = essai
    if ligne:
        page.drawString(MARGE, y, ligne)
        y -= taille * 1.55
    return y


def rendre_l_attestation(a: AttestationDAdhesion) -> bytes:
    tampon = BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    page.setTitle(f"Attestation d'adhésion {a.reference}")
    page.setAuthor("CGA Broad Range Consulting Group")
    identite = identite_des_documents()
    agrement = next((p for p in identite.pied if "grément" in p), "")

    y = entete_de_document(page)
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 17)
    page.drawCentredString(_LARGEUR / 2, y, "ATTESTATION D'ADHÉSION")
    y -= 6 * mm
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 9)
    page.drawCentredString(_LARGEUR / 2, y, f"Référence {a.reference}")
    y -= 14 * mm

    page.setFillColor("#1f1b2b")
    centre = identite.raison_sociale + (f", {agrement}" if agrement else "")
    y = _paragraphe(page, f"Le Centre de gestion agréé {centre}, atteste que l'entreprise :", y)
    y -= 3 * mm

    # Le cadre de l'adhérent.
    lignes = [
        ("Dénomination", a.denomination),
        ("Numéro d'identifiant unique (NIU)", a.niu),
        ("Registre du commerce (RCCM)", a.rccm or "non communiqué"),
        ("Régime fiscal", a.regime),
        ("Numéro d'adhésion", a.numero_d_adhesion),
        ("Adhérente depuis le", _date_longue(a.adherent_depuis)),
    ]
    hauteur = len(lignes) * 7.5 * mm + 6 * mm
    page.setFillColor(FOND)
    page.setStrokeColor(FILET)
    page.roundRect(MARGE, y - hauteur, _LARGEUR - 2 * MARGE, hauteur, 3 * mm, fill=1, stroke=1)
    ly = y - 8 * mm
    for libelle, valeur in lignes:
        page.setFillColor(GRIS)
        page.setFont("Helvetica", 9.5)
        page.drawString(MARGE + 6 * mm, ly, libelle)
        page.setFillColor(INDIGO)
        page.setFont("Helvetica-Bold", 10.5)
        page.drawString(MARGE + 72 * mm, ly, valeur)
        ly -= 7.5 * mm
    y -= hauteur + 9 * mm

    page.setFillColor("#1f1b2b")
    y = _paragraphe(
        page,
        f"est adhérente du Centre à la date du {_date_longue(a.delivree_le.date())}, et que "
        "son adhésion est en cours à cette date.",
        y,
    )
    y -= 3 * mm
    y = _paragraphe(
        page,
        "L'adhésion ouvre droit aux avantages prévus par l'article 119 du Code général des "
        "impôts, sous réserve que l'adhérent souscrive sa déclaration des résultats dans les "
        "délais (article 119 (2)).",
        y,
        taille=9.5,
    )
    y -= 2 * mm
    y = _paragraphe(
        page,
        "Pour l'application du précompte réduit sur les achats, la preuve de l'adhésion est "
        "apportée par l'appartenance au fichier des adhérents publié sur le site de la Direction "
        "générale des impôts (arrêté du MINFI du 15 janvier 2020, article 2).",
        y,
        taille=9.5,
    )
    y -= 4 * mm
    y = _paragraphe(
        page,
        "En foi de quoi la présente attestation est délivrée pour servir et "
        "valoir ce que de droit.",
        y,
    )

    y -= 10 * mm
    page.setFont("Helvetica", 10)
    fait = f"Fait à Douala, le {_date_longue(a.delivree_le.date())}"
    page.drawRightString(_LARGEUR - MARGE, y, fait)
    y -= 7 * mm
    page.setFont("Helvetica-Bold", 10)
    page.drawRightString(_LARGEUR - MARGE, y, "Pour le Centre, la Direction")

    # La vérification : la référence et l'empreinte, recalculables par le cabinet.
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 7.5)
    page.drawString(
        MARGE,
        34 * mm,
        f"Vérification : référence {a.reference} · empreinte "
        f"{empreinte_de_l_attestation(a)} · à confirmer auprès du Centre.",
    )
    pied_de_document(page)
    page.showPage()
    page.save()
    return tampon.getvalue()
