"""Le fichier du personnel : inscrire un salarié, ouvrir un contrat, le clore (pas 89).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Les deux routes d'écriture du Social construisaient leurs entités et les rangeaient,
sans rien confronter à l'existant. Essai sur les données de démonstration, avant
correction, par un comptable qui suit trois dossiers :

- inscrire, sur LA COLOMBE, un salarié au matricule `SAL-0004` : NKOULOU, salarié de
  SARL BATIMENT PLUS, était **renommé « INTRUS » et passait chez LA COLOMBE**, contrat
  de 410 000 FCFA compris ;
- ouvrir, par l'adresse de LA COLOMBE, un contrat au matricule `SAL-0001` : le salarié
  d'AGRO changeait de dossier ;
- ouvrir un second contrat qui chevauche le premier : accepté, et la paie du mois
  devenait impossible (409) ;
- un contrat dont la fin précède le début : erreur 500.

Le premier défaut faisait disparaître un salarié de la déclaration sociale de son
employeur, c'est-à-dire des cotisations CNPS dues pour lui.

⚠️ TROIS RÈGLES, UNE PAR DÉFAUT

1. **Un matricule ne se réattribue pas.** Il est unique au locataire : l'inscrire une
   seconde fois est refusé, quel que soit le dossier. Le refus ne nomme pas le dossier
   qui l'emploie.
2. **Un contrat s'ouvre sur un salarié du dossier de l'adresse.** Un salarié d'un autre
   dossier y est « introuvable », exactement comme un matricule inconnu (pas 88).
3. **Deux contrats d'un même salarié ne se chevauchent pas.** Changer de salaire ou de
   type de contrat, c'est clore le contrat en vigueur puis en ouvrir un autre, ce que
   disait déjà la route et qu'aucun geste ne permettait : `clore_un_contrat` existe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from app.contextes.social.domaine.entites import Contrat, Salarie
from app.contextes.social.domaine.ports import DepotContrats, DepotSalaries, SalarieIntrouvable

__all__ = [
    "ContratEnChevauchement",
    "ContratIntrouvable",
    "MatriculeDejaAttribue",
    "clore_un_contrat",
    "inscrire_un_salarie",
    "ouvrir_un_contrat",
]


class MatriculeDejaAttribue(ValueError):
    """Le matricule appartient déjà à un salarié du locataire."""


class ContratEnChevauchement(ValueError):
    """Le contrat proposé couvre des jours déjà couverts par un autre contrat du salarié."""


class ContratIntrouvable(LookupError):
    """Aucun contrat du salarié n'est en vigueur à la date de clôture demandée."""


def _du_dossier(matricule: str, entreprise: str, salaries: DepotSalaries) -> Salarie:
    """Le salarié, s'il est employé par ce dossier. Sinon : introuvable, sans dire où il est."""
    introuvable = f"aucun salarié au matricule {matricule} dans ce dossier"
    try:
        salarie = salaries.lire(matricule)
    except SalarieIntrouvable as absence:
        raise SalarieIntrouvable(introuvable) from absence
    if salarie.entreprise != entreprise:
        raise SalarieIntrouvable(introuvable)
    return salarie


def _rattacher(salarie: Salarie, contrats: DepotContrats) -> None:
    """Dit au dépôt de contrats chez qui le salarié travaille, **d'après le salarié**.

    ⚠️ La réalisation SQL du dépôt tient ce rattachement par requête : sans lui, écrire le
    contrat d'un salarié inscrit lors d'une requête précédente échoue. La route le faisait
    avec le dossier **de l'adresse**, et c'est ainsi qu'un contrat ouvert par l'adresse
    d'un dossier déplaçait le salarié d'un autre. Il est pris désormais sur le salarié
    lui-même, dont on vient de vérifier qu'il est du dossier.
    """
    if hasattr(contrats, "rattacher"):
        contrats.rattacher(salarie.matricule, salarie.entreprise)


def inscrire_un_salarie(
    salarie: Salarie, *, salaries: DepotSalaries, contrats: DepotContrats
) -> Salarie:
    """Inscrit un salarié nouveau. Refuse un matricule déjà attribué, ici ou ailleurs."""
    try:
        existant = salaries.lire(salarie.matricule)
    except SalarieIntrouvable:
        existant = None
    if existant is not None:
        raise MatriculeDejaAttribue(
            f"le matricule {salarie.matricule} est déjà attribué"
            + (
                f" à {existant.prenom} {existant.nom}, dans ce dossier."
                if existant.entreprise == salarie.entreprise
                # ⚠️ Ni le nom ni le dossier : un matricule d'autrui ne renseigne sur personne.
                else "."
            )
            + " Un matricule ne se réattribue pas : choisissez-en un autre."
        )
    salaries.enregistrer(salarie)
    # Le rattachement au dossier vit à côté du contrat : sans lui, la première écriture
    # de contrat refuserait faute de savoir chez qui il s'exerce.
    if hasattr(contrats, "rattacher"):
        contrats.rattacher(salarie.matricule, salarie.entreprise)
    return salarie


def ouvrir_un_contrat(
    contrat: Contrat, *, entreprise: str, salaries: DepotSalaries, contrats: DepotContrats
) -> Contrat:
    """Ouvre un contrat sur un salarié du dossier, sans chevaucher ses contrats existants."""
    salarie = _du_dossier(contrat.salarie, entreprise, salaries)
    _rattacher(salarie, contrats)
    for existant in contrats.du_salarie(contrat.salarie):
        commence_avant_la_fin = existant.fin is None or contrat.debut < existant.fin
        finit_apres_le_debut = contrat.fin is None or contrat.fin > existant.debut
        if commence_avant_la_fin and finit_apres_le_debut:
            fin = f"au {existant.fin:%d/%m/%Y} (exclu)" if existant.fin else "sans fin"
            raise ContratEnChevauchement(
                f"{contrat.salarie} a déjà un contrat {existant.type_contrat} du "
                f"{existant.debut:%d/%m/%Y} {fin}. Deux contrats ne se chevauchent pas : "
                "clore le contrat en vigueur à la date du changement, puis ouvrir le nouveau."
            )
    contrats.enregistrer(contrat)
    return contrat


def clore_un_contrat(
    matricule: str,
    *,
    entreprise: str,
    le: date,
    salaries: DepotSalaries,
    contrats: DepotContrats,
) -> Contrat:
    """Clôt, à la date `le` (exclue), le contrat du salarié qui couvre la veille.

    ⚠️ La fin est exclue, comme partout dans le contexte (`[debut, fin[`) : clore « le
    01/10 » laisse le contrat couvrir tout septembre, et un nouveau contrat peut commencer
    le 01/10 sans chevauchement.
    """
    salarie = _du_dossier(matricule, entreprise, salaries)
    _rattacher(salarie, contrats)
    en_vigueur = [
        c for c in contrats.du_salarie(matricule) if c.debut < le and (c.fin is None or c.fin >= le)
    ]
    if not en_vigueur:
        raise ContratIntrouvable(
            f"aucun contrat de {matricule} ne court jusqu'au {le:%d/%m/%Y} : "
            "rien à clore à cette date."
        )
    contrat = en_vigueur[-1]
    if contrat.fin == le:
        raise ContratIntrouvable(f"le contrat de {matricule} est déjà clos au {le:%d/%m/%Y}.")
    close = contrat.model_copy(update={"fin": le})
    contrats.enregistrer(close)
    return close
