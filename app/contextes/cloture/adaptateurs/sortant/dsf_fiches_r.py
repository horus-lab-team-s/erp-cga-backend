"""Les fiches R1, R2 et R3 du modèle « DSF Normal » : où va chaque information (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENNENT CES CELLULES

Le modèle ne marque pas ses cellules de saisie : toutes sont déverrouillées. Elles ont
été relevées sur le rendu du modèle (LibreOffice, page par page) et sur ses bordures :
dans la fiche R1, chaque valeur va dans le cadre AU-DESSUS de son libellé (« Greffe »,
« N° Registre du commerce »… sont sous leur cadre). Les codes de la fiche R2 ont une
case par chiffre. Les codes eux-mêmes sont ceux de la note 36 du guide de la DGI.

⚠️ CE QUI RESTE UNE LECTURE, À CONFIRMER SUR LE PORTAIL
* ZK (forme juridique) a trois cases pour un code à deux chiffres : la troisième est
  lue comme l'indicateur d'agrément prioritaire de la note 36 (« remplacer 0 par 1 »),
  posée à 0 ;
* ZL (régime fiscal) a deux cases pour un code à un chiffre : posé dans la première ;
* les cases à cocher (contrôle, approbation en AG) : un « X » dans la case du contrôle ;
  l'approbation en AG est une case de formulaire, non remplie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["ActiviteR2", "IdentiteDsf", "PersonneR3", "valeurs_des_fiches_r"]

FICHE_R1, FICHE_R2, FICHE_R3 = "Fiche R1", "Fiche R2", "Fiche R3"

#: Note 36, 1 : code de la forme juridique.
_CODES_FORME = {
    "SA": "01",
    "SARL": "02",
    "SARLU": "02",
    "GIE": "06",
    "ASSOCIATION": "07",
    "SAS": "08",
}
_FORME_AUTRE = "09"
#: Note 36, 2 : code du régime fiscal. La DSF Normal ne concerne que le réel.
_CODE_REEL_NORMAL = "1"
#: Les cases à cocher du contrôle de l'entité (ZQ, ZR, ZS).
_CASE_DU_CONTROLE = {"PUBLIC": "P9", "PRIVE_NATIONAL": "P11", "PRIVE_ETRANGER": "P13"}

#: Fiche R2 : six lignes d'activité, de trois rangées chacune ; « Divers » et « TOTAL ».
_LIGNES_ACTIVITE = (30, 33, 36, 39, 42, 45)
_LIGNE_DIVERS, _LIGNE_TOTAL = 47, 48
_CASES_NACAM = "FGHIJK"
#: Fiche R3 : treize lignes de dirigeants, douze d'administrateurs.
_LIGNES_DIRIGEANTS = range(11, 24)
_LIGNES_CONSEIL = range(33, 45)
#: Fiche R1 : huit lignes de domiciliation bancaire.
_LIGNES_BANQUE = range(41, 49)


@dataclass(frozen=True)
class ActiviteR2:
    designation: str
    code_nacam: str
    chiffre_affaires_ht: Decimal | None = None


@dataclass(frozen=True)
class PersonneR3:
    nom: str
    qualite: str
    prenoms: str | None = None
    niu: str | None = None
    adresse: str | None = None


@dataclass(frozen=True)
class IdentiteDsf:
    """Ce que les fiches R1 à R3 reprennent : le dossier, plus la fiche signalétique."""

    denomination: str
    niu: str
    forme_juridique: str
    ouverture: date
    cloture: date
    rccm: str | None = None
    siege: str | None = None
    telephone: str | None = None
    courriel: str | None = None
    boite_postale: str | None = None
    ville: str | None = None
    adresse_geographique: str | None = None
    numero_cnps_employeur: str | None = None
    code_importateur: str | None = None
    personne_a_contacter: str | None = None
    professionnel_des_etats: str | None = None
    signataire_nom: str | None = None
    signataire_qualite: str | None = None
    banque: str | None = None
    numero_de_compte: str | None = None
    pays_du_siege: str = "09"
    etablissements_au_cameroun: int | None = None
    etablissements_hors_cameroun: int | None = None
    premiere_annee_au_cameroun: int | None = None
    controle: str | None = None
    activites: tuple[ActiviteR2, ...] = ()
    dirigeants: tuple[PersonneR3, ...] = ()
    conseil_d_administration: tuple[PersonneR3, ...] = ()
    #: L'exercice précédent, s'il est connu : sa clôture et sa durée en mois.
    cloture_precedente: date | None = None
    duree_precedente: int | None = None
    avertissements: list[str] = field(default_factory=list)


def _mois(debut: date, fin: date) -> int:
    return (fin.year - debut.year) * 12 + fin.month - debut.month + 1


def _cases(colonnes: str, ligne: int, texte: str) -> dict[str, str]:
    """Un caractère par case, de gauche à droite."""
    return {f"{c}{ligne}": car for c, car in zip(colonnes, texte, strict=False)}


def _sans_vides(valeurs: dict[str, str | Decimal | None]) -> dict[str, str | Decimal]:
    return {k: v for k, v in valeurs.items() if v not in (None, "")}


def valeurs_des_fiches_r(i: IdentiteDsf) -> dict[str, dict[str, str | Decimal]]:
    """Feuille → cellule → valeur, pour les fiches R1, R2 et R3."""
    duree = _mois(i.ouverture, i.cloture)
    en_tete = {
        "A4": f"Désignation entité : {i.denomination}    Exercice clos le {i.cloture:%d-%m-%Y}",
        "A5": f"Numéro d'identification : {i.niu}    Durée (en mois) : {duree}",
    }
    principale = i.activites[0] if i.activites else None

    r1: dict[str, str | Decimal | None] = {
        "A5": f"Description sociale de l'entreprise : {i.denomination} ({i.forme_juridique})",
        "A6": f"Siège social : {i.siege or ''}",
        "A7": (
            f"N° d'identification fiscal : {i.niu}    Exercice clos le : "
            f"{i.cloture:%d/%m/%Y}    Durée en mois : {duree}"
        ),
        "E9": f"DU : {i.ouverture:%d/%m/%Y}    AU : {i.cloture:%d/%m/%Y}",
        "F13": f"{i.cloture_precedente:%d/%m/%Y}" if i.cloture_precedente else None,
        "I13": str(i.duree_precedente) if i.duree_precedente else None,
        "E15": i.rccm,
        "C17": i.numero_cnps_employeur,
        "F17": i.code_importateur,
        "I17": principale.code_nacam if principale else None,
        "C21": i.telephone,
        "E21": i.courriel,
        "I21": i.boite_postale,
        "J21": i.ville,
        "B23": i.adresse_geographique,
        "C25": principale.designation if principale else None,
        "C27": i.personne_a_contacter,
        "C29": i.professionnel_des_etats,
        "C39": i.signataire_nom,
        "C41": i.signataire_qualite,
    }
    if i.banque or i.numero_de_compte:
        premiere = _LIGNES_BANQUE[0]
        r1 |= {f"G{premiere}": i.banque, f"I{premiere}": i.numero_de_compte}

    r2: dict[str, str | Decimal | None] = dict(en_tete)
    r2 |= _cases("FGH", 9, _CODES_FORME.get(i.forme_juridique, _FORME_AUTRE) + "0")
    r2 |= _cases("FG", 11, _CODE_REEL_NORMAL)
    r2 |= _cases("FGH", 13, i.pays_du_siege)
    if i.etablissements_au_cameroun is not None:
        r2 |= _cases("FGH", 15, f"{i.etablissements_au_cameroun:03d}"[-3:])
    if i.etablissements_hors_cameroun is not None:
        r2 |= _cases("FG", 17, f"{i.etablissements_hors_cameroun:02d}"[-2:])
    if i.premiere_annee_au_cameroun is not None:
        r2 |= _cases("FGHIJ", 20, str(i.premiere_annee_au_cameroun))
    if i.controle in _CASE_DU_CONTROLE:
        r2[_CASE_DU_CONTROLE[i.controle]] = "X"

    detaillees, divers = i.activites[: len(_LIGNES_ACTIVITE)], i.activites[len(_LIGNES_ACTIVITE) :]
    connus = [a.chiffre_affaires_ht for a in i.activites if a.chiffre_affaires_ht is not None]
    total = sum(connus, Decimal(0))
    for ligne, a in zip(_LIGNES_ACTIVITE, detaillees, strict=False):
        r2[f"A{ligne}"] = a.designation
        r2 |= _cases(_CASES_NACAM, ligne, a.code_nacam)
        if a.chiffre_affaires_ht is not None:
            r2[f"M{ligne}"] = a.chiffre_affaires_ht
            if total:
                r2[f"P{ligne}"] = _part(a.chiffre_affaires_ht, total)
    if divers:
        reste = sum((a.chiffre_affaires_ht or Decimal(0) for a in divers), Decimal(0))
        r2[f"M{_LIGNE_DIVERS}"] = reste
        if total:
            r2[f"P{_LIGNE_DIVERS}"] = _part(reste, total)
    if connus:
        r2[f"M{_LIGNE_TOTAL}"] = total
        r2[f"P{_LIGNE_TOTAL}"] = "100 %"

    r3: dict[str, str | Decimal | None] = dict(en_tete)
    for ligne, d in zip(_LIGNES_DIRIGEANTS, i.dirigeants, strict=False):
        r3 |= {f"A{ligne}": d.nom, f"B{ligne}": d.prenoms, f"C{ligne}": d.qualite}
        r3 |= {f"D{ligne}": d.niu, f"E{ligne}": d.adresse}
    for ligne, m in zip(_LIGNES_CONSEIL, i.conseil_d_administration, strict=False):
        r3 |= {f"A{ligne}": m.nom, f"B{ligne}": m.prenoms, f"C{ligne}": m.qualite}
        r3[f"E{ligne}"] = m.adresse

    return {FICHE_R1: _sans_vides(r1), FICHE_R2: _sans_vides(r2), FICHE_R3: _sans_vides(r3)}


def _part(montant: Decimal, total: Decimal) -> str:
    pourcentage = (montant * 100 / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{pourcentage} %".replace(".", ",")
