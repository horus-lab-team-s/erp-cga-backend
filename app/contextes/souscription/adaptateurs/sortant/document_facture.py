"""La facture définitive des honoraires, rendue d'une facture émise (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
La même maison que la note d'honoraires (`entete_de_document`, `pied_de_document`),
avec ce que la note ne porte pas : le NIU, le RCCM et le régime de TVA du cabinet, la
ventilation HT, TVA, TTC, et la date du règlement.

⚠️ RENDUE DE LA FACTURE ÉMISE, JAMAIS DE L'ÉTAT DU JOUR. Tout ce qu'elle imprime vient
de `FactureDHonoraires`, figée à l'émission : la télécharger une seconde fois, après une
correction de l'identité du cabinet, rend le même document.

⚠️ RIEN D'INTERNE (point 21) : ni qui a encaissé, ni qui a émis.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.colors import black
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
    montant_imprime,
    pied_de_document,
)
from app.contextes.souscription.domaine.facturation import (
    FactureDHonoraires,
    RegimeTvaDuCabinet,
)
from app.partage.formats import MOIS, en_toutes_lettres

__all__ = ["rendre_la_facture"]

_LARGEUR, _HAUTEUR = A4


def _periode(facture: FactureDHonoraires) -> str:
    if (facture.periode_fin - facture.periode).days <= 31:
        return f"{MOIS[facture.periode.month - 1]} {facture.periode.year}"
    return f"du {facture.periode:%d/%m/%Y} au {facture.periode_fin:%d/%m/%Y}"


def _taux(facture: FactureDHonoraires) -> str:
    taux = facture.identite.taux_tva
    assert taux is not None
    return f"{taux.normalize():f}".replace(".", ",")


def _mention_tva(facture: FactureDHonoraires) -> str:
    identite = facture.identite
    if identite.regime_tva is RegimeTvaDuCabinet.ASSUJETTI:
        return f"Assujetti à la TVA, taux de {_taux(facture)} %"
    if identite.regime_tva is RegimeTvaDuCabinet.EXONERE:
        return f"Exonéré de TVA : {identite.fondement_exoneration}"
    return "Non assujetti à la TVA"


def rendre_la_facture(facture: FactureDHonoraires) -> bytes:
    tampon = BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    page.setTitle(f"Facture {facture.numero}")
    page.setAuthor("CGA Broad Range Consulting Group")
    identite = facture.identite

    y = entete_de_document(page)
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 17)
    page.drawString(MARGE, y, "Facture")
    page.setFont("Helvetica-Bold", 11)
    page.drawRightString(_LARGEUR - MARGE, y, f"N° {facture.numero}")
    y -= 7 * mm
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 8.5)
    page.drawString(
        MARGE, y, f"NIU {identite.niu}  ·  RCCM {identite.rccm}  ·  {_mention_tva(facture)}"
    )
    y -= 11 * mm

    milieu = _LARGEUR / 2 + 10 * mm
    page.setFont("Helvetica-Bold", 7)
    page.drawString(MARGE, y, "FACTURÉE À")
    page.drawString(milieu, y, "DATES")
    page.setFillColor(black)
    page.setFont("Helvetica-Bold", 10)
    page.drawString(MARGE, y - 5 * mm, facture.adherent[:60])
    page.setFont("Helvetica", 9)
    page.drawString(MARGE, y - 9.5 * mm, f"NIU {facture.niu}")
    page.drawString(milieu, y - 5 * mm, f"Émise le {facture.emise_le:%d/%m/%Y}")
    page.setFillColor(GRIS)
    page.drawString(milieu, y - 9.5 * mm, f"Réglée le {facture.reglee_le:%d/%m/%Y}")
    y -= 24 * mm

    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(MARGE, y, "DÉSIGNATION")
    page.drawString(MARGE + 105 * mm, y, "PÉRIODE")
    page.drawRightString(_LARGEUR - MARGE, y, "MONTANT HT")
    page.setStrokeColor(FILET)
    page.setLineWidth(0.6)
    page.line(MARGE, y - 2 * mm, _LARGEUR - MARGE, y - 2 * mm)
    y -= 7.5 * mm
    page.setFillColor(black)
    page.setFont("Helvetica", 9.5)
    page.drawString(MARGE, y, "Honoraires du centre de gestion agréé")
    page.drawString(MARGE + 105 * mm, y, _periode(facture))
    page.drawRightString(_LARGEUR - MARGE, y, f"{montant_imprime(facture.montant_ht)} FCFA")
    y -= 4 * mm
    page.line(MARGE, y, _LARGEUR - MARGE, y)
    page.setStrokeColor(black)
    y -= 9 * mm

    gauche = _LARGEUR - MARGE - 80 * mm
    lignes = [("Total HT", facture.montant_ht)]
    if identite.regime_tva is RegimeTvaDuCabinet.ASSUJETTI:
        lignes.append((f"TVA {_taux(facture)} %", facture.tva))
    page.setFont("Helvetica", 9.5)
    for libelle, montant in lignes:
        page.setFillColor(GRIS)
        page.drawString(gauche + 5 * mm, y, libelle)
        page.setFillColor(black)
        page.drawRightString(_LARGEUR - MARGE - 5 * mm, y, f"{montant_imprime(montant)} FCFA")
        y -= 6 * mm
    y -= 2 * mm
    page.setFillColor(FOND)
    page.roundRect(gauche, y - 3.5 * mm, 80 * mm, 11 * mm, 2 * mm, stroke=0, fill=1)
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 8.5)
    page.drawString(gauche + 5 * mm, y + 0.6 * mm, "TOTAL TTC")
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 13)
    page.drawRightString(
        _LARGEUR - MARGE - 5 * mm, y + 0.4 * mm, f"{montant_imprime(facture.montant_ttc)} FCFA"
    )
    y -= 13 * mm
    page.setFont("Helvetica-Oblique", 9)
    page.setFillColor(GRIS)
    page.drawString(
        MARGE,
        y,
        "Arrêtée la présente facture à la somme de "
        f"{en_toutes_lettres(facture.montant_ttc).replace('-', ' ')} francs CFA toutes taxes "
        "comprises.",
    )
    y -= 12 * mm
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 9.5)
    page.drawString(
        MARGE,
        y,
        f"Acquittée le {facture.reglee_le:%d/%m/%Y}, référence {facture.reference_du_reglement}.",
    )

    pied_de_document(page)
    page.showPage()
    page.save()
    return tampon.getvalue()
