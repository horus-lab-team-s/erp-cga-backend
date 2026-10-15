"""Les modèles « MODELE FOURNISSEURS » et « MODELE TIERS » de la DGI, remplis (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI LE XML, ET PAS OPENPYXL

Les deux modèles portent le logo de la DGI (une image) : openpyxl ne le réécrit pas. Le
fichier est donc recopié octet pour octet, sauf la feuille, réécrite ligne à ligne, et
la chaîne de calcul, qui suit la ligne TOTAL.

PLUS DE SEIZE TIERS
Le modèle offre seize lignes (14 à 29) puis TOTAL. Au-delà, les lignes s'ajoutent dans
le même style et TOTAL descend : ses formules couvrent toutes les lignes, sa fusion
A:G le suit, et sa valeur est calculée ici pour qu'un lecteur qui ne recalcule pas lise
la bonne somme.

⚠️ LE NOM DU FICHIER EST CELUI DU MODÈLE : le portail refuse un fichier renommé (guide
DGI, « Nom de fichier incorrect »).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape

from app.contextes.comptabilite.domaine.declaration_par_tiers import LigneParTiers

__all__ = ["MODELES", "FicheDeclaree", "remplir_la_declaration_par_tiers"]

_DOSSIER = Path(__file__).resolve().parent / "modeles_dgi"
#: Nature → nom exact du modèle, qui est aussi le nom du fichier rendu.
MODELES = {"fournisseurs": "MODELE FOURNISSEURS.xlsx", "tiers": "MODELE TIERS.xlsx"}
_FEUILLE = "xl/worksheets/sheet1.xml"
_PREMIERE, _LIGNES_DU_MODELE = 14, 16
_MONTANTS = "HIJKLMNO"


@dataclass(frozen=True)
class FicheDeclaree:
    denomination: str
    niu: str | None
    pays: str | None = "CAMEROUN"


@cache
def _modele(nom: str) -> bytes:
    return (_DOSSIER / nom).read_bytes()


def _texte(colonne: str, ligne: int, style: str, valeur: str | None) -> str:
    if not valeur:
        return f'<c r="{colonne}{ligne}" s="{style}"/>'
    return (
        f'<c r="{colonne}{ligne}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
        f"{escape(valeur)}</t></is></c>"
    )


def _nombre(colonne: str, ligne: int, style: str, valeur: Decimal | int) -> str:
    return f'<c r="{colonne}{ligne}" s="{style}"><v>{int(valeur)}</v></c>'


def _montants(x: LigneParTiers) -> list[Decimal]:
    return [
        x.ouverture.ht,
        x.ouverture.tva,
        x.facturation.ht,
        x.facturation.tva,
        x.paiement.ht,
        x.paiement.tva,
        x.cloture.ht,
        x.cloture.tva,
    ]


def remplir_la_declaration_par_tiers(
    nature: str, lignes: list[LigneParTiers], fiches: dict[str, FicheDeclaree]
) -> bytes:
    """Le modèle officiel de la nature (« fournisseurs » ou « tiers »), une ligne par tiers."""
    source = zipfile.ZipFile(io.BytesIO(_modele(MODELES[nature])))
    xml = source.read(_FEUILLE).decode()

    def ligne_xml(numero: int) -> str:
        m = re.search(rf'<row r="{numero}"[^>]*>.*?</row>', xml, re.S)
        if m is None:
            raise RuntimeError(f"la ligne {numero} manque au modèle {MODELES[nature]}")
        return m.group(0)

    gabarit = ligne_xml(_PREMIERE)
    entete_de_ligne = re.match(r"<row\b[^>]*>", gabarit).group(0)
    style = re.search(r'<c r="A14" s="(\d+)"', gabarit).group(1)
    total_xml = ligne_xml(_PREMIERE + _LIGNES_DU_MODELE)
    style_total_texte = re.search(r'<c r="A30" s="(\d+)"', total_xml).group(1)
    style_total_fin = re.search(r'<c r="G30" s="(\d+)"', total_xml).group(1)
    style_total = re.search(r'<c r="H30" s="(\d+)"', total_xml).group(1)
    libelle_total = re.search(r'<c r="A30"[^>]*>(<v>\d+</v>)</c>', total_xml).group(1)

    nombre = max(_LIGNES_DU_MODELE, len(lignes))
    t = _PREMIERE + nombre  # la ligne TOTAL
    decalage = t - (_PREMIERE + _LIGNES_DU_MODELE)
    totaux = [Decimal(0)] * len(_MONTANTS)

    rangees = []
    for i in range(nombre):
        r = _PREMIERE + i
        cellules = []
        if i < len(lignes):
            x = lignes[i]
            fiche = fiches.get(x.tiers) or FicheDeclaree(denomination="", niu=x.tiers)
            cellules += [
                _nombre("A", r, style, i + 1),
                _texte("B", r, style, fiche.niu),
                _texte("C", r, style, fiche.denomination),
                _texte("D", r, style, None),
                _texte("E", r, style, None),
                _texte("F", r, style, fiche.pays),
                _texte("G", r, style, None),
            ]
            for j, (colonne, montant) in enumerate(zip(_MONTANTS, _montants(x), strict=True)):
                cellules.append(_nombre(colonne, r, style, montant))
                totaux[j] += montant
        else:
            cellules = [f'<c r="{c}{r}" s="{style}"/>' for c in "ABCDEFG" + _MONTANTS]
        rangees.append(
            re.sub(r'r="\d+"', f'r="{r}"', entete_de_ligne, count=1) + "".join(cellules) + "</row>"
        )

    derniere = t - 1
    total = (
        re.sub(r'r="\d+"', f'r="{t}"', re.match(r"<row\b[^>]*>", total_xml).group(0), count=1)
        + f'<c r="A{t}" s="{style_total_texte}" t="s">{libelle_total}</c>'
        + "".join(f'<c r="{c}{t}" s="{style_total_texte}"/>' for c in "BCDEF")
        + f'<c r="G{t}" s="{style_total_fin}"/>'
        + "".join(
            f'<c r="{c}{t}" s="{style_total}"><f>SUM({c}{_PREMIERE}:{c}{derniere})</f>'
            f"<v>{int(v)}</v></c>"
            for c, v in zip(_MONTANTS, totaux, strict=True)
        )
        + "</row>"
    )

    debut = xml.index(f'<row r="{_PREMIERE}"')
    fin = xml.index("</row>", xml.index(f'<row r="{_PREMIERE + _LIGNES_DU_MODELE}"')) + len(
        "</row>"
    )
    suite = re.sub(
        r'(<row r="|<c r="[A-Z]+)(\d+)"',
        lambda m: f'{m.group(1)}{int(m.group(2)) + decalage}"',
        xml[fin:].split("</sheetData>", 1)[0],
    )
    reste = "</sheetData>" + xml[fin:].split("</sheetData>", 1)[1]
    xml = xml[:debut] + "".join(rangees) + total + suite + reste
    xml = xml.replace('<mergeCell ref="A30:G30"/>', f'<mergeCell ref="A{t}:G{t}"/>')
    xml = re.sub(
        r'<dimension ref="A1:O(\d+)"/>',
        lambda m: f'<dimension ref="A1:O{int(m.group(1)) + decalage}"/>',
        xml,
    )
    chaine = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f'<c r="{c}{t}" i="1"/>' for c in _MONTANTS)
        + "</calcChain>"
    )

    remplacees = {_FEUILLE: xml.encode(), "xl/calcChain.xml": chaine.encode()}
    sortie = io.BytesIO()
    with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        for entree in source.infolist():
            cible.writestr(entree, remplacees.get(entree.filename) or source.read(entree.filename))
    return sortie.getvalue()
