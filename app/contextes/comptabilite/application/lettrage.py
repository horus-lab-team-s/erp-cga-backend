"""Lettrer et délettrer (pas 108). Voir l'en-tête de `domaine/lettrage.py`.

Aucun cas d'usage ne lit l'horloge, la session ni la base : la route passe les écritures de
l'exercice, le plan, les lettrages existants du compte, la personne et l'instant.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.contextes.comptabilite.domaine.entites import Compte, EcritureComptable, EtatEcriture
from app.contextes.comptabilite.domaine.lettrage import (
    LettrageDeLignes,
    LettrageRefuse,
    ReferenceDeLigne,
    ReglagesDuLettrage,
    lettre_de_rang,
)

__all__ = ["lettrages_par_ligne", "lettrer"]


def lettrages_par_ligne(
    lettrages: list[LettrageDeLignes],
) -> dict[tuple[str, int], LettrageDeLignes]:
    """Les lettrages **actifs**, indexés par ligne. Un lettrage défait ne lettre plus rien."""
    return {
        (reference.cle_ecriture, reference.rang): lettrage
        for lettrage in lettrages
        if lettrage.actif
        for reference in lettrage.lignes
    }


def _montant(valeur: Decimal) -> str:
    return f"{valeur:,.0f}".replace(",", " ")


def lettrer(
    *,
    dossier: str,
    exercice: str,
    compte: str,
    references: list[ReferenceDeLigne],
    ecritures_de_l_exercice: list[EcritureComptable],
    plan: list[Compte],
    lettrages_du_compte: list[LettrageDeLignes],
    reglages: ReglagesDuLettrage,
    par: str,
    le: datetime,
) -> LettrageDeLignes:
    """Apparie les lignes désignées, ou refuse en disant laquelle et pourquoi.

    `lettrages_du_compte` : **tous** les lettrages du compte sur l'exercice, défaits compris.
    Ils décident de la lettre suivante, qui ne se réutilise jamais.
    """
    au_plan = next((c for c in plan if c.numero == compte), None)
    if au_plan is None or not au_plan.lettrable:
        raise LettrageRefuse(
            f"le compte {compte} n'est pas lettrable : seuls les comptes de tiers se lettrent "
            "(fournisseurs, clients, personnel)."
        )
    if len(set(references)) < 2:
        raise LettrageRefuse("un lettrage apparie au moins deux lignes distinctes.")

    ecritures = {e.cle: e for e in ecritures_de_l_exercice}
    deja = lettrages_par_ligne(lettrages_du_compte)
    debit = credit = Decimal(0)
    tiers: set[str] = set()
    for reference in references:
        ecriture = ecritures.get(reference.cle_ecriture)
        if ecriture is None:
            raise LettrageRefuse(
                f"écriture {reference.cle_ecriture} inconnue sur l'exercice {exercice}."
            )
        if ecriture.etat is not EtatEcriture.VALIDEE:
            raise LettrageRefuse(
                f"écriture {ecriture.cle} en brouillon : elle peut encore changer, on ne la "
                "lettre qu'une fois validée."
            )
        if reference.rang >= len(ecriture.lignes):
            raise LettrageRefuse(
                f"l'écriture {ecriture.cle} n'a pas de ligne {reference.rang + 1}."
            )
        ligne = ecriture.lignes[reference.rang]
        if ligne.compte != compte:
            raise LettrageRefuse(
                f"la ligne {reference.rang + 1} de {ecriture.cle} est au compte {ligne.compte}, "
                f"pas au {compte} : un lettrage reste sur un seul compte."
            )
        if (reference.cle_ecriture, reference.rang) in deja:
            autre = deja[(reference.cle_ecriture, reference.rang)]
            raise LettrageRefuse(
                f"la ligne {reference.rang + 1} de {ecriture.cle} est déjà lettrée "
                f"{autre.lettre} : délettrer d'abord."
            )
        if ligne.lettrage:
            raise LettrageRefuse(
                f"la ligne {reference.rang + 1} de {ecriture.cle} porte la lettre "
                f"{ligne.lettrage} reprise du logiciel du client : elle ne se relettre pas ici."
            )
        if ligne.tiers:
            tiers.add(ligne.tiers)
        if ligne.au_debit:
            debit += ligne.montant
        else:
            credit += ligne.montant

    if reglages.meme_tiers_exige and len(tiers) > 1:
        raise LettrageRefuse(
            f"les lignes portent des tiers différents ({', '.join(sorted(tiers))}) : lettrer "
            "la facture de l'un avec le règlement de l'autre effacerait deux dettes qui existent."
        )
    if abs(debit - credit) > reglages.ecart_tolere:
        raise LettrageRefuse(
            f"la sélection ne se solde pas : {_montant(debit)} au débit, {_montant(credit)} au "
            f"crédit, écart de {_montant(abs(debit - credit))} FCFA. Un lettrage déséquilibré "
            "cacherait un reste dû."
        )
    lettre = lettre_de_rang(len(lettrages_du_compte))
    return LettrageDeLignes(
        identifiant=f"LET-{exercice}-{compte}-{lettre}",
        dossier=dossier,
        exercice=exercice,
        compte=compte,
        lettre=lettre,
        lignes=tuple(sorted(set(references), key=lambda r: (r.cle_ecriture, r.rang))),
        total_debit=debit,
        total_credit=credit,
        tiers=next(iter(tiers)) if len(tiers) == 1 else None,
        par=par,
        le=le,
    )
