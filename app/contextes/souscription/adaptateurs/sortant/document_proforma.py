"""Le document de proforma, tel que le client le reçoit et le classe.

─────────────────────────────────────────────────────────────────────────────────
CE QUI MANQUAIT

Le code le disait de lui-même : « le contenu du document PDF n'est pas encore
produit : son empreinte porte donc ce que le système sait de figé ». La cliente
lisait une page, cliquait « j'accepte », et **n'avait rien à garder** de son
engagement. Pas de pièce jointe, pas même une feuille imprimable.

⚠️ **LE DOCUMENT EST DÉTERMINISTE, ET C'EST CE QUI DONNE UN SENS À L'EMPREINTE.**

Un PDF porte ordinairement sa date de création et un identifiant tirés au hasard.
Deux rendus de la même proforma donneraient alors deux empreintes, et
`document_intact` répondrait toujours « non » : le contrôle existerait sans rien
contrôler. `invariant=1` et une date figée sur l'émission suppriment ces deux
sources de variation.

⚠️ **AUCUN MONTANT N'EST CALCULÉ ICI.** Les rubriques viennent du référentiel,
les sous-totaux du domaine, le total de leur somme. Ce module met en page, et
c'est tout : un rendu qui recalculerait finirait par imprimer autre chose que ce
que le système a enregistré.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from app.contextes.souscription.domaine.composition_proforma import CompositionProforma
from app.partage.formats import ESPACE_FINE, en_toutes_lettres
from app.partage.formats import montant as _montant_ecran

__all__ = ["rendre_le_document"]


def _sous(valeur: Decimal) -> str:
    """Un montant pour le PDF, séparateur de milliers compris.

    ⚠️ **L'ESPACE FINE INSÉCABLE N'EXISTE PAS DANS HELVETICA**, et reportlab la
    rend en carré noir : le document affichait « 63\u25a0000 FCFA ». Défaut vu en
    regardant la page, pas en lisant le code : écran et PDF partagent le même
    `montant()`, et l'écran la rendait correctement.

    ⚠️ **ET L'INSÉCABLE ORDINAIRE N'Y CHANGE RIEN.** Premier correctif tenté :
    U+00A0, présent au Latin-1. Le carré disparaît, mais reportlab écrit une
    espace ordinaire dans le flux — vérifié octet par octet, le document contient
    « (63 000 FCFA) Tj ». La promesse d'insécabilité aurait donc été fausse.

    Elle n'a de toute façon aucun sens ici : **un PDF ne recompose pas**. Chaque
    montant est posé à une coordonnée absolue par un `drawString` qui lui est
    propre ; il n'existe aucun retour à la ligne à empêcher. L'insécable protège
    d'un repli que ce format ne connaît pas.

    On écrit donc l'espace ordinaire, délibérément, plutôt que de garder un
    caractère que la bibliothèque remplace en silence.
    """
    return _montant_ecran(valeur).replace(ESPACE_FINE, " ")


# ── La mise en page ──────────────────────────────────────────────────────────
#
# Les mesures sont en millimètres, converties par `mm`. reportlab travaille en
# points typographiques, dont personne ne parle quand on décrit une marge.

_LARGEUR, _HAUTEUR = A4
#: 20 mm de marge, comme les proformas du cabinet.
_MARGE = 20 * mm

#: La charte. Les mêmes valeurs que les courriels (`gabarits_courriel.py`) :
#: un client qui reçoit le message puis ouvre le document doit voir la même
#: maison.
_INDIGO = HexColor("#3B2A6B")
_MAGENTA = HexColor("#8C2D86")
#: Le gris des mentions secondaires, assez foncé pour rester lisible à
#: l'impression en noir et blanc, que le cabinet pratique.
_GRIS = HexColor("#555555")

#: Le pied de page, tel qu'il figure sur les documents du cabinet.
#: ⚠️ Le point médian « · » et non le tiret cadratin : aucun tiret cadratin
#: nulle part dans ce que le client lit.
_PIED = (
    "+237 233 424 847   ·   +237 699 902 184   ·   WhatsApp +237 699 902 184",
    "contact@cga-brcgroup.com   ·   www.cga-brcgroup.com",
    "Douala Akwa, B.P. 7761   ·   Yaoundé Elig-Essono   ·   Bafoussam Quartier Haoussa",
    "Agrément n° 00000048/MINFI/DGI du 28 janvier 2020",
)


def rendre_le_document(
    composition: CompositionProforma,
    *,
    numero: str,
    version: int,
    emise_le: datetime,
    client: str,
    total_arrete: Decimal | None = None,
) -> bytes:
    """Le PDF, en octets.

    ⚠️ `total_arrete` n'est **pas** le total de la composition quand le
    responsable s'en écarte : il a ce droit, motif à l'appui, et le document doit
    porter ce qu'il a arrêté. Le détail des rubriques reste celui du référentiel,
    et l'écart se lit entre les deux. Le cacher ferait signer au client un total
    que ses propres lignes ne justifient pas, sans qu'il puisse le voir.
    """
    tampon = BytesIO()
    # ⚠️ `invariant=1` : sans lui, reportlab pose un identifiant aléatoire et
    # l'heure courante, et deux rendus du même document donnent deux empreintes.
    page = canvas.Canvas(tampon, pagesize=A4, invariant=1)
    page.setTitle(f"Proforma {numero}")
    page.setAuthor("CGA Broad Range Consulting Group")
    page.setSubject(composition.libelle)

    y = _entete(page)
    y = _titre(page, composition, y)
    y = _identification(page, numero, version, emise_le, client, y)
    for rubrique in composition.rubriques:
        y = _rubrique(page, rubrique, y)
    total = composition.total if total_arrete is None else total_arrete
    y = _total(page, total, composition.total, y)
    _pied(page)
    page.showPage()
    page.save()
    return tampon.getvalue()


def _entete(page: canvas.Canvas) -> float:
    haut = _HAUTEUR - 14 * mm
    page.setFillColor(_INDIGO)
    page.rect(0, haut - 14 * mm, _LARGEUR, 14 * mm, stroke=0, fill=1)
    page.setFillColor(white)
    page.setFont("Helvetica-Bold", 16)
    page.drawString(_MARGE, haut - 6 * mm, "CGA BROAD RANGE CONSULTING")
    page.setFont("Helvetica", 7.5)
    page.drawString(
        _MARGE,
        haut - 11 * mm,
        "ASSISTANCE COMPTABLE ET FISCALE  ·  ASSISTANCE JURIDIQUE  ·  FORMATIONS"
        "  ·  PROCÉDURES ADMINISTRATIVES",
    )
    return haut - 26 * mm


def _titre(page: canvas.Canvas, composition: CompositionProforma, y: float) -> float:
    page.setFillColor(HexColor("#EDE7F3"))
    page.rect(_MARGE, y - 2 * mm, _LARGEUR - 2 * _MARGE, 8 * mm, stroke=0, fill=1)
    page.setFillColor(black)
    page.setFont("Helvetica-Bold", 11.5)
    page.drawCentredString(_LARGEUR / 2, y + 0.6 * mm, composition.titre)
    y -= 10 * mm
    page.setFont("Helvetica-Bold", 8.5)
    for ligne in _couper(
        page, composition.libelle, "Helvetica-Bold", 8.5, _LARGEUR - 2 * _MARGE
    ):
        page.drawCentredString(_LARGEUR / 2, y, ligne)
        y -= 4.6 * mm
    return y - 3 * mm


def _identification(
    page: canvas.Canvas,
    numero: str,
    version: int,
    emise_le: datetime,
    client: str,
    y: float,
) -> float:
    """Le numéro, la version et le client. **Absents des documents papier.**

    ⚠️ Les proformas du cabinet n'en portent pas : elles sont établies à la main,
    une par client, et le classeur fait office de numérotation. Un document émis
    par un système doit se désigner lui-même, sans quoi deux propositions
    successives au même client ne se distinguent que par leur date.
    """
    page.setFont("Helvetica", 9)
    page.setFillColor(_GRIS)
    page.drawString(_MARGE, y, f"N° {numero}   ·   version {version}   ·   {emise_le:%d/%m/%Y}")
    page.drawRightString(_LARGEUR - _MARGE, y, f"Établie pour : {client}")
    page.setFillColor(black)
    return y - 8 * mm


def _rubrique(page: canvas.Canvas, rubrique, y: float) -> float:
    page.setFont("Helvetica-Bold", 10)
    page.drawCentredString(_LARGEUR / 2, y, f"{rubrique.numero}.   {rubrique.intitule}")
    y -= 6.5 * mm
    page.setFont("Helvetica", 9.5)
    for ligne in rubrique.lignes:
        page.drawString(_MARGE + 4 * mm, y, f"•  {ligne.libelle}")
        page.drawRightString(_LARGEUR - _MARGE, y, f"{_sous(ligne.montant)} FCFA")
        y -= 5.2 * mm
    # ⚠️ Le sous-total se calcule, il n'est pas au référentiel : une seconde
    # vérité à côté des lignes finirait par en diverger.
    if len(rubrique.lignes) > 1:
        page.setLineWidth(0.6)
        page.line(_LARGEUR - _MARGE - 42 * mm, y + 1.5 * mm, _LARGEUR - _MARGE, y + 1.5 * mm)
        y -= 1 * mm
        page.setFont("Helvetica-Bold", 9.5)
        page.drawRightString(
            _LARGEUR - _MARGE, y, f"{_sous(rubrique.sous_total)} FCFA"
        )
        y -= 5 * mm
    return y - 3 * mm


def _total(page: canvas.Canvas, total: Decimal, total_compose: Decimal, y: float) -> float:
    # ⚠️ L'air avant le cartouche appartient au cartouche, et non à ce qui le
    # précède. Une rubrique à ligne unique ne trace pas de filet de sous-total et
    # descend donc moins : sans cette marge, le cadre du total venait toucher sa
    # dernière ligne, et seulement dans ce cas-là.
    y -= 5 * mm
    page.setLineWidth(1.2)
    page.rect(_MARGE, y - 3 * mm, _LARGEUR - 2 * _MARGE, 10 * mm, stroke=1, fill=0)
    page.setFont("Helvetica-Bold", 12)
    page.drawString(_MARGE + 4 * mm, y + 0.8 * mm, "TOTAL GENERAL")
    page.drawRightString(_LARGEUR - _MARGE - 4 * mm, y + 0.8 * mm, f"{_sous(total)} FCFA")
    y -= 12 * mm

    # ⚠️ LE MONTANT EN TOUTES LETTRES : la seule protection contre un chiffre
    # altéré après signature. Un « 5 » devenu « 8 » ne se voit pas.
    page.setFont("Helvetica", 9)
    amorce = "Arrête la présente pro-forma à la somme de :  "
    page.drawString(_MARGE, y, amorce)
    # ⚠️ La largeur MESURÉE, et non un décalage écrit à la main. Le décalage fixe
    # de 62 mm chevauchait les deux-points, et n'aurait plus rien voulu dire le
    # jour où l'amorce change d'un mot.
    page.setFont("Helvetica-Bold", 10)
    lettres = en_toutes_lettres(total).replace("-", " ")
    page.drawString(
        _MARGE + page.stringWidth(amorce, "Helvetica", 9),
        y,
        f"{lettres.capitalize()} francs FCFA.",
    )
    y -= 7 * mm

    # ⚠️ L'ÉCART EST IMPRIMÉ, JAMAIS TU. Le responsable peut s'écarter du barème,
    # motif à l'appui ; le client doit pouvoir lire que le total qu'il accepte
    # n'est pas celui que les lignes additionnent.
    if total != total_compose:
        page.setFont("Helvetica-Oblique", 8.5)
        page.setFillColor(_MAGENTA)
        page.drawString(
            _MARGE,
            y,
            f"Montant arrêté par le cabinet. Le détail ci-dessus totalise "
            f"{_sous(total_compose)} FCFA.",
        )
        page.setFillColor(black)
        y -= 6 * mm
    return y


def _pied(page: canvas.Canvas) -> None:
    bas = 22 * mm
    page.setFillColor(_INDIGO)
    page.rect(0, 0, _LARGEUR, bas, stroke=0, fill=1)
    page.setFillColor(white)
    page.setFont("Helvetica", 7.5)
    y = bas - 5 * mm
    for ligne in _PIED:
        page.drawCentredString(_LARGEUR / 2, y, ligne)
        y -= 4.2 * mm


def _couper(
    page: canvas.Canvas, texte: str, police: str, taille: float, largeur: float
) -> list[str]:
    """Coupe le texte pour tenir dans `largeur` points, aux espaces seulement.

    ⚠️ **À LA MESURE, ET NON AU COMPTE DE CARACTÈRES.** Le premier jet coupait
    au-delà de 95 caractères : le sous-titre de la SARL sans suivi en fait 97, et
    le document laissait « FCFA » seul sur une deuxième ligne. Une largeur en
    caractères suppose une police à chasse fixe, ce qu'Helvetica n'est pas — un
    « i » et un « M » n'y occupent pas la même place.

    ⚠️ Jamais au milieu d'un mot : un libellé tronqué au caractère près se lit
    mal et fait douter du reste du document.

    ⚠️ **LES LIGNES SONT ÉQUILIBRÉES.** Sans cela, un texte qui dépasse d'un mot
    laisse ce mot seul en veuve, ce qui se voit immédiatement sur un document
    centré. On cherche donc la plus petite largeur qui tient toujours dans le
    même nombre de lignes, et on recoupe avec elle.
    """
    def decouper(maximum: float) -> list[str]:
        lignes: list[str] = []
        courante = ""
        for mot in texte.split():
            essai = f"{courante} {mot}".strip()
            if page.stringWidth(essai, police, taille) > maximum and courante:
                lignes.append(courante)
                courante = mot
            else:
                courante = essai
        if courante:
            lignes.append(courante)
        return lignes

    lignes = decouper(largeur)
    if len(lignes) < 2:
        return lignes

    # La largeur idéale si le texte se répartissait exactement. On remonte
    # ensuite jusqu'à retrouver le même nombre de lignes.
    entier = page.stringWidth(texte, police, taille)
    cible = entier / len(lignes)
    while cible < largeur:
        essai = decouper(cible)
        if len(essai) <= len(lignes):
            return essai
        cible += largeur / 40
    return lignes
