"""La DSF au format DGI : le modèle Excel officiel, rempli (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA DGI ATTEND (guide utilisateur DSF 2025, § 9)

Le dépôt recommandé est le téléversement du fichier Excel « format DGI » : le portail
lit les cellules du modèle et remplit lui-même les pages en ligne. Le fichier doit
rester le modèle officiel, sous son nom, sans être renommé.

⚠️ POURQUOI ON ÉCRIT DANS LE XML, ET NON AVEC UN TABLEUR

Le modèle compte 74 feuilles, protégées, dont certaines s'étendent jusqu'à la colonne
XFD. Le recharger et le réécrire entièrement avec openpyxl coûterait des centaines de
mégaoctets par requête et réécrirait 74 feuilles dont le portail attend la forme
exacte. Ici, on n'ouvre que l'archive : les cellules visées reçoivent leur valeur,
tout le reste est recopié octet pour octet.

⚠️ LES LIGNES SE TROUVENT PAR LEUR CODE REF, lu dans le modèle lui-même (colonne A ou
H) : si la DGI déplace une ligne dans une version suivante, le remplissage suit. Un
code attendu absent du modèle lève une erreur, plutôt que d'écrire à côté.

CE QUI EST REMPLI : le bilan (brut, amortissements, net, net N-1 ; passif N et N-1) et
le compte de résultat (N, N-1), totaux et soldes intermédiaires compris, ainsi que la
désignation et le NIU en tête de ces deux pages. Les fiches R1 à R3, les notes et les
annexes restent à compléter sur le portail : leurs cellules de saisie ne se déduisent
pas sans risque du modèle seul.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from app.contextes.cloture.domaine.etats_dgi import EtatsDgi

__all__ = ["NOM_DU_MODELE", "ModeleIncompatible", "remplir_la_dsf_normal"]

MODELE = Path(__file__).parent / "modeles_dgi" / "DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx"
#: Le nom sous lequel le fichier se téléverse : celui du modèle, sans rien ajouter.
NOM_DU_MODELE = MODELE.name

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_BILAN = "BILAN PAYSAGE"
_RESULTAT = "COMPTE DE RESULTAT"


class ModeleIncompatible(RuntimeError):
    """Le modèle ne porte pas une cellule ou un code que le remplissage attend."""


@cache
def _archive() -> bytes:
    return MODELE.read_bytes()


def _chemins_des_feuilles(z: zipfile.ZipFile) -> dict[str, str]:
    classeur = z.read("xl/workbook.xml").decode()
    liens = z.read("xl/_rels/workbook.xml.rels").decode()
    relation = re.compile(
        r'<Relationship\b(?=[^>]*\bId="(?P<id>[^"]+)")(?=[^>]*\bTarget="(?P<cible>[^"]+)")[^>]*>'
    )
    cibles = {m.group("id"): m.group("cible") for m in relation.finditer(liens)}
    chemins = {}
    feuille = re.compile(
        r'<sheet\b(?=[^>]*\bname="(?P<nom>[^"]+)")(?=[^>]*\br:id="(?P<id>[^"]+)")[^>]*>'
    )
    for m in feuille.finditer(classeur):
        cible = cibles[m.group("id")].lstrip("/")
        chemins[m.group("nom")] = cible if cible.startswith("xl/") else f"xl/{cible}"
    return chemins


def _chaines(z: zipfile.ZipFile) -> list[str]:
    racine = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in racine.iter(f"{_NS}si")]


def _codes_ref(xml: str, chaines: list[str], colonne: str) -> dict[str, int]:
    """Code REF → numéro de ligne, lu dans la colonne donnée de la feuille."""
    codes = {}
    for m in re.finditer(rf'<c r="{colonne}(\d+)"[^>]*\bt="s"[^>]*><v>(\d+)</v></c>', xml):
        texte = chaines[int(m.group(2))].strip()
        if re.fullmatch(r"[A-Z]{2}", texte):
            codes[texte] = int(m.group(1))
    return codes


def _nombre(valeur: Decimal) -> str:
    return str(int(valeur.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def _ecrire(xml: str, valeurs: dict[str, Decimal | str]) -> str:
    """Pose chaque valeur dans sa cellule, en gardant son style. Le reste est inchangé."""
    for coordonnee, valeur in valeurs.items():
        motif = re.compile(rf'<c r="{coordonnee}"(?P<attrs>[^>]*?)(?:/>|>.*?</c>)', re.S)
        m = motif.search(xml)
        if m is None:
            raise ModeleIncompatible(f"la cellule {coordonnee} n'existe pas dans le modèle")
        attrs = re.sub(r'\s+t="[^"]*"', "", m.group("attrs"))
        if isinstance(valeur, Decimal):
            cellule = f'<c r="{coordonnee}"{attrs}><v>{_nombre(valeur)}</v></c>'
        else:
            cellule = (
                f'<c r="{coordonnee}"{attrs} t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(valeur)}</t></is></c>"
            )
        xml = xml[: m.start()] + cellule + xml[m.end() :]
    return xml


def remplir_la_dsf_normal(
    etats: EtatsDgi,
    *,
    precedents: EtatsDgi | None,
    denomination: str,
    niu: str,
    cloture: date,
    fiches: dict[str, dict[str, Decimal | str]] | None = None,
) -> bytes:
    """Le modèle officiel « DSF Normal », bilan et compte de résultat remplis.

    `fiches` : les fiches signalétiques R1 à R3 (feuille → cellule → valeur), voir
    `dsf_fiches_r.py`. Seules les feuilles remplies sont réécrites."""
    source = zipfile.ZipFile(io.BytesIO(_archive()))
    chemins = _chemins_des_feuilles(source)
    chaines = _chaines(source)
    entete = {
        "A4": f"Désignation entité : {denomination}",
        "A5": f"Numéro d'identification : {niu}",
    }

    bilan = source.read(chemins[_BILAN]).decode()
    a_l_actif, au_passif = _codes_ref(bilan, chaines, "A"), _codes_ref(bilan, chaines, "H")
    valeurs: dict[str, Decimal | str] = {**entete, "A6": f"BILAN AU {cloture:%d/%m/%Y}"}
    for code, ligne in etats.actif.items():
        rang = a_l_actif.get(code)
        if rang is None:
            raise ModeleIncompatible(f"le code {code} manque à l'actif du modèle")
        valeurs |= {f"D{rang}": ligne.brut, f"E{rang}": ligne.amortissements, f"F{rang}": ligne.net}
        if precedents is not None:
            valeurs[f"G{rang}"] = precedents.actif[code].net
    for code, montant in etats.passif.items():
        rang = au_passif.get(code)
        if rang is None:
            raise ModeleIncompatible(f"le code {code} manque au passif du modèle")
        valeurs[f"K{rang}"] = montant
        if precedents is not None:
            valeurs[f"L{rang}"] = precedents.passif[code]
    bilan = _ecrire(bilan, valeurs)

    resultat = source.read(chemins[_RESULTAT]).decode()
    lignes = _codes_ref(resultat, chaines, "A")
    valeurs = {**entete, "A6": f"COMPTE DE RESULTAT AU {cloture:%d/%m/%Y}"}
    for code, montant in etats.resultat.items():
        rang = lignes.get(code)
        if rang is None:
            raise ModeleIncompatible(f"le code {code} manque au compte de résultat du modèle")
        valeurs[f"E{rang}"] = montant
        if precedents is not None:
            valeurs[f"F{rang}"] = precedents.resultat[code]
    resultat = _ecrire(resultat, valeurs)

    remplacees = {chemins[_BILAN]: bilan.encode(), chemins[_RESULTAT]: resultat.encode()}
    for feuille, cellules in (fiches or {}).items():
        chemin = chemins.get(feuille)
        if chemin is None:
            raise ModeleIncompatible(f"la feuille « {feuille} » manque au modèle")
        remplacees[chemin] = _ecrire(source.read(chemin).decode(), cellules).encode()
    sortie = io.BytesIO()
    with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        for entree in source.infolist():
            contenu = remplacees.get(entree.filename) or source.read(entree.filename)
            cible.writestr(entree, contenu)
    return sortie.getvalue()
