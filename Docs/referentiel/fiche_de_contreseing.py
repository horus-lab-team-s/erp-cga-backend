#!/usr/bin/env python3
"""Édite la fiche de contreseing du référentiel normatif.

─────────────────────────────────────────────────────────────────────────────────
À QUOI SERT CE DOCUMENT, ET POURQUOI IL NE RESSEMBLE À AUCUN AUTRE

Le référentiel porte 59 paramètres dont 50 sont déclarés `VALIDE` au nom du
cabinet. Une validation qui ne peut pas être relue n'en est pas une : elle
déplace la responsabilité sans permettre de l'exercer. Cette fiche est ce qui
rend la relecture possible en une séance, par quelqu'un qui a le texte sous la
main et qui n'ouvrira pas un fichier YAML.

D'où sa forme : une ligne par paramètre, la valeur en gros, le fondement en
regard, la source consultée dessous, et une case à cocher. Le relecteur coche,
barre, annote. Ce qu'il barre redevient `A_VALIDER`.

⚠️ CE QUE LA FICHE SÉPARE, ET QUI EST TOUT SON INTÉRÊT

Les paramètres de nature `LOI` engagent un professionnel sur un texte. Ceux de
nature `POLITIQUE_CABINET` relèvent d'un arbitrage de la direction : aucun
fiscaliste n'a qualité pour les attester, et lui demander de les valider lui
ferait perdre son temps sur des lignes qui ne le concernent pas. Les deux
familles sont donc éditées séparément, avec deux blocs de signature distincts.

LA COLONNE « SOURCE CONSULTÉE » EST LA PLUS IMPORTANTE

Elle ne dit pas quel texte fait autorité — la colonne « fondement » s'en charge.
Elle dit **ce qui a été réellement ouvert** au moment de la validation. La
distinction est ce qui permet à un tiers de refaire le chemin, et de constater le
cas échéant qu'une valeur a été retenue sur une fiche de vulgarisation plutôt que
sur le code relié.

    python3 Docs/referentiel/fiche_de_contreseing.py
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
SOURCE = RACINE / "Docs" / "referentiel" / "parametres.yaml"
SORTIE_HTML = RACINE / "Docs" / "fiche-de-contreseing-referentiel.html"
SORTIE_PDF = RACINE / "Docs" / "fiche-de-contreseing-referentiel.pdf"


def e(texte: object) -> str:
    """Échappe, et écarte le tiret quadratin que le cabinet ne veut pas voir."""
    propre = str(texte).replace(" — ", ", ").replace("—", ":").replace(" – ", ", ")
    return html.escape(propre)


def formater(valeur: object, unite: str) -> str:
    if unite == "POURCENTAGE":
        return f"{valeur} %".replace(".", ",")
    if unite == "FCFA":
        return f"{int(valeur):,}".replace(",", " ") + " FCFA"
    if unite == "JOURS":
        return f"{valeur} jours"
    if unite == "JOUR_DU_MOIS":
        return f"le {valeur} du mois"
    if unite == "EXERCICES":
        return f"{valeur} exercices"
    if unite == "POINTS":
        return f"{valeur} points"
    return str(valeur)


CSS = """
@page { size: A4; margin: 14mm 12mm 16mm; }
* { box-sizing: border-box; }
body { font: 400 9.2pt/1.42 "Source Serif 4", Georgia, serif; color: #16181d; margin: 0; }
h1 { font: 700 19pt/1.2 "Inter", "Helvetica Neue", sans-serif; margin: 0 0 2mm; }
h2 { font: 700 11.5pt/1.3 "Inter", sans-serif; margin: 7mm 0 2.5mm;
     padding-bottom: 1.2mm; border-bottom: 1.6pt solid #16181d; page-break-after: avoid; }
h3 { font: 600 9.5pt/1.3 "Inter", sans-serif; margin: 4mm 0 1.5mm; color: #4a5160;
     text-transform: uppercase; letter-spacing: .06em; page-break-after: avoid; }
.chapeau { font-size: 9pt; color: #3d434f; margin: 0 0 4mm; }
.encadre { border: 1.2pt solid #16181d; padding: 3mm 3.5mm; margin: 0 0 5mm;
           background: #fafaf8; font-size: 8.8pt; }
.encadre p { margin: 0 0 1.6mm; }
.encadre p:last-child { margin: 0; }
table { width: 100%; border-collapse: collapse; margin-bottom: 3mm; }
th { font: 600 7.6pt/1.2 "Inter", sans-serif; text-transform: uppercase;
     letter-spacing: .05em; color: #5b6270; text-align: left;
     border-bottom: .8pt solid #b9bec8; padding: 1.4mm 1.6mm; }
td { padding: 1.8mm 1.6mm; border-bottom: .4pt solid #dfe2e8; vertical-align: top;
     font-size: 8.6pt; }
tr { page-break-inside: avoid; }
.code { font: 600 7.9pt/1.3 ui-monospace, "SFMono-Regular", Consolas, monospace;
        letter-spacing: -.01em; }
.valeur { font: 700 10pt/1.2 "Inter", sans-serif; white-space: nowrap; }
.source { display: block; margin-top: 1mm; font-size: 7.5pt; color: #6b7280; font-style: italic; }
.note { display: block; margin-top: 1.2mm; padding-left: 2mm;
        border-left: 1.6pt solid #c9752f; font-size: 7.6pt; color: #4a5160; }
.case { width: 9mm; text-align: center; font-size: 13pt; color: #98a0ad; }
.attente td { background: #fdf6ec; }
.marque { font: 600 7.2pt/1 "Inter", sans-serif; color: #a8641f;
          border: .8pt solid #d9a468; border-radius: 2pt; padding: .5mm 1.2mm; }
.signature { margin-top: 6mm; border: 1.2pt solid #16181d; padding: 4mm; page-break-inside: avoid; }
.signature .lignes { display: flex; gap: 6mm; margin-top: 5mm; }
.signature .lignes div { flex: 1; border-top: .8pt solid #16181d; padding-top: 1.5mm;
                          font-size: 7.8pt; color: #5b6270; }
footer { margin-top: 6mm; font-size: 7.6pt; color: #6b7280;
         border-top: .4pt solid #dfe2e8; padding-top: 2mm; }
"""


def ligne(p: dict, version: dict, *, attente: bool) -> str:
    note = version.get("note")
    return (
        f'<tr class="{"attente" if attente else ""}">'
        f'<td class="case">☐</td>'
        f'<td><span class="code">{e(p["code"])}</span><br>{e(p["libelle"])}'
        + (f'<span class="note">{e(note)}</span>' if note else "")
        + "</td>"
        f'<td class="valeur">{e(formater(version["valeur"], p["unite"]))}'
        + ('<br><span class="marque">EN ATTENTE</span>' if attente else "")
        + "</td>"
        f'<td>{e(version["fondement"]["texte"])}'
        f'<span class="source">Consulté : {e(version["fondement"]["source"])}</span></td>'
        "</tr>"
    )


def tableau(parametres: list[dict]) -> str:
    if not parametres:
        return "<p>Aucun.</p>"
    lignes = []
    for p in sorted(parametres, key=lambda x: (x["categorie"], x["code"])):
        for v in p["versions"]:
            lignes.append(ligne(p, v, attente=v["statut"] != "VALIDE"))
    return (
        '<table><thead><tr><th></th><th>Paramètre</th><th>Valeur retenue</th>'
        "<th>Fondement visé et source consultée</th></tr></thead><tbody>"
        + "".join(lignes)
        + "</tbody></table>"
    )


def bloc_signature(titre: str, qui: str) -> str:
    return (
        f'<div class="signature"><strong>{e(titre)}</strong>'
        f"<p style=\"font-size:8.4pt;margin:1.5mm 0 0\">{e(qui)}</p>"
        '<div class="lignes"><div>Nom et qualité</div><div>Date</div>'
        "<div>Signature</div></div></div>"
    )


def rendre() -> str:
    donnees = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    ps = donnees["parametres"]
    loi = [p for p in ps if p.get("nature", "LOI") == "LOI"]
    cabinet = [p for p in ps if p.get("nature") == "POLITIQUE_CABINET"]
    valides = sum(1 for p in ps for v in p["versions"] if v["statut"] == "VALIDE")
    attente = sum(1 for p in ps for v in p["versions"] if v["statut"] != "VALIDE")
    le_jour = datetime.now(UTC).date().isoformat()

    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Fiche de contreseing, référentiel normatif</title><style>{CSS}</style></head><body>
<h1>Fiche de contreseing du référentiel normatif</h1>
<p class="chapeau">CGA Broad Range Consulting Group · agrément MINFI/DGI n° 00000048 ·
référentiel version {e(donnees["version_referentiel"])} ·
{len(ps)} paramètres, dont {valides} validés et {attente} en attente ·
éditée le {e(le_jour)}</p>

<div class="encadre">
<p><strong>Ce que l'on vous demande de faire.</strong> Cocher chaque ligne dont vous
confirmez la valeur sur le texte. Barrer et annoter celles que vous contestez : ce qui est
barré repasse au statut « à valider » et le produit le signalera de nouveau sur chaque
chiffre qui en dépend.</p>
<p><strong>La colonne de droite porte deux choses distinctes.</strong> Le fondement dit quel
texte fait autorité. La source, en italique dessous, dit ce qui a été réellement ouvert au
moment de la validation. Si la source n'est pas le texte lui-même, c'est exactement là que
votre relecture apporte quelque chose.</p>
<p><strong>Les lignes sur fond ocre n'ont pas été confirmées.</strong> Elles ne sont pas
oubliées : elles sont marquées, et tout rapport qui s'en sert le dit.</p>
</div>

<h2>I · Paramètres de nature légale</h2>
<h3>Engagent un professionnel sur un texte : CGI, lois de finances, textes CNPS, actes
uniformes OHADA</h3>
{tableau(loi)}
{bloc_signature("Contreseing du référent fiscal",
                "Confirme avoir confronté chaque valeur cochée au texte visé.")}

<h2>II · Réglages de politique interne</h2>
<h3>Aucun texte ne les fixe. Ils relèvent d'un arbitrage de la direction, et un fiscaliste
n'a pas qualité pour les attester</h3>
{tableau(cabinet)}
{bloc_signature("Arrêté de la direction",
                "Confirme que ces réglages sont ceux que le cabinet entend appliquer.")}

<footer>Document généré depuis <code>Docs/referentiel/parametres.yaml</code>. Il ne se
modifie pas à la main : corriger le fichier source, puis rééditer. Le modèle refuse une
valeur déclarée validée qui ne porterait pas le nom de son signataire et la date.</footer>
</body></html>"""


def imprimer() -> bool:
    navigateur = next(
        (
            chemin
            for nom in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable")
            if (chemin := shutil.which(nom))
        ),
        None,
    )
    if navigateur is None:
        print("Chrome introuvable : le HTML est écrit, le PDF ne l'est pas.", file=sys.stderr)
        return False
    subprocess.run(
        [
            navigateur,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={SORTIE_PDF}",
            str(SORTIE_HTML),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return True


def main() -> int:
    SORTIE_HTML.write_text(rendre(), encoding="utf-8")
    print(f"HTML : {SORTIE_HTML.relative_to(RACINE)}")
    if imprimer():
        print(f"PDF  : {SORTIE_PDF.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
