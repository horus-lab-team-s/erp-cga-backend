"""Le bulletin de paie imprimable (15 octobre 2026).

⚠️ AUCUN MONTANT N'EST CALCULÉ ICI : tout vient de `calculer_bulletin`, comme à
l'écran. Un bulletin PDF qui recompterait finirait par donner au salarié un autre
net que celui que l'écran et la DIPE déclarent.

⚠️ TANT QU'UNE LIGNE REPOSE SUR UNE VALEUR « À VALIDER » AU RÉFÉRENTIEL, le bulletin
le dit en tête : il n'est pas opposable avant la validation du fiscaliste.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.contextes.social.domaine.entites import Bulletin
from app.partage.formats import ESPACE_FINE, MOIS
from app.partage.formats import montant as _montant_ecran

__all__ = ["rendre_le_bulletin"]

_INDIGO = HexColor("#2E1B4D")
_MAGENTA = HexColor("#8C2D86")
_GRIS = HexColor("#5B556B")
_FOND = HexColor("#F6F4FB")
_TRAIT = HexColor("#E2DDEA")

_TEXTE = ParagraphStyle("t", fontName="Helvetica", fontSize=9, leading=12)
_PETIT = ParagraphStyle("p", parent=_TEXTE, fontSize=7.5, leading=10, textColor=_GRIS)
_ALERTE = ParagraphStyle("a", parent=_TEXTE, textColor=_MAGENTA, fontName="Helvetica-Bold")


def _m(valeur) -> str:
    return _montant_ecran(Decimal(str(valeur))).replace(ESPACE_FINE, " ")


def _p(texte: str, style=_TEXTE) -> Paragraph:
    return Paragraph(texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _lignes(*morceaux: str | None) -> Paragraph:
    """Plusieurs lignes dans une case : chaque morceau échappé, puis joints par un saut.
    ⚠️ Échapper le tout imprimait « <br/> » en toutes lettres (vu au rendu)."""
    echappes = [
        m.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for m in morceaux if m
    ]
    return Paragraph("<br/>".join(echappes), _TEXTE)


def _taux(valeur) -> str:
    """« 4,2 % » : la virgule décimale, comme sur tout document du cabinet."""
    return f"{str(valeur).replace('.', ',')} %"


def _table(lignes, largeurs, *, totaux=(), montants=()):
    t = Table(lignes, colWidths=largeurs)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), _INDIGO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _INDIGO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for c in montants:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for r in totaux:
        style += [
            ("FONT", (0, r), (-1, r), "Helvetica-Bold", 9.5),
            ("BACKGROUND", (0, r), (-1, r), _FOND),
            ("LINEABOVE", (0, r), (-1, r), 0.5, _TRAIT),
        ]
    t.setStyle(TableStyle(style))
    return t


def rendre_le_bulletin(
    bulletin: Bulletin,
    *,
    employeur: str,
    niu: str,
    salarie: str,
    matricule_cnps: str | None,
    poste: str | None,
) -> bytes:
    tampon = BytesIO()
    marge = 15 * mm
    periode = f"{MOIS[bulletin.periode.mois - 1]} {bulletin.periode.annee}"
    document = SimpleDocTemplate(
        tampon,
        pagesize=A4,
        leftMargin=marge,
        rightMargin=marge,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=f"Bulletin de paie · {salarie} · {periode}",
        author="CGA Broad Range Consulting Group",
        invariant=1,
    )
    largeur = A4[0] - 2 * marge
    contenu: list = [
        _p(
            f"BULLETIN DE PAIE · {periode.upper()}",
            ParagraphStyle(
                "h", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=_INDIGO
            ),
        ),
        Spacer(0, 3 * mm),
        _table(
            [
                ["Employeur", "Salarié"],
                [
                    _lignes(employeur, f"NIU {niu}"),
                    _lignes(
                        salarie,
                        f"Matricule {bulletin.salarie}"
                        + (f" · CNPS {matricule_cnps}" if matricule_cnps else ""),
                        poste,
                    ),
                ],
            ],
            [largeur / 2, largeur / 2],
        ),
        Spacer(0, 4 * mm),
    ]
    non_valide = any(ligne.non_valide for ligne in bulletin.lignes)
    if non_valide:
        contenu += [
            _p(
                "Bulletin provisoire : certains taux appliqués restent à confirmer. Il "
                "n'est pas opposable avant cette confirmation.",
                _ALERTE,
            ),
            Spacer(0, 2 * mm),
        ]
    for alerte in bulletin.alertes:
        contenu += [_p(alerte, _ALERTE), Spacer(0, 2 * mm)]

    remuneration = [
        ["Rémunération", "Montant"],
        ["Salaire de base", _m(bulletin.salaire_base)],
        ["Primes", _m(bulletin.primes)],
        ["Avantages en nature (évalués, non versés)", _m(bulletin.avantages_evalues)],
        ["Brut taxable", _m(bulletin.brut_taxable)],
    ]
    contenu += [
        _table(remuneration, [largeur - 35 * mm, 35 * mm], totaux=[4], montants=[1]),
        Spacer(0, 4 * mm),
    ]

    def retenues(salariales: bool, titre: str, total_libelle: str, total) -> Table:
        lignes = [[titre, "Base", "Taux", "Montant"]]
        for ligne in bulletin.lignes:
            if ligne.a_charge_du_salarie is not salariales:
                continue
            libelle = ligne.libelle + (" (à valider)" if ligne.non_valide else "")
            lignes.append(
                [
                    _p(libelle),
                    _m(ligne.assiette),
                    _taux(ligne.taux) if ligne.taux is not None else "barème",
                    _m(ligne.montant),
                ]
            )
        lignes.append([total_libelle, "", "", _m(total)])
        return _table(
            lignes,
            [largeur - 85 * mm, 30 * mm, 20 * mm, 35 * mm],
            totaux=[len(lignes) - 1],
            montants=[1, 2, 3],
        )

    contenu += [
        retenues(True, "Retenues du salarié", "Total des retenues", bulletin.retenues_salariales),
        Spacer(0, 4 * mm),
        _table(
            [["Net à payer", _m(bulletin.net_a_payer)]],
            [largeur - 35 * mm, 35 * mm],
            totaux=[0],
            montants=[1],
        ),
        Spacer(0, 6 * mm),
        retenues(False, "Charges de l'employeur", "Total des charges", bulletin.charges_patronales),
        Spacer(0, 3 * mm),
        _p(f"Coût total pour l'employeur : {_m(bulletin.cout_employeur)} FCFA", _TEXTE),
        Spacer(0, 8 * mm),
        _p(
            "Bulletin établi par CGA Broad Range Consulting Group pour le compte de l'employeur. "
            "Chaque taux est celui du référentiel à la fin du mois, avec son fondement (CGI 2026 "
            "et ses annexes, décret CNPS n° 2016/072). À conserver sans limitation de durée.",
            _PETIT,
        ),
    ]
    document.build(contenu)
    return tampon.getvalue()
