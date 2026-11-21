"""La note d'honoraires d'une échéance : ce que l'adhérent doit, et comment le régler.

─────────────────────────────────────────────────────────────────────────────────
18 OCTOBRE 2026, POINTS 19 ET 20

Le système émettait la proforma et le reçu ; entre les deux, rien ne disait par
écrit à l'adhérent ce qu'il devait pour une période. La note d'honoraires comble ce
vide : la même maison que la proforma (`entete_de_document`, `pied_de_document`), une
ligne par période, le net à payer en chiffres et en lettres, et les deux façons de
régler. Réglée, elle le dit, et renvoie au reçu.

⚠️ AUCUNE MENTION FISCALE INVENTÉE. Le NIU, le RCCM et le régime de TVA du cabinet ne
sont pas connus de la plateforme : la note n'en porte aucun plutôt qu'un faux. Le
montant est celui du contrat, tel qu'il est convenu, sans TVA ajoutée ni retirée.

⚠️ RIEN D'INTERNE : ni le nom de qui a fixé le contrat, ni celui de qui a encaissé.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
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
from app.contextes.souscription.domaine.honoraires import (
    EcheanceDHonoraires,
    EtatEcheanceDHonoraires,
)
from app.partage.formats import MOIS, en_toutes_lettres

__all__ = ["numero_de_la_note", "rendre_la_note"]

_LARGEUR, _HAUTEUR = A4


def numero_de_la_note(niu: str, periode: date) -> str:
    """Stable : la même échéance porte toujours le même numéro, quel que soit le jour."""
    return f"NH-{niu[-6:]}-{periode:%Y%m}"


def _periode(echeance: EcheanceDHonoraires) -> str:
    if (echeance.fin - echeance.debut).days <= 31:
        return f"{MOIS[echeance.debut.month - 1]} {echeance.debut.year}"
    return f"du {echeance.debut:%d/%m/%Y} au {echeance.fin:%d/%m/%Y}"


def rendre_la_note(
    echeance: EcheanceDHonoraires,
    *,
    adherent: str,
    emise_le: date,
) -> bytes:
    tampon = BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    numero = numero_de_la_note(echeance.niu, echeance.debut)
    page.setTitle(f"Note d'honoraires {numero}")
    page.setAuthor("CGA Broad Range Consulting Group")

    y = entete_de_document(page)
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 17)
    page.drawString(MARGE, y, "Note d'honoraires")
    y -= 7 * mm
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 9)
    page.drawString(MARGE, y, "Suivi comptable et fiscal par le centre de gestion agréé")
    y -= 10 * mm

    milieu = _LARGEUR / 2 + 10 * mm
    page.setFont("Helvetica-Bold", 7)
    page.drawString(MARGE, y, "ÉTABLIE POUR")
    page.drawString(milieu, y, "RÉFÉRENCE")
    page.setFillColor(black)
    page.setFont("Helvetica-Bold", 10)
    page.drawString(MARGE, y - 5 * mm, adherent[:60])
    page.setFont("Helvetica", 9)
    page.drawString(MARGE, y - 9.5 * mm, f"NIU {echeance.niu}")
    page.drawString(milieu, y - 5 * mm, f"N° {numero}")
    page.setFillColor(GRIS)
    page.drawString(milieu, y - 9.5 * mm, f"Émise le {emise_le:%d/%m/%Y}")
    page.drawString(milieu, y - 14 * mm, f"Exigible le {echeance.exigible_le:%d/%m/%Y}")
    y -= 26 * mm

    # Le tableau : une ligne, sous ses intitulés de colonne.
    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(MARGE, y, "DÉSIGNATION")
    page.drawString(MARGE + 105 * mm, y, "PÉRIODE")
    page.drawRightString(_LARGEUR - MARGE, y, "MONTANT")
    page.setStrokeColor(FILET)
    page.setLineWidth(0.6)
    page.line(MARGE, y - 2 * mm, _LARGEUR - MARGE, y - 2 * mm)
    y -= 7.5 * mm
    page.setFillColor(black)
    page.setFont("Helvetica", 9.5)
    page.drawString(MARGE, y, "Honoraires du centre de gestion agréé")
    page.drawString(MARGE + 105 * mm, y, _periode(echeance))
    page.drawRightString(_LARGEUR - MARGE, y, f"{montant_imprime(echeance.montant)} FCFA")
    y -= 4 * mm
    page.line(MARGE, y, _LARGEUR - MARGE, y)
    page.setStrokeColor(black)
    y -= 10 * mm

    # Le net à payer, à droite, dans son cartouche.
    gauche = _LARGEUR - MARGE - 80 * mm
    page.setFillColor(FOND)
    page.roundRect(gauche, y - 3.5 * mm, 80 * mm, 11 * mm, 2 * mm, stroke=0, fill=1)
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 8.5)
    page.drawString(gauche + 5 * mm, y + 0.6 * mm, "NET À PAYER")
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 13)
    page.drawRightString(
        _LARGEUR - MARGE - 5 * mm, y + 0.4 * mm, f"{montant_imprime(echeance.montant)} FCFA"
    )
    page.setFillColor(black)
    y -= 13 * mm
    page.setFont("Helvetica-Oblique", 9)
    page.setFillColor(GRIS)
    page.drawString(
        MARGE,
        y,
        f"Arrêtée la présente note à la somme de "
        f"{en_toutes_lettres(echeance.montant).replace('-', ' ')} francs CFA.",
    )
    y -= 14 * mm

    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(MARGE, y, "RÈGLEMENT")
    page.setStrokeColor(FILET)
    page.line(MARGE, y - 2 * mm, _LARGEUR - MARGE, y - 2 * mm)
    page.setStrokeColor(black)
    y -= 7 * mm
    page.setFillColor(black)
    page.setFont("Helvetica", 9.5)
    if echeance.etat is EtatEcheanceDHonoraires.REGLEE and echeance.reglement is not None:
        r = echeance.reglement
        page.setFillColor(INDIGO)
        page.setFont("Helvetica-Bold", 10)
        page.drawString(
            MARGE, y, f"Réglée le {r.constate_le:%d/%m/%Y}, référence {r.reference_externe}."
        )
        page.setFillColor(GRIS)
        page.setFont("Helvetica", 9)
        page.drawString(
            MARGE, y - 5 * mm, "Le reçu correspondant se télécharge depuis votre espace."
        )
    else:
        for ligne in (
            "Par mobile money, depuis votre espace adhérent : la note passe à « Réglée »"
            " toute seule.",
            "Au guichet du cabinet, en espèces ou par virement : la responsable de caisse"
            " vous remet un reçu.",
        ):
            page.drawString(MARGE, y, f"•  {ligne}")
            y -= 5.2 * mm

    pied_de_document(page)
    page.showPage()
    page.save()
    return tampon.getvalue()
