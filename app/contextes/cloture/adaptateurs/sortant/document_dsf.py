"""L'aperçu de la DSF, imprimable : la version figée, mise en page.

─────────────────────────────────────────────────────────────────────────────────
⚠️ RENDU DEPUIS LE CONTENU FIGÉ, JAMAIS DEPUIS LA LIASSE DU MOMENT. Le réviseur
valide ce qu'il lit ; s'il lisait un recalcul, une écriture passée entre-temps lui
ferait valider autre chose que ce qui partira. Le PDF porte l'empreinte du contenu
d'où il sort.

⚠️ AUCUN MONTANT N'EST CALCULÉ ICI : tout vient de la liasse figée.

⚠️ CE N'EST PAS LE FORMULAIRE DE LA DGI. Le modèle « format DGI » ne se télécharge
qu'une fois connecté au compte du contribuable ; le document le dit en pied de
page, pour qu'aucun adhérent ne le prenne pour sa déclaration officielle.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from app.contextes.cloture.domaine.declaration_dsf import DeclarationDSF, EtatDSF
from app.partage.formats import ESPACE_FINE
from app.partage.formats import montant as _montant_ecran

__all__ = ["rendre_la_dsf"]

_INDIGO = HexColor("#3B2A6B")
_MAGENTA = HexColor("#8C2D86")
_GRIS = HexColor("#555555")
_FOND = HexColor("#EEEAF5")
_TRAIT = HexColor("#BBBBBB")
_ROUGE = HexColor("#A61B1B")
_VERT = HexColor("#1B6E3A")

_SYSTEMES = {"NORMAL": "Système normal", "MINIMAL": "Système minimal de trésorerie (SMT)"}
_ETATS = {
    EtatDSF.PREPAREE: "PROJET · préparée, non validée",
    EtatDSF.VALIDEE: "Validée, prête au dépôt",
    EtatDSF.DEPOSEE: "Déposée à la DGI",
    EtatDSF.REMPLACEE: "Remplacée par une version plus récente",
}

_TEXTE = ParagraphStyle("t", fontName="Helvetica", fontSize=8.5, leading=11)
_PETIT = ParagraphStyle("p", parent=_TEXTE, fontSize=7.5, leading=9.5, textColor=_GRIS)
_TITRE = ParagraphStyle(
    "h", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=_INDIGO, spaceAfter=4
)


def _m(valeur) -> str:
    """Un montant, sans l'espace fine que Helvetica ne connaît pas (voir la proforma)."""
    return _montant_ecran(Decimal(str(valeur))).replace(ESPACE_FINE, " ")


def _p(texte: str, style: ParagraphStyle = _TEXTE) -> Paragraph:
    return Paragraph(texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _table(lignes: list[list], largeurs: list[float], *, totaux=(), montants=()) -> LongTable:
    table = LongTable(lignes, colWidths=largeurs, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), _INDIGO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _INDIGO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for colonne in montants:
        style.append(("ALIGN", (colonne, 0), (colonne, -1), "RIGHT"))
    for rang in totaux:
        style += [
            ("FONT", (0, rang), (-1, rang), "Helvetica-Bold", 8.5),
            ("BACKGROUND", (0, rang), (-1, rang), _FOND),
            ("LINEABOVE", (0, rang), (-1, rang), 0.5, _TRAIT),
        ]
    table.setStyle(TableStyle(style))
    return table


def _postes(liasse: dict, sens: str, largeur: float, titre_total: str) -> LongTable:
    lignes: list[list] = [["Poste", "Libellé", "Comptes", "Montant"]]
    total = Decimal(0)
    for ligne in liasse["lignes"]:
        if ligne["sens"] != sens:
            continue
        total += Decimal(str(ligne["montant"]))
        lignes.append(
            [
                ligne["poste"],
                _p(ligne["libelle"]),
                _p(", ".join(ligne.get("comptes") or []), _PETIT),
                _m(ligne["montant"]),
            ]
        )
    if len(lignes) == 1:
        lignes.append(["", _p("Aucun poste mouvementé."), "", ""])
    lignes.append(["", titre_total, "", _m(total)])
    fixes = [18 * mm, 0, 40 * mm, 30 * mm]
    fixes[1] = largeur - sum(fixes)
    return _table(lignes, fixes, totaux=[len(lignes) - 1], montants=[3])


def rendre_la_dsf(dsf: DeclarationDSF, *, noms: dict[str, str] | None = None) -> bytes:
    """Le PDF de la version, en octets ; déterministe pour une même version et les mêmes noms.

    `noms` : identifiant de compte → nom, pour les visas. Le nom se lit, l'identifiant
    reste entre parenthèses : c'est lui que le journal d'audit porte.
    """
    noms = noms or {}

    def _douala(instant) -> str:
        # Horodaté en UTC (`partage/horloge.py`) ; lu à Douala, UTC+1 sans heure d'été.
        return f"{instant + timedelta(hours=1):%d/%m/%Y %H:%M}"

    def qui(compte: str | None) -> str:
        if not compte:
            return ""
        return f"{noms[compte]} ({compte})" if compte in noms else compte

    liasse = json.loads(dsf.contenu)
    tampon = BytesIO()
    marge = 15 * mm
    document = SimpleDocTemplate(
        tampon,
        pagesize=A4,
        leftMargin=marge,
        rightMargin=marge,
        topMargin=30 * mm,
        bottomMargin=22 * mm,
        title=f"DSF {dsf.exercice} · {dsf.denomination} · version {dsf.version}",
        author="CGA Broad Range Consulting Group",
        invariant=1,
    )
    largeur = A4[0] - 2 * marge

    def habiller(toile, doc) -> None:
        toile.saveState()
        haut = A4[1] - 13 * mm
        toile.setFillColor(_INDIGO)
        toile.setFont("Helvetica-Bold", 12)
        toile.drawString(
            marge, haut, f"Déclaration statistique et fiscale · exercice {dsf.exercice}"
        )
        toile.setFillColorRGB(0, 0, 0)
        toile.setFont("Helvetica-Bold", 9)
        toile.drawString(marge, haut - 5.5 * mm, dsf.denomination)
        toile.setFont("Helvetica", 9)
        toile.drawString(
            marge + toile.stringWidth(dsf.denomination, "Helvetica-Bold", 9) + 3 * mm,
            haut - 5.5 * mm,
            f"NIU {dsf.entreprise}",
        )
        couleur = {EtatDSF.DEPOSEE: _VERT, EtatDSF.VALIDEE: _INDIGO}.get(dsf.etat, _MAGENTA)
        toile.setFillColor(couleur)
        toile.setFont("Helvetica-Bold", 9)
        toile.drawRightString(A4[0] - marge, haut, f"Version {dsf.version} · {_ETATS[dsf.etat]}")
        toile.setFillColor(_GRIS)
        toile.setFont("Helvetica", 6.5)
        toile.drawString(
            marge,
            13 * mm,
            "Document de travail de la plateforme CGA BRCG, à reporter sur le modèle de la DGI "
            "(fichier « format DGI » ou API de télédéclaration).",
        )
        toile.drawString(
            marge,
            9.5 * mm,
            "Il ne remplace pas la DSF déposée sur le portail. "
            f"Empreinte SHA-256 : {dsf.empreinte}",
        )
        toile.drawRightString(A4[0] - marge, 9.5 * mm, f"Page {doc.page}")
        toile.restoreState()

    contenu: list = []

    # ── Identification, synthèse, contrôles, visas ────────────────────────────
    contenu.append(_p("Identification", _TITRE))
    droit = liasse.get("droit_cga") or {}
    identification = [
        ["Dénomination", _p(dsf.denomination)],
        ["NIU", dsf.entreprise],
        ["Exercice", f"du {dsf.periode_debut:%d/%m/%Y} au {dsf.periode_fin:%d/%m/%Y}"],
        ["Système", _SYSTEMES.get(dsf.systeme, dsf.systeme)],
        [
            "Adhérent CGA",
            _p(
                ("Droit ouvert : " if droit.get("ouvert") else "Droit fermé : ")
                + str(droit.get("motif", "non apprécié"))
            ),
        ],
    ]
    contenu.append(_table([["", ""], *identification], [40 * mm, largeur - 40 * mm]))
    contenu.append(Spacer(0, 5 * mm))

    contenu.append(_p("Synthèse", _TITRE))
    synthese = [
        ["Grandeur", "Montant"],
        ["Total de l'actif", _m(liasse["total_actif"])],
        # ⚠️ Hors résultat : la liasse range le résultat de l'exercice à part (il boucle
        # le bilan). Sans le dire, « actif 32 M, passif 5 M » se lirait comme un écart.
        ["Total du passif, hors résultat de l'exercice", _m(liasse["total_passif"])],
        ["Total des produits", _m(liasse["total_produits"])],
        ["Total des charges", _m(liasse["total_charges"])],
        ["Résultat comptable", _m(liasse["resultat_comptable"])],
        ["Réintégrations", _m(liasse["total_reintegrations"])],
        ["Déductions", _m(liasse["total_deductions"])],
        ["Résultat fiscal", _m(liasse["resultat_fiscal"])],
    ]
    contenu.append(_table(synthese, [largeur - 40 * mm, 40 * mm], totaux=[5, 8], montants=[1]))
    contenu.append(Spacer(0, 5 * mm))

    contenu.append(_p("Contrôles de cohérence", _TITRE))
    controles = [["Contrôle", "Résultat"]]
    for c in liasse["controles"]:
        controles.append([_p(c["libelle"]), "satisfait" if c["satisfait"] else "EN ÉCHEC"])
    table = _table(controles, [largeur - 30 * mm, 30 * mm])
    for rang, c in enumerate(liasse["controles"], start=1):
        table.setStyle(
            TableStyle([("TEXTCOLOR", (1, rang), (1, rang), _VERT if c["satisfait"] else _ROUGE)])
        )
    contenu.append(table)
    contenu.append(Spacer(0, 5 * mm))

    visas = [
        ["Visa", "Par", "Le"],
        ["Préparée", qui(dsf.preparee_par), _douala(dsf.preparee_le)],
        [
            "Validée (second regard)",
            qui(dsf.validee_par) or "à valider",
            _douala(dsf.validee_le) if dsf.validee_le else "",
        ],
        [
            "Déposée à la DGI",
            (
                f"{qui(dsf.deposee_par)} · accusé {dsf.numero_accuse}"
                if dsf.deposee_par
                else "à déposer"
            ),
            _douala(dsf.deposee_le) if dsf.deposee_le else "",
        ],
    ]
    contenu.append(
        KeepTogether(
            [
                _p("Visas", _TITRE),
                _table(visas, [45 * mm, largeur - 85 * mm, 40 * mm]),
            ]
        )
    )

    # ── Le bilan ──────────────────────────────────────────────────────────────
    contenu.append(PageBreak())
    contenu.append(_p("Bilan · actif", _TITRE))
    contenu.append(_postes(liasse, "ACTIF", largeur, "Total de l'actif"))
    contenu.append(Spacer(0, 6 * mm))
    contenu.append(_p("Bilan · passif", _TITRE))
    contenu.append(
        _postes(liasse, "PASSIF", largeur, "Total du passif, hors résultat de l'exercice")
    )
    contenu.append(
        _p(
            f"Résultat de l'exercice, qui boucle le bilan : {_m(liasse['resultat_comptable'])}",
            _PETIT,
        )
    )

    # ── Le compte de résultat ────────────────────────────────────────────────
    contenu.append(PageBreak())
    contenu.append(_p("Compte de résultat · produits", _TITRE))
    contenu.append(_postes(liasse, "PRODUIT", largeur, "Total des produits"))
    contenu.append(Spacer(0, 6 * mm))
    contenu.append(_p("Compte de résultat · charges", _TITRE))
    contenu.append(_postes(liasse, "CHARGE", largeur, "Total des charges"))

    # ── Le passage au résultat fiscal ────────────────────────────────────────
    contenu.append(Spacer(0, 6 * mm))
    passage = [["Code", "Libellé", "Nature", "Montant"]]
    passage.append(["", "Résultat comptable", "", _m(liasse["resultat_comptable"])])
    for ligne in liasse["passage"]:
        passage.append(
            [
                ligne["code"],
                _p(ligne["libelle"]),
                "réintégration" if ligne["nature"] == "REINTEGRATION" else "déduction",
                _m(ligne["montant"]),
            ]
        )
    passage.append(["", "Résultat fiscal", "", _m(liasse["resultat_fiscal"])])
    fixes = [22 * mm, 0, 28 * mm, 30 * mm]
    fixes[1] = largeur - sum(fixes)
    elements = [
        _p("Passage du résultat comptable au résultat fiscal", _TITRE),
        _table(passage, fixes, totaux=[1, len(passage) - 1], montants=[3]),
    ]
    if liasse.get("abattement_cga_ecarte"):
        elements += [
            Spacer(0, 2 * mm),
            _p(f"Abattement CGA écarté : {liasse['abattement_cga_ecarte']}", _PETIT),
        ]
    contenu.append(KeepTogether(elements))

    document.build(contenu, onFirstPage=habiller, onLaterPages=habiller)
    return tampon.getvalue()
