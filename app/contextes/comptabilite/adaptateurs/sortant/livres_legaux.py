"""Les livres obligatoires d'un dossier, imprimables : livre-journal, grand livre, balance.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ (14 octobre 2026) : « toutes les actions comptables, y
compris les grands livres ». À l'écran, tout se lisait déjà ; mais l'Acte uniforme
OHADA relatif au droit comptable (art. 19) exige que l'entité tienne un livre-journal,
un grand livre et une balance générale, et un contrôleur les demande sur papier ou
en PDF, arrêtés à une date. Ce module les met en page.

⚠️ AUCUN MONTANT N'EST CALCULÉ ICI. Les soldes viennent de `balance`, les mouvements
de `grand_livre`, comme à l'écran : un livre qui recompterait finirait par imprimer
un autre chiffre que celui que l'écran montre.

⚠️ SEULES LES ÉCRITURES VALIDÉES. Un brouillon est une intention ; un livre légal
qui en porterait serait faux le jour où le brouillon change. L'en-tête le dit.

⚠️ LE DOCUMENT EST DÉTERMINISTE (`invariant=1`, date d'édition passée en
paramètre) : deux éditions des mêmes écritures donnent les mêmes octets.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from app.contextes.comptabilite.domaine.entites import EcritureComptable, EtatEcriture
from app.contextes.comptabilite.domaine.projections import balance, grand_livre
from app.partage.formats import ESPACE_FINE
from app.partage.formats import montant as _montant_ecran
from app.partage.horloge import a_douala

__all__ = ["Livre", "EnteteDuLivre", "rendre_le_livre"]


class Livre(StrEnum):
    JOURNAL = "journal"
    GRAND_LIVRE = "grand-livre"
    BALANCE = "balance"


TITRES = {
    Livre.JOURNAL: "Livre-journal",
    Livre.GRAND_LIVRE: "Grand livre",
    Livre.BALANCE: "Balance générale",
}

_INDIGO = HexColor("#3B2A6B")
_GRIS = HexColor("#555555")
_FOND = HexColor("#EEEAF5")
_TRAIT = HexColor("#BBBBBB")


class EnteteDuLivre:
    """Ce que porte chaque page : qui, quel exercice, quelle période, édité quand."""

    def __init__(
        self,
        *,
        niu: str,
        denomination: str,
        exercice: str,
        du: date | None,
        au: date | None,
        edite_le: datetime,
    ) -> None:
        self.niu = niu
        self.denomination = denomination
        self.exercice = exercice
        self.du = du
        self.au = au
        self.edite_le = edite_le

    @property
    def periode(self) -> str:
        if self.du and self.au:
            return f"du {self.du:%d/%m/%Y} au {self.au:%d/%m/%Y}"
        if self.au:
            return f"jusqu'au {self.au:%d/%m/%Y}"
        if self.du:
            return f"depuis le {self.du:%d/%m/%Y}"
        return "exercice entier"


def _m(valeur: Decimal) -> str:
    """Un montant, sans l'espace fine que Helvetica ne connaît pas (voir la proforma)."""
    return _montant_ecran(valeur).replace(ESPACE_FINE, " ")


def _m_ou_vide(valeur: Decimal) -> str:
    return _m(valeur) if valeur else ""


_TEXTE = ParagraphStyle("texte", fontName="Helvetica", fontSize=7.5, leading=9)
_TEXTE_GRAS = ParagraphStyle("gras", parent=_TEXTE, fontName="Helvetica-Bold")
_SOUS_TITRE = ParagraphStyle(
    "sous_titre", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=_INDIGO
)


def _p(texte: str, style: ParagraphStyle = _TEXTE) -> Paragraph:
    # Un libellé peut porter « & » ou « < » : Paragraph lit du balisage.
    return Paragraph(texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _style_de_table(lignes_de_total: list[int], colonnes_de_montants: range) -> TableStyle:
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), _INDIGO),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _INDIGO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("ALIGN", (colonnes_de_montants.start, 0), (colonnes_de_montants.stop - 1, -1), "RIGHT"),
    ]
    for rang in lignes_de_total:
        style += [
            ("FONT", (0, rang), (-1, rang), "Helvetica-Bold", 7.5),
            ("BACKGROUND", (0, rang), (-1, rang), _FOND),
            ("LINEABOVE", (0, rang), (-1, rang), 0.5, _TRAIT),
        ]
    return TableStyle(style)


def _table(
    lignes: list[list],
    largeurs: list[float],
    totaux: list[int],
    montants: range,
    separations: list[int] | None = None,
):
    table = LongTable(lignes, colWidths=largeurs, repeatRows=1)
    style = _style_de_table(totaux, montants)
    for rang in separations or []:
        style.add("LINEABOVE", (0, rang), (-1, rang), 0.3, _TRAIT)
    table.setStyle(style)
    return table


# ── Le livre-journal ─────────────────────────────────────────────────────────────


def _journal(ecritures: list[EcritureComptable], _anterieures, largeur: float, _intitule) -> list:
    """Chaque écriture, dans l'ordre chronologique, avec ses lignes ; un total par
    journal et un total général, qui s'équilibrent."""
    entete = ["Date", "Journal · n°", "Compte", "Libellé", "Pièce", "Débit", "Crédit"]
    lignes: list[list] = [entete]
    totaux: list[int] = []
    separations: list[int] = []
    par_journal: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for e in sorted(ecritures, key=lambda e: (e.date_operation, e.journal, e.numero)):
        if len(lignes) > 1:
            separations.append(len(lignes))
        for rang, ligne in enumerate(e.lignes):
            debit = ligne.montant if ligne.au_debit else Decimal(0)
            credit = Decimal(0) if ligne.au_debit else ligne.montant
            par_journal[e.journal][0] += debit
            par_journal[e.journal][1] += credit
            lignes.append(
                [
                    f"{e.date_operation:%d/%m/%Y}" if rang == 0 else "",
                    f"{e.journal} {e.numero}" if rang == 0 else "",
                    ligne.compte,
                    _p(ligne.libelle),
                    (e.piece_justificative or "") if rang == 0 else "",
                    _m_ou_vide(debit),
                    _m_ou_vide(credit),
                ]
            )
    for code in sorted(par_journal):
        d, c = par_journal[code]
        totaux.append(len(lignes))
        lignes.append(["", "", "", f"Total du journal {code}", "", _m(d), _m(c)])
    total_d = sum((v[0] for v in par_journal.values()), Decimal(0))
    total_c = sum((v[1] for v in par_journal.values()), Decimal(0))
    totaux.append(len(lignes))
    lignes.append(["", "", "", "Total général", "", _m(total_d), _m(total_c)])
    fixes = [18 * mm, 22 * mm, 18 * mm, 0, 30 * mm, 26 * mm, 26 * mm]
    fixes[3] = largeur - sum(fixes)
    return [_table(lignes, fixes, totaux, range(5, 7), separations)]


# ── Le grand livre ───────────────────────────────────────────────────────────────


def _grand_livre(
    ecritures: list[EcritureComptable],
    anterieures: list[EcritureComptable],
    largeur: float,
    intitule,
) -> list:
    """Chaque compte : son solde reporté, ses mouvements de la période et son solde
    progressif, puis ses totaux.

    ⚠️ LE SOLDE REPORTÉ. Sur une période qui commence après l'ouverture de l'exercice,
    un solde progressif parti de zéro serait faux à chaque ligne : il part du solde
    des mouvements antérieurs de l'exercice, et le livre l'imprime.
    """
    fixes = [18 * mm, 22 * mm, 0, 26 * mm, 26 * mm, 28 * mm]
    fixes[2] = largeur - sum(fixes)
    reports = {s.compte: s.solde for s in balance(anterieures)}
    periode = {s.compte: s for s in balance(ecritures)}
    contenu: list = []
    for compte in sorted(set(reports) | set(periode)):
        report = reports.get(compte, Decimal(0))
        mouvements = grand_livre(ecritures, compte)
        lignes: list[list] = [["Date", "Journal · pièce", "Libellé", "Débit", "Crédit", "Solde"]]
        if compte in reports:
            lignes.append(["", "", _p("Solde reporté", _TEXTE_GRAS), "", "", _solde(report)])
        for m in mouvements:
            lignes.append(
                [
                    f"{m.date_operation:%d/%m/%Y}",
                    f"{m.journal} {m.piece_justificative or ''}".strip(),
                    _p(m.libelle + (f" · {m.tiers}" if m.tiers else "")),
                    _m(m.montant) if m.sens.value == "DEBIT" else "",
                    _m(m.montant) if m.sens.value == "CREDIT" else "",
                    _solde(report + m.solde_progressif),
                ]
            )
        rang_total = len(lignes)
        du_compte = periode.get(compte)
        debit = du_compte.total_debit if du_compte else Decimal(0)
        credit = du_compte.total_credit if du_compte else Decimal(0)
        lignes.append(
            [
                "",
                "",
                f"Total du compte {compte}",
                _m(debit),
                _m(credit),
                _solde(report + debit - credit),
            ]
        )
        contenu.append(_p(f"{compte} · {intitule(compte)}", _SOUS_TITRE))
        contenu.append(_table(lignes, fixes, [rang_total], range(3, 6)))
        contenu.append(Spacer(0, 4 * mm))
    return contenu


def _solde(valeur: Decimal) -> str:
    if valeur > 0:
        return f"{_m(valeur)} D"
    if valeur < 0:
        return f"{_m(-valeur)} C"
    return "0"


# ── La balance générale ──────────────────────────────────────────────────────────


def _balance(
    ecritures: list[EcritureComptable],
    anterieures: list[EcritureComptable],
    largeur: float,
    intitule,
) -> list:
    """Les comptes, leurs mouvements et leurs soldes ; un total par classe, et le total
    général où débits et crédits s'égalent.

    ⚠️ UNE BALANCE S'ARRÊTE À UNE DATE, DEPUIS L'OUVERTURE : les mouvements antérieurs
    à la période y entrent. Une balance « de juillet » seule ne dirait aucun solde vrai.
    """
    ecritures = [*anterieures, *ecritures]
    fixes = [20 * mm, 0, 30 * mm, 30 * mm, 30 * mm, 30 * mm]
    fixes[1] = largeur - sum(fixes)
    lignes: list[list] = [
        [
            "Compte",
            "Intitulé",
            "Mouvements débit",
            "Mouvements crédit",
            "Solde débiteur",
            "Solde créditeur",
        ]
    ]
    totaux: list[int] = []
    soldes = balance(ecritures)
    par_classe: dict[int, list[Decimal]] = defaultdict(lambda: [Decimal(0)] * 4)

    def cumuler(cible: list[Decimal], s) -> None:
        cible[0] += s.total_debit
        cible[1] += s.total_credit
        cible[2] += s.solde_debiteur
        cible[3] += s.solde_crediteur

    for rang, s in enumerate(soldes):
        lignes.append(
            [
                s.compte,
                _p(intitule(s.compte)),
                _m(s.total_debit),
                _m(s.total_credit),
                _m_ou_vide(s.solde_debiteur),
                _m_ou_vide(s.solde_crediteur),
            ]
        )
        cumuler(par_classe[s.classe], s)
        suivant = soldes[rang + 1] if rang + 1 < len(soldes) else None
        if suivant is None or suivant.classe != s.classe:
            t = par_classe[s.classe]
            totaux.append(len(lignes))
            lignes.append(["", f"Total de la classe {s.classe}", *(_m(v) for v in t)])
    general = [sum((t[i] for t in par_classe.values()), Decimal(0)) for i in range(4)]
    totaux.append(len(lignes))
    lignes.append(["", "Total général", *(_m(v) for v in general)])
    return [_table(lignes, fixes, totaux, range(2, 6))]


_COMPOSITEURS: dict[Livre, Callable] = {
    Livre.JOURNAL: _journal,
    Livre.GRAND_LIVRE: _grand_livre,
    Livre.BALANCE: _balance,
}


def rendre_le_livre(
    livre: Livre,
    ecritures: list[EcritureComptable],
    entete: EnteteDuLivre,
    intitule: Callable[[str], str],
) -> bytes:
    """Le PDF du livre, en octets.

    `ecritures` : toutes celles de l'exercice. La période de l'en-tête découpe ici :
    avant `du`, les mouvements font le solde reporté (grand livre) ou entrent dans
    les cumuls (balance) ; après `au`, rien. Les brouillons sont écartés ici, quoi
    qu'on passe.
    """
    validees = [
        e
        for e in ecritures
        if e.etat is EtatEcriture.VALIDEE and (entete.au is None or e.date_operation <= entete.au)
    ]
    anterieures = [e for e in validees if entete.du is not None and e.date_operation < entete.du]
    validees = [e for e in validees if entete.du is None or e.date_operation >= entete.du]
    tampon = BytesIO()
    taille = landscape(A4) if livre is Livre.BALANCE else A4
    marge = 12 * mm
    document = SimpleDocTemplate(
        tampon,
        pagesize=taille,
        leftMargin=marge,
        rightMargin=marge,
        topMargin=26 * mm,
        bottomMargin=16 * mm,
        title=f"{TITRES[livre]} · {entete.denomination} · {entete.exercice}",
        author="CGA Broad Range Consulting Group",
        invariant=1,
    )
    largeur = taille[0] - 2 * marge

    def habiller(toile, doc) -> None:
        toile.saveState()
        haut = taille[1] - 12 * mm
        toile.setFillColor(_INDIGO)
        toile.setFont("Helvetica-Bold", 12)
        toile.drawString(marge, haut, TITRES[livre])
        toile.setFont("Helvetica", 8.5)
        toile.setFillColor(_GRIS)
        periode = (
            # La balance cumule depuis l'ouverture : elle dit sa date d'arrêté, pas une période.
            (f"arrêtée au {entete.au:%d/%m/%Y}" if entete.au else "exercice entier")
            if livre is Livre.BALANCE
            else entete.periode
        )
        toile.drawRightString(taille[0] - marge, haut, f"Exercice {entete.exercice} · {periode}")
        toile.setFillColorRGB(0, 0, 0)
        toile.setFont("Helvetica-Bold", 9)
        toile.drawString(marge, haut - 5 * mm, entete.denomination)
        toile.setFont("Helvetica", 8.5)
        toile.drawString(
            marge + toile.stringWidth(entete.denomination, "Helvetica-Bold", 9) + 3 * mm,
            haut - 5 * mm,
            f"NIU {entete.niu}",
        )
        toile.setFillColor(_GRIS)
        toile.drawRightString(taille[0] - marge, haut - 5 * mm, "Écritures validées uniquement")
        toile.setFont("Helvetica", 7)
        toile.drawString(
            marge,
            9 * mm,
            f"Tenu par CGA Broad Range Consulting Group · édité le "
            f"{a_douala(entete.edite_le):%d/%m/%Y à %H:%M} (heure de Douala)",
        )
        toile.drawRightString(taille[0] - marge, 9 * mm, f"Page {doc.page}")
        toile.restoreState()

    contenu = _COMPOSITEURS[livre](validees, anterieures, largeur, intitule)
    if not validees and not (anterieures and livre is not Livre.JOURNAL):
        contenu = [_p("Aucune écriture validée sur cette période.", _TEXTE_GRAS)]
    document.build(contenu, onFirstPage=habiller, onLaterPages=habiller)
    return tampon.getvalue()
