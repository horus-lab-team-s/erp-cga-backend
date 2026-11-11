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
from app.contextes.cloture.domaine.etats_smt import LIGNES_ACTIF, LIGNES_PASSIF, EtatsSmt

__all__ = [
    "NOM_DU_MODELE",
    "NOM_DU_MODELE_SMT",
    "ModeleIncompatible",
    "remplir_la_dsf_normal",
    "remplir_la_dsf_smt",
]

MODELE = Path(__file__).parent / "modeles_dgi" / "DSF_Normal_DGIFORMAT_VERROUILLEVF.xlsx"
#: Le nom sous lequel le fichier se téléverse : celui du modèle, sans rien ajouter.
NOM_DU_MODELE = MODELE.name
#: 11 novembre 2026 : le modèle du Système minimal de trésorerie (adhérents à l'IGS).
MODELE_SMT = Path(__file__).parent / "modeles_dgi" / "DSF_SMT_VEROUILLE-09-03-2021.xlsx"
NOM_DU_MODELE_SMT = MODELE_SMT.name

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_BILAN = "BILAN PAYSAGE"
_RESULTAT = "COMPTE DE RESULTAT"


class ModeleIncompatible(RuntimeError):
    """Le modèle ne porte pas une cellule ou un code que le remplissage attend."""


@cache
def _archive(modele: Path = MODELE) -> bytes:
    return modele.read_bytes()


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


# ── Le Système minimal de trésorerie (11 novembre 2026) ───────────────────────────
#
# La feuille « MOD BILAN » : un bilan de neuf lignes, un compte de résultat de trésorerie.
# Les totaux (lignes 16, 26, 33, 34) sont des FORMULES du modèle : on ne les remplace pas,
# mais on met à jour la valeur qu'elles gardent en cache (le portail lit la cellule, pas la
# formule), et le classeur demande un recalcul complet à l'ouverture. Le résultat G
# (ligne 42) n'a pas de formule dans le modèle : il s'écrit.

_MOD_BILAN = "MOD BILAN"
_LIGNE_ACTIF = dict(zip(LIGNES_ACTIF, range(11, 16), strict=True))
_LIGNE_PASSIF = dict(zip(LIGNES_PASSIF, range(11, 15), strict=True))
_LIGNE_RESULTAT = {
    "recettes_ventes": 24,
    "autres_recettes": 25,
    "depenses_achats": 27,
    "depenses_loyers": 28,
    "depenses_salaires": 29,
    "depenses_impots": 30,
    "charges_interets": 31,
    "autres_depenses": 32,
    "variation_stocks": 36,
    "variation_creances": 37,
    "variation_dettes": 38,
    "dotations": 40,
}


def _valeurs_smt(etats: EtatsSmt, colonnes: tuple[str, str, str]) -> dict[str, Decimal]:
    """Les cellules de saisie : `colonnes` = (actif, passif, résultat)."""
    actif, passif, resultat = colonnes
    valeurs: dict[str, Decimal] = {}
    for ligne, rang in _LIGNE_ACTIF.items():
        valeurs[f"{actif}{rang}"] = etats.actif[ligne]
    for ligne, rang in _LIGNE_PASSIF.items():
        valeurs[f"{passif}{rang}"] = etats.passif[ligne]
    for ligne, rang in _LIGNE_RESULTAT.items():
        valeurs[f"{resultat}{rang}"] = etats.resultat[ligne]
    valeurs[f"{resultat}42"] = etats.resultat_g
    return valeurs


def _totaux_smt(etats: EtatsSmt, colonnes: tuple[str, str, str]) -> dict[str, Decimal]:
    """Les valeurs en cache des formules du modèle."""
    actif, passif, resultat = colonnes
    return {
        f"{actif}16": etats.total_actif,
        f"{passif}16": etats.total_passif,
        f"{resultat}26": etats.total_recettes,
        f"{resultat}33": etats.total_depenses,
        f"{resultat}34": etats.solde_de_tresorerie,
    }


def _mettre_en_cache(xml: str, valeurs: dict[str, Decimal]) -> str:
    """La valeur gardée par une cellule à formule, la formule intacte."""
    for coordonnee, valeur in valeurs.items():
        motif = re.compile(rf'<c r="{coordonnee}"(?P<attrs>[^>]*?)(?:/>|>(?P<corps>.*?)</c>)', re.S)
        m = motif.search(xml)
        if m is None or "<f" not in (m.group("corps") or ""):
            raise ModeleIncompatible(f"la cellule {coordonnee} n'est pas une formule du modèle")
        corps = re.sub(r"<v>.*?</v>", "", m.group("corps"), flags=re.S)
        cellule = f'<c r="{coordonnee}"{m.group("attrs")}>{corps}<v>{_nombre(valeur)}</v></c>'
        xml = xml[: m.start()] + cellule + xml[m.end() :]
    return xml


def remplir_la_dsf_smt(
    etats: EtatsSmt,
    *,
    precedents: EtatsSmt | None,
    denomination: str,
    niu: str,
    cloture: date,
    duree_en_mois: int,
) -> bytes:
    """Le modèle officiel « DSF SMT », bilan et compte de résultat remplis.

    Refuse des états dont G ne retombe pas sur le résultat comptable, ou déséquilibrés :
    l'appelant les a déjà écartés, ceci est le dernier verrou."""
    if not etats.equilibre or not etats.concorde:
        raise ModeleIncompatible("états SMT incohérents : le fichier ne sort pas.")
    source = zipfile.ZipFile(io.BytesIO(_archive(MODELE_SMT)))
    chemins = _chemins_des_feuilles(source)
    if _MOD_BILAN not in chemins:
        raise ModeleIncompatible(f"la feuille « {_MOD_BILAN} » manque au modèle")
    feuille = source.read(chemins[_MOD_BILAN]).decode()
    valeurs: dict[str, Decimal | str] = {
        "A4": f"Désignation entité : {denomination}",
        "A5": f"Numéro d'identification : {niu}",
        "G4": f"Exercice clos le {cloture:%d/%m/%Y}",
        "G5": f"Durée (en mois) : {duree_en_mois}",
        "A7": f"BILAN SMT AU {cloture:%d/%m/%Y}",
        "A20": f"COMPTE DE RESULTAT SMT AU {cloture:%d/%m/%Y}",
        **_valeurs_smt(etats, ("E", "J", "E")),
    }
    totaux = _totaux_smt(etats, ("E", "J", "E"))
    if precedents is not None:
        valeurs |= _valeurs_smt(precedents, ("F", "K", "F"))
        totaux |= _totaux_smt(precedents, ("F", "K", "F"))
    feuille = _mettre_en_cache(_ecrire(feuille, valeurs), totaux)

    # Le classeur se recalcule entièrement à l'ouverture : les totaux affichés suivent.
    classeur = source.read("xl/workbook.xml").decode()
    classeur = re.sub(
        r"<calcPr\b([^>]*?)\s*/>", r'<calcPr\1 fullCalcOnLoad="1"/>', classeur, count=1
    )

    remplacees = {chemins[_MOD_BILAN]: feuille.encode(), "xl/workbook.xml": classeur.encode()}
    sortie = io.BytesIO()
    with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as cible:
        for entree in source.infolist():
            contenu = remplacees.get(entree.filename) or source.read(entree.filename)
            cible.writestr(entree, contenu)
    return sortie.getvalue()
