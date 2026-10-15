"""Le reçu d'honoraires acquittés (15 octobre 2026) : ce que l'adhérent classe et produit.

Même en-tête et même pied que la proforma (`document_proforma.py`) : un client qui reçoit
l'une puis l'autre doit reconnaître la maison. ⚠️ Le montant vient du règlement constaté,
jamais d'une saisie ; il est écrit en chiffres ET en lettres, protection contre un chiffre
altéré, comme sur la proforma.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.contextes.souscription.adaptateurs.sortant.document_proforma import (
    _GRIS,
    _INDIGO,
    _MARGE,
    _entete,
    _pied,
    _sous,
)
from app.contextes.souscription.domaine.honoraires import CanalDeReglement, ReglementDHonoraires
from app.partage.formats import MOIS, en_toutes_lettres

__all__ = ["rendre_le_recu", "rendre_un_recu"]

_CANAUX = {
    CanalDeReglement.GUICHET: "au guichet du cabinet",
    CanalDeReglement.MOBILE_MONEY: "par mobile money",
}


def rendre_le_recu(
    reglement: ReglementDHonoraires, *, adherent: str, periode: str, encaisse_par: str
) -> bytes:
    return rendre_un_recu(
        titre="REÇU D'HONORAIRES · ACQUITTÉ",
        numero=reglement.identifiant,
        le=reglement.constate_le,
        avant=[
            ("Adhérent", adherent),
            ("NIU", reglement.niu),
            ("Objet", f"Honoraires du centre de gestion agréé, {periode}"),
        ],
        montant=reglement.montant,
        apres=[
            (
                "Réglé",
                f"{_CANAUX.get(reglement.canal, reglement.canal.value)}, référence "
                f"{reglement.reference_externe}",
            ),
            ("Encaissé par", encaisse_par),
        ],
    )


def rendre_un_recu(
    *,
    titre: str,
    numero: str,
    le: datetime,
    avant: list[tuple[str, str]],
    montant: Decimal,
    apres: list[tuple[str, str]],
) -> bytes:
    """Un reçu acquitté, à l'en-tête de la maison. `le` est horodaté en UTC.

    15 octobre 2026 : servi aussi au reçu de la cliente d'une création. Le montant est
    écrit en chiffres et en lettres ; l'appelant ne le formate pas.
    """
    tampon = BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    page.setTitle(f"{titre.split(' · ')[0].capitalize()} {numero}")
    page.setAuthor("CGA Broad Range Consulting Group")
    y = _entete(page)
    page.setFillColor(_INDIGO)
    page.setFont("Helvetica-Bold", 15)
    page.drawString(_MARGE, y, titre)
    local = le + timedelta(hours=1)  # horodaté en UTC, lu à Douala
    page.setFont("Helvetica", 9)
    page.setFillColor(_GRIS)
    page.drawString(
        _MARGE,
        y - 6 * mm,
        f"N° {numero} · le {local.day} {MOIS[local.month - 1]} {local.year}",
    )
    lignes = [*avant, ("Montant réglé", f"{_sous(montant)} FCFA"), *apres]
    y -= 20 * mm
    for libelle, valeur in lignes:
        page.setFillColor(_GRIS)
        page.setFont("Helvetica", 9.5)
        page.drawString(_MARGE, y, libelle)
        page.setFillColor(black)
        page.setFont("Helvetica-Bold" if libelle == "Montant réglé" else "Helvetica", 10.5)
        page.drawString(_MARGE + 40 * mm, y, valeur[:95])
        y -= 8 * mm
    y -= 4 * mm
    page.setFont("Helvetica-Oblique", 9.5)
    page.drawString(
        _MARGE,
        y,
        f"Arrêté le présent reçu à la somme de {en_toutes_lettres(montant)} francs CFA.",
    )
    _pied(page)
    page.showPage()
    page.save()
    return tampon.getvalue()
