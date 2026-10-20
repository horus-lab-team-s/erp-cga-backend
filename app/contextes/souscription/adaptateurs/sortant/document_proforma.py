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

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.contextes.souscription.domaine.composition_proforma import CompositionProforma
from app.partage.formats import ESPACE_FINE, en_toutes_lettres
from app.partage.formats import montant as _montant_ecran
from app.partage.marque import LOGO

__all__ = [
    "FILET",
    "FOND",
    "GRIS",
    "INDIGO",
    "MARGE",
    "entete_de_document",
    "montant_imprime",
    "pied_de_document",
    "rendre_le_document",
]


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
#:
#: 18 octobre 2026 (points 19 et 20) : « des factures plus soft, avec de bonnes
#: séparations, comme celles des grandes entreprises ». Page blanche, filets fins, la
#: marque en accent seulement (titre, total) ; plus de bandeau plein. L'indigo est celui
#: de la console (`--brand-indigo-900`), et non plus un voisin approximatif.
INDIGO = HexColor("#2E1B4D")
_MAGENTA = HexColor("#8C2D86")
#: Le gris des mentions secondaires, assez foncé pour rester lisible à
#: l'impression en noir et blanc, que le cabinet pratique.
GRIS = HexColor("#5B556B")
#: Les filets et les fonds de cartouche : présents, jamais appuyés.
FILET = HexColor("#E2DDEA")
FOND = HexColor("#F6F4FB")

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
    """Le logo et la maison à gauche, un filet fin dessous : l'en-tête des documents.

    ⚠️ **LE LOGO MANQUAIT, ET LE CABINET L'A RELEVÉ** (14 octobre 2026). Un
    document sans la marque de la maison se lit comme un brouillon.

    ⚠️ **SON ABSENCE NE FAIT PAS ÉCHOUER LE RENDU.** Sans fichier, la raison sociale
    prend sa place, et le document s'émet quand même.

    18 octobre 2026 : le bandeau indigo plein cède la place à une page blanche ; la
    raison sociale reste écrite une fois par page.
    """
    haut = _HAUTEUR - 16 * mm
    gauche = MARGE
    if _LOGO.is_file():
        hauteur_logo = 13 * mm
        page.drawImage(
            ImageReader(str(_LOGO)),
            MARGE,
            haut - hauteur_logo,
            width=hauteur_logo * 288 / 162,
            height=hauteur_logo,
            preserveAspectRatio=True,
            mask=None,
        )
        gauche = MARGE + hauteur_logo * 288 / 162 + 5 * mm
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 11)
    page.drawString(gauche, haut - 5 * mm, "CGA BROAD RANGE CONSULTING")
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 7.5)
    page.drawString(
        gauche,
        haut - 9.5 * mm,
        "Centre de gestion agréé  ·  Comptabilité, fiscalité, juridique, formations",
    )
    page.drawRightString(_LARGEUR - MARGE, haut - 5 * mm, "www.cga-brcgroup.com")
    page.drawRightString(_LARGEUR - MARGE, haut - 9.5 * mm, "contact@cga-brcgroup.com")
    page.setStrokeColor(FILET)
    page.setLineWidth(0.8)
    page.line(MARGE, haut - 16 * mm, _LARGEUR - MARGE, haut - 16 * mm)
    page.setStrokeColor(black)
    page.setFillColor(black)
    return haut - 26 * mm


def _titre(page: canvas.Canvas, composition: CompositionProforma, y: float) -> float:
    """Le type de document en grand, à gauche ; ce qu'il propose, dessous."""
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 17)
    page.drawString(MARGE, y, composition.titre)
    y -= 7 * mm
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 9)
    for ligne in _couper(page, composition.libelle, "Helvetica", 9, _LARGEUR - 2 * MARGE):
        page.drawString(MARGE, y, ligne)
        y -= 4.6 * mm
    page.setFillColor(black)
    return y - 3 * mm


def _identification(
    page: canvas.Canvas,
    numero: str,
    version: int,
    emise_le: datetime,
    client: str,
    y: float,
) -> float:
    """Le numéro, la version et le client, en deux colonnes. **Absents des documents papier.**

    ⚠️ Les proformas du cabinet n'en portent pas : elles sont établies à la main,
    une par client. Un document émis par un système doit se désigner lui-même, sans
    quoi deux propositions successives au même client ne se distinguent que par leur
    date.
    """
    milieu = _LARGEUR / 2 + 10 * mm
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 7)
    page.drawString(MARGE, y, "ÉTABLIE POUR")
    page.drawString(milieu, y, "RÉFÉRENCE")
    page.setFillColor(black)
    page.setFont("Helvetica-Bold", 10)
    page.drawString(MARGE, y - 5 * mm, client[:60])
    page.setFont("Helvetica", 9)
    page.drawString(milieu, y - 5 * mm, f"N° {numero}   ·   version {version}")
    page.setFillColor(GRIS)
    page.drawString(milieu, y - 9.5 * mm, f"Émise le {emise_le:%d/%m/%Y}")
    page.setFillColor(black)
    return y - 17 * mm


def _rubrique(page: canvas.Canvas, rubrique, y: float) -> float:
    """Une rubrique : son intitulé en petites capitales sur un filet, ses lignes dessous."""
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(MARGE, y, f"{rubrique.numero}.  {rubrique.intitule.upper()}")
    page.setStrokeColor(FILET)
    page.setLineWidth(0.6)
    page.line(MARGE, y - 2 * mm, _LARGEUR - MARGE, y - 2 * mm)
    page.setStrokeColor(black)
    page.setFillColor(black)
    y -= 6.5 * mm
    page.setFont("Helvetica", 9.5)
    for ligne in rubrique.lignes:
        page.drawString(MARGE + 2 * mm, y, ligne.libelle)
        page.drawRightString(_LARGEUR - MARGE, y, f"{montant_imprime(ligne.montant)} FCFA")
        y -= 5.2 * mm
    # ⚠️ Le sous-total se calcule, il n'est pas au référentiel : une seconde
    # vérité à côté des lignes finirait par en diverger.
    if len(rubrique.lignes) > 1:
        # ⚠️ Le filet est AU-DESSUS du sous-total, avec de l'air : passé au travers des
        # chiffres, il faisait lire le sous-total barré, donc annulé.
        page.setStrokeColor(FILET)
        page.setLineWidth(0.6)
        page.line(_LARGEUR - MARGE - 42 * mm, y + 2.6 * mm, _LARGEUR - MARGE, y + 2.6 * mm)
        page.setStrokeColor(black)
        y -= 2.2 * mm
        page.setFont("Helvetica-Bold", 9.5)
        page.drawString(_LARGEUR - MARGE - 70 * mm, y, "Sous-total")
        page.drawRightString(_LARGEUR - MARGE, y, f"{montant_imprime(rubrique.sous_total)} FCFA")
        y -= 5 * mm
    return y - 3 * mm


def _total(page: canvas.Canvas, total: Decimal, total_compose: Decimal, y: float) -> float:
    # ⚠️ L'air avant le cartouche appartient au cartouche, et non à ce qui le
    # précède. Une rubrique à ligne unique ne trace pas de filet de sous-total et
    # descend donc moins : sans cette marge, le cadre du total venait toucher sa
    # dernière ligne, et seulement dans ce cas-là.
    y -= 5 * mm
    # 18 octobre 2026 : un cartouche à fond clair, sans cadre noir ; le montant porte
    # seul la couleur de la maison.
    page.setFillColor(FOND)
    page.roundRect(MARGE, y - 3.5 * mm, _LARGEUR - 2 * MARGE, 11 * mm, 2 * mm, stroke=0, fill=1)
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 8.5)
    page.drawString(MARGE + 5 * mm, y + 0.6 * mm, "TOTAL GENERAL")
    page.setFillColor(INDIGO)
    page.setFont("Helvetica-Bold", 13)
    page.drawRightString(_LARGEUR - MARGE - 5 * mm, y + 0.4 * mm, f"{montant_imprime(total)} FCFA")
    page.setFillColor(black)
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


def _intitule_de_section(page: canvas.Canvas, titre: str, y: float) -> None:
    """Un intitulé en petites capitales grises, posé sur un filet : la séparation des
    documents de la maison (18 octobre 2026)."""
    page.setFillColor(GRIS)
    page.setFont("Helvetica-Bold", 7.5)
    page.drawString(MARGE, y, titre)
    page.setStrokeColor(FILET)
    page.setLineWidth(0.6)
    page.line(MARGE, y - 2 * mm, _LARGEUR - MARGE, y - 2 * mm)
    page.setStrokeColor(black)
    page.setFillColor(black)


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
        _intitule_de_section(page, titre, y)
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
        _intitule_de_section(page, "DELAI D'EXECUTION ET REGLEMENT", y)
        y -= 6.5 * mm
        page.setFont("Helvetica", 9.5)
        for ligne in lignes:
            page.drawString(MARGE, y, ligne)
            y -= 4.8 * mm


def pied_de_document(page: canvas.Canvas) -> None:
    """Un filet fin, puis les coordonnées en petit gris : le pied des documents."""
    bas = 22 * mm
    page.setStrokeColor(FILET)
    page.setLineWidth(0.6)
    page.line(MARGE, bas, _LARGEUR - MARGE, bas)
    page.setStrokeColor(black)
    page.setFillColor(GRIS)
    page.setFont("Helvetica", 7)
    y = bas - 5 * mm
    for ligne in _PIED:
        page.drawCentredString(_LARGEUR / 2, y, ligne)
        y -= 3.8 * mm
    page.setFillColor(black)


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
