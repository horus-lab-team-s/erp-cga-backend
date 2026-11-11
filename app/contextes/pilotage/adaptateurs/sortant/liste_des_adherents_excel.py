"""La liste à jour des adhérents, en Excel (CGI art. 119 (4), 10 novembre 2026).

« Les promoteurs des centres de gestion agréés sont tenus d'annexer à leurs déclarations
mensuelles, la liste à jour de leurs adhérents. » Aucun modèle n'est publié par la DGI pour
cette annexe : la liste reprend ce qui identifie un adhérent devant l'administration (NIU,
raison sociale, régime, centre des impôts) et la date d'effet de son adhésion, qui ouvre
ses avantages.

⚠️ Les dénominations viennent de formulaires : elles sont déclarées texte, jamais formule.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from app.contextes.portefeuille.api import Entreprise
from app.partage.tableur import forcer_le_texte

__all__ = ["liste_des_adherents_en_excel", "nom_du_fichier"]

_REGIMES = {"IGS": "Impôt général synthétique", "REEL": "Réel"}
_ENTETES = [
    "N°",
    "N° d'adhésion",
    "NIU",
    "Raison sociale",
    "Forme juridique",
    "Régime fiscal",
    "Centre des impôts",
    "Adhérent depuis le",
    "RCCM",
    "Activité",
]


def nom_du_fichier(jour: date) -> str:
    return f"liste-des-adherents-{jour:%Y%m%d}.xlsx"


def liste_des_adherents_en_excel(
    adherents: Iterable[Entreprise], jour: date, *, cabinet: str, agrement: str | None
) -> bytes:
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Adhérents"
    gras = Font(bold=True)
    feuille.append([cabinet])
    feuille["A1"].font = gras
    if agrement:
        feuille.append([agrement])
    feuille.append([f"Liste à jour des adhérents au {jour:%d/%m/%Y} (CGI art. 119 (4))"])
    feuille.append([])
    feuille.append(_ENTETES)
    entete = feuille.max_row
    for cellule in feuille[entete]:
        cellule.font = gras
    nombre = 0
    for rang, e in enumerate(adherents, start=1):
        adhesion = e.adhesion_au(jour)
        feuille.append(
            [
                rang,
                (adhesion.numero if adhesion else None) or "",
                e.niu,
                e.denomination,
                str(e.forme_juridique),
                _REGIMES.get(str(e.regime_au(jour)), str(e.regime_au(jour))),
                str(e.rattachement_au(jour)),
                adhesion.debut.strftime("%d/%m/%Y") if adhesion else "",
                e.rccm or "",
                e.activite or "",
            ]
        )
        forcer_le_texte(feuille, feuille.max_row, range(2, 11))
        nombre = rang
    feuille.append([])
    pluriel = "s" if nombre > 1 else ""
    feuille.append([f"{nombre} adhérent{pluriel} actif{pluriel}"])
    for colonne, largeur in zip("ABCDEFGHIJ", (6, 16, 18, 38, 16, 26, 18, 18, 24, 36), strict=True):
        feuille.column_dimensions[colonne].width = largeur
    tampon = BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()
