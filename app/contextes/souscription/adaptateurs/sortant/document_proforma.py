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
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.contextes.souscription.domaine.composition_proforma import CompositionProforma
from app.partage.formats import ESPACE_FINE, en_toutes_lettres
from app.partage.formats import montant as _montant_ecran
from app.partage.marque import LOGO

__all__ = ["rendre_le_document"]


def montant_imprime(valeur: Decimal) -> str:
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
MARGE = 20 * mm

#: La charte. Les mêmes valeurs que les courriels (`gabarits_courriel.py`) :
#: un client qui reçoit le message puis ouvre le document doit voir la même
#: maison.
INDIGO = HexColor("#3B2A6B")
_MAGENTA = HexColor("#8C2D86")
#: Le gris des mentions secondaires, assez foncé pour rester lisible à
#: l'impression en noir et blanc, que le cabinet pratique.
GRIS = HexColor("#555555")

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

    y = entete_de_document(page)
    y = _titre(page, composition, y)
    y = _identification(page, numero, version, emise_le, client, y)
    for rubrique in composition.rubriques:
        y = _rubrique(page, rubrique, y)
    total = composition.total if total_arrete is None else total_arrete
    y = _total(page, total, composition.total, y)
    if composition.a_une_suite:
        _suite(page, composition, y)
    pied_de_document(page)
    page.showPage()
    page.save()
    return tampon.getvalue()


#: Le logo de la maison. Lu ici dans une variable du module, pour qu'un essai
#: puisse éprouver le rendu sans lui.
_LOGO = LOGO
#: La place qu'il occupe à gauche du bandeau. Ses proportions sont 16 / 9.
_LARGEUR_LOGO = 30 * mm


def entete_de_document(page: canvas.Canvas) -> float:
    """Le logo à gauche, le bandeau à sa droite : l'en-tête des documents papier.

    ⚠️ **LE LOGO MANQUAIT, ET LE CABINET L'A RELEVÉ** (14 octobre 2026). Un
    document sans la marque de la maison se lit comme un brouillon, et c'est lui
    que le client compare au papier qu'on lui remettait jusque-là.

    ⚠️ **SON ABSENCE NE FAIT PAS ÉCHOUER LE RENDU.** Une image mal construite, où
    le fichier manquerait, doit encore émettre des proformas : le bandeau reprend
    alors toute la largeur, comme avant.
    """
    haut = _HAUTEUR - 14 * mm
    gauche = 0.0
    if _LOGO.is_file():
        page.drawImage(
            ImageReader(str(_LOGO)),
            MARGE - 6 * mm,
            haut - 15.5 * mm,
            width=_LARGEUR_LOGO,
            height=_LARGEUR_LOGO * 162 / 288,
            preserveAspectRatio=True,
            mask=None,
        )
        gauche = MARGE - 6 * mm + _LARGEUR_LOGO + 4 * mm
    page.setFillColor(INDIGO)
    page.rect(gauche, haut - 14 * mm, _LARGEUR - gauche, 14 * mm, stroke=0, fill=1)
    page.setFillColor(white)
    page.setFont("Helvetica-Bold", 16)
    page.drawString(max(gauche + 5 * mm, MARGE), haut - 6 * mm, "CGA BROAD RANGE CONSULTING")
    page.setFont("Helvetica", 7 if gauche else 7.5)
    page.drawString(
        max(gauche + 5 * mm, MARGE),
        haut - 11 * mm,
        "ASSISTANCE COMPTABLE ET FISCALE  ·  ASSISTANCE JURIDIQUE  ·  FORMATIONS"
        "  ·  PROCÉDURES ADMINISTRATIVES",
    )
    return haut - 26 * mm


def _titre(page: canvas.Canvas, composition: CompositionProforma, y: float) -> float:
    page.setFillColor(HexColor("#EDE7F3"))
    page.rect(MARGE, y - 2 * mm, _LARGEUR - 2 * MARGE, 8 * mm, stroke=0, fill=1)
    page.setFillColor(black)
    page.setFont("Helvetica-Bold", 11.5)
    page.drawCentredString(_LARGEUR / 2, y + 0.6 * mm, composition.titre)
    y -= 10 * mm
    page.setFont("Helvetica-Bold", 8.5)
    for ligne in _couper(
        page, composition.libelle, "Helvetica-Bold", 8.5, _LARGEUR - 2 * MARGE
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
    page.setFillColor(GRIS)
    page.drawString(MARGE, y, f"N° {numero}   ·   version {version}   ·   {emise_le:%d/%m/%Y}")
    page.drawRightString(_LARGEUR - MARGE, y, f"Établie pour : {client}")
    page.setFillColor(black)
    return y - 8 * mm


def _rubrique(page: canvas.Canvas, rubrique, y: float) -> float:
    page.setFont("Helvetica-Bold", 10)
    page.drawCentredString(_LARGEUR / 2, y, f"{rubrique.numero}.   {rubrique.intitule}")
    y -= 6.5 * mm
    page.setFont("Helvetica", 9.5)
    for ligne in rubrique.lignes:
        page.drawString(MARGE + 4 * mm, y, f"•  {ligne.libelle}")
        page.drawRightString(_LARGEUR - MARGE, y, f"{montant_imprime(ligne.montant)} FCFA")
        y -= 5.2 * mm
    # ⚠️ Le sous-total se calcule, il n'est pas au référentiel : une seconde
    # vérité à côté des lignes finirait par en diverger.
    if len(rubrique.lignes) > 1:
        # ⚠️ Le filet est AU-DESSUS du sous-total, avec de l'air. Il passait à
        # 2,5 mm de la ligne de base, c'est-à-dire au travers des chiffres : le
        # sous-total se lisait barré, donc annulé.
        page.setLineWidth(0.6)
        page.line(_LARGEUR - MARGE - 42 * mm, y + 2.6 * mm, _LARGEUR - MARGE, y + 2.6 * mm)
        y -= 2.2 * mm
        page.setFont("Helvetica-Bold", 9.5)
        page.drawRightString(
            _LARGEUR - MARGE, y, f"{montant_imprime(rubrique.sous_total)} FCFA"
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
    page.rect(MARGE, y - 3 * mm, _LARGEUR - 2 * MARGE, 10 * mm, stroke=1, fill=0)
    page.setFont("Helvetica-Bold", 12)
    page.drawString(MARGE + 4 * mm, y + 0.8 * mm, "TOTAL GENERAL")
    page.drawRightString(_LARGEUR - MARGE - 4 * mm, y + 0.8 * mm, f"{montant_imprime(total)} FCFA")
    y -= 12 * mm

    # ⚠️ LE MONTANT EN TOUTES LETTRES : la seule protection contre un chiffre
    # altéré après signature. Un « 5 » devenu « 8 » ne se voit pas.
    page.setFont("Helvetica", 9)
    amorce = "Arrête la présente pro-forma à la somme de :  "
    page.drawString(MARGE, y, amorce)
    # ⚠️ La largeur MESURÉE, et non un décalage écrit à la main. Le décalage fixe
    # de 62 mm chevauchait les deux-points, et n'aurait plus rien voulu dire le
    # jour où l'amorce change d'un mot.
    page.setFont("Helvetica-Bold", 10)
    lettres = en_toutes_lettres(total).replace("-", " ")
    page.drawString(
        MARGE + page.stringWidth(amorce, "Helvetica", 9),
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
            MARGE,
            y,
            f"Montant arrêté par le cabinet. Le détail ci-dessus totalise "
            f"{montant_imprime(total_compose)} FCFA.",
        )
        page.setFillColor(black)
        y -= 6 * mm
    return y


#: Sous cette hauteur, on tourne la page : le pied occupe 22 mm.
_PLANCHER = 30 * mm


def _suite(page: canvas.Canvas, composition: CompositionProforma, y: float) -> None:
    """Ce que le client apporte, ce qu'il reçoit, en combien de temps.

    ⚠️ **CHAQUE BLOC TIENT ENSEMBLE OU PASSE À LA PAGE SUIVANTE.** Un titre laissé
    seul en bas de page, sa liste en haut de la suivante, fait croire que la liste
    est vide. La hauteur d'un bloc est donc mesurée avant d'être tracée.
    """
    largeur = _LARGEUR - 2 * MARGE

    def placer(y: float, hauteur: float) -> float:
        if y - hauteur >= _PLANCHER:
            return y
        pied_de_document(page)
        page.showPage()
        return entete_de_document(page)

    def liste(titre: str, elements: tuple[str, ...], y: float, *, numerotee: bool) -> float:
        if not elements:
            return y
        retrait = 7 * mm
        blocs = [
            _couper(page, texte, "Helvetica", 9.5, largeur - retrait, equilibrer=False)
            for texte in elements
        ]
        hauteur = 9 * mm + sum(len(b) for b in blocs) * 4.8 * mm + len(blocs) * 1 * mm
        y = placer(y - 4 * mm, hauteur)
        page.setFillColor(black)
        page.setFont("Helvetica-Bold", 10)
        page.drawCentredString(_LARGEUR / 2, y, titre)
        y -= 7 * mm
        page.setFont("Helvetica", 9.5)
        for rang, lignes in enumerate(blocs, start=1):
            page.drawString(MARGE, y, f"{rang}." if numerotee else "•")
            for ligne in lignes:
                page.drawString(MARGE + retrait, y, ligne)
                y -= 4.8 * mm
            y -= 1 * mm
        return y - 2 * mm

    y = liste("DOCUMENTS A FOURNIR", composition.documents_a_fournir, y, numerotee=True)
    y = liste("DOCUMENTS A RECEVOIR", composition.documents_a_recevoir, y, numerotee=False)

    if composition.delai_d_execution:
        lignes = _couper(
            page, composition.delai_d_execution, "Helvetica", 9.5, largeur, equilibrer=False
        )
        y = placer(y - 4 * mm, 9 * mm + len(lignes) * 4.8 * mm)
        page.setFont("Helvetica-Bold", 10)
        page.drawString(MARGE, y, "DELAI D'EXECUTION ET REGLEMENT")
        y -= 6.5 * mm
        page.setFont("Helvetica", 9.5)
        for ligne in lignes:
            page.drawString(MARGE, y, ligne)
            y -= 4.8 * mm


def pied_de_document(page: canvas.Canvas) -> None:
    bas = 22 * mm
    page.setFillColor(INDIGO)
    page.rect(0, 0, _LARGEUR, bas, stroke=0, fill=1)
    page.setFillColor(white)
    page.setFont("Helvetica", 7.5)
    y = bas - 5 * mm
    for ligne in _PIED:
        page.drawCentredString(_LARGEUR / 2, y, ligne)
        y -= 4.2 * mm


def _couper(
    page: canvas.Canvas,
    texte: str,
    police: str,
    taille: float,
    largeur: float,
    *,
    equilibrer: bool = True,
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
    # ⚠️ L'équilibrage sert un texte CENTRÉ. Sur un paragraphe aligné à gauche, il
    # raccourcirait toutes les lignes pour rien : la liste des pièces se couperait
    # aux deux tiers de la page.
    if len(lignes) < 2 or not equilibrer:
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
