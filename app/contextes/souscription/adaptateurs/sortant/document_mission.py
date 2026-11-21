"""Le livrable d'une mission : les seuls points retenus, dans les mots du juriste.

⚠️ RIEN D'ÉCARTÉ, RIEN D'EN ATTENTE N'Y FIGURE, et le texte imprimé est `texte_retenu`,
pas la proposition de l'agent : c'est le juriste qui signe. Tant que la mission n'est
pas livrée, le document porte « PROJET » en tête de chaque page.

⚠️ AUCUNE MENTION DE L'AGENT DANS LE CORPS : le client reçoit l'avis du cabinet. La
page de garde dit, elle, comment l'analyse a été conduite (lecture assistée, décision
humaine), parce que le cabinet s'y est engagé.

⚠️ AUCUN NOM DE COLLABORATEUR (18 octobre 2026) : le livrable part chez le client, et qui
l'a instruit ou validé est une information interne. Le cabinet signe ; le journal d'audit
et l'écran de la mission gardent les noms.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.contextes.souscription.domaine.missions import (
    LIBELLES_TYPE,
    EtatMission,
    Mission,
    OrigineProposition,
    Rubrique,
    TypeMission,
)
from app.partage.horloge import a_douala
from app.partage.marque.papier import HAUTEUR_DU_BANDEAU, bandeau_de_la_maison

__all__ = ["rendre_le_livrable"]

_INDIGO = HexColor("#2E1B4D")
_MAGENTA = HexColor("#8C2D86")
_GRIS = HexColor("#5B556B")

_ORDRE = [
    (Rubrique.RESUME, "Objet du contrat"),
    (Rubrique.PARTIES, "Les parties"),
    (Rubrique.OBLIGATION, "Obligations essentielles"),
    (Rubrique.CLAUSE_A_RISQUE, "Clauses à surveiller"),
    (Rubrique.MANQUE, "Ce qui manque"),
    (Rubrique.RECOMMANDATION, "Nos recommandations"),
]
#: 15 octobre 2026 : une DSF ciblée parlait « d'objet du contrat » au client. Ses intitulés.
_ORDRE_DSF = [
    (Rubrique.RESUME, "Synthèse de la déclaration"),
    (Rubrique.CLAUSE_A_RISQUE, "Points à surveiller"),
    (Rubrique.MANQUE, "Ce qui manque"),
    (Rubrique.RECOMMANDATION, "Nos recommandations"),
    (Rubrique.PARTIES, "Les parties"),
    (Rubrique.OBLIGATION, "Obligations"),
]
_GRAVITES = {"ELEVEE": "risque élevé", "MOYENNE": "risque moyen", "FAIBLE": "risque faible"}

_TEXTE = ParagraphStyle("t", fontName="Helvetica", fontSize=10, leading=14)
_CITATION = ParagraphStyle(
    "c", parent=_TEXTE, fontName="Helvetica-Oblique", fontSize=9, textColor=_GRIS, leftIndent=8 * mm
)
_TITRE = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=_INDIGO)
_POINT = ParagraphStyle("p", fontName="Helvetica-Bold", fontSize=10.5, leading=14)
_NOTE = ParagraphStyle("n", parent=_TEXTE, fontSize=8.5, leading=11, textColor=_GRIS)


def _p(texte: str, style=_TEXTE) -> Paragraph:
    return Paragraph(texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _douala(instant) -> str:
    return f"{a_douala(instant):%d/%m/%Y}"


def _projet_de_contrat(contenu: list, mission: Mission, retenues) -> None:
    """Le projet : les articles retenus, numérotés dans l'ordre où le juriste les a gardés."""
    titre = (mission.type_de_contrat or "Contrat").upper()
    contenu.append(_p(f"PROJET DE {titre}", _TITRE))
    contenu.append(Spacer(0, 3 * mm))
    contenu.append(
        _p(
            "Projet préparé par le cabinet, à relire par les parties avant toute signature. "
            "Les mentions [À COMPLÉTER] signalent une donnée que le cahier ne portait pas.",
            _NOTE,
        )
    )
    contenu.append(Spacer(0, 5 * mm))
    articles = [p for p in retenues if p.rubrique is Rubrique.ARTICLE]
    for numero, article in enumerate(articles, start=1):
        contenu.append(_p(f"Article {numero} · {article.titre}", _POINT))
        contenu.append(_p(article.texte_retenu or ""))
        contenu.append(Spacer(0, 3 * mm))


def rendre_le_livrable(mission: Mission) -> bytes:
    tampon = BytesIO()
    marge = 18 * mm
    document = SimpleDocTemplate(
        tampon,
        pagesize=A4,
        leftMargin=marge,
        rightMargin=marge,
        topMargin=28 * mm + HAUTEUR_DU_BANDEAU,
        bottomMargin=20 * mm,
        title=f"{LIBELLES_TYPE[mission.type]} · {mission.client} · {mission.identifiant}",
        author="CGA Broad Range Consulting Group",
        invariant=1,
    )
    livree = mission.etat is EtatMission.LIVREE

    def habiller(toile, doc) -> None:
        toile.saveState()
        # 19 octobre 2026 (points 19-20) : le papier à en-tête du cabinet, puis le titre.
        haut = bandeau_de_la_maison(toile, largeur=A4[0], hauteur=A4[1], marge=marge) - 6 * mm
        toile.setFillColor(_INDIGO)
        toile.setFont("Helvetica-Bold", 12)
        toile.drawString(marge, haut, f"{LIBELLES_TYPE[mission.type]} · {mission.client}")
        toile.setFont("Helvetica", 8.5)
        toile.setFillColor(_GRIS)
        toile.drawString(marge, haut - 5 * mm, f"{mission.identifiant} · {mission.objet[:90]}")
        if not livree:
            toile.setFillColor(_MAGENTA)
            toile.setFont("Helvetica-Bold", 10)
            toile.drawRightString(A4[0] - marge, haut, "PROJET · non validé")
        toile.setFillColor(_GRIS)
        toile.setFont("Helvetica", 7)
        toile.drawString(
            marge, 10 * mm, "CGA Broad Range Consulting Group · agrément MINFI/DGI n° 00000048"
        )
        toile.drawRightString(A4[0] - marge, 10 * mm, f"Page {doc.page}")
        toile.restoreState()

    contenu: list = []
    retenues = mission.retenues
    if mission.type is TypeMission.REDACTION_CONTRAT:
        _projet_de_contrat(contenu, mission, retenues)
    else:
        assiste = any(p.origine is OrigineProposition.AGENT for p in retenues)
        dsf = mission.type is TypeMission.DSF_CIBLEE
        objet = (
            f"Ce document présente les constats et recommandations du cabinet sur la "
            f"déclaration statistique et fiscale de l'exercice {mission.exercice} "
            f"(NIU {mission.dossier_niu}). La déclaration elle-même est préparée, validée "
            "et déposée par son propre parcours. "
            if dsf
            else "Ce document présente l'avis du cabinet sur le contrat qui lui a été confié. "
        )
        contenu.append(
            _p(
                objet
                + (
                    "Une lecture assistée a préparé des propositions ; chaque point "
                    "ci-dessous a été examiné, retenu et rédigé par un collaborateur du "
                    "cabinet, puis validé par un second regard."
                    if assiste
                    else "Chaque point ci-dessous a été rédigé par un collaborateur du "
                    "cabinet, puis validé par un second regard."
                ),
                _NOTE,
            )
        )
        contenu.append(Spacer(0, 6 * mm))
        for rubrique, titre in _ORDRE_DSF if dsf else _ORDRE:
            points = [p for p in retenues if p.rubrique is rubrique]
            if not points:
                continue
            contenu.append(_p(titre, _TITRE))
            contenu.append(Spacer(0, 2 * mm))
            for point in points:
                entete = point.titre
                if point.gravite:
                    entete += f" ({_GRAVITES[point.gravite.value]})"
                contenu.append(_p(entete, _POINT))
                contenu.append(_p(point.texte_retenu or ""))
                if point.citation:
                    contenu.append(_p(f"« {point.citation} »", _CITATION))
                contenu.append(Spacer(0, 3 * mm))
            contenu.append(Spacer(0, 3 * mm))

    contenu.append(Spacer(0, 6 * mm))
    if livree and mission.validee_le:
        visa = f"Validé par le cabinet le {_douala(mission.validee_le)}."
    else:
        visa = "Projet en cours de validation par le cabinet."
    contenu.append(_p(visa, _NOTE))
    document.build(contenu, onFirstPage=habiller, onLaterPages=habiller)
    return tampon.getvalue()
