"""La balance auxiliaire des clients ou des fournisseurs (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LA DGI DEMANDE : parmi les listings obligatoires de la DSF, la « balance
auxiliaire clients » et la « balance auxiliaire fournisseurs », au format Excel (guide
utilisateur DSF 2025 de la DGI, « Les listings »). impots.cm n'en publie pas de modèle
(ses modèles par tiers sont dans `declaration_par_tiers.py`) : ce module en donne le
CONTENU, que tout comptable reconnaît.

UNE LIGNE PAR TIERS
    solde reporté   le journal des à-nouveaux (AN) : ce que l'exercice précédent laisse
    débit, crédit   les mouvements de l'exercice, hors à-nouveaux
    solde           reporté + débit − crédit, débiteur ou créditeur

⚠️ LES ÉCRITURES VALIDÉES SEULES, comme la balance générale : un brouillon n'est pas de
la comptabilité.

⚠️ CE QUI N'A PAS DE TIERS FIGURE QUAND MÊME, sur une ligne « sans tiers » : la taire
ferait une balance auxiliaire qui ne retombe pas sur le compte collectif, et c'est
précisément ce que le comptable doit corriger avant le dépôt.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.contextes.comptabilite.domaine.entites import (
    EcritureComptable,
    EtatEcriture,
    Sens,
)

__all__ = ["LigneBalanceAuxiliaire", "NatureAuxiliaire", "balance_auxiliaire"]

JOURNAL_DES_A_NOUVEAUX = "AN"


class NatureAuxiliaire(StrEnum):
    CLIENTS = "clients"
    FOURNISSEURS = "fournisseurs"


#: Les racines SYSCOHADA des comptes de chaque balance : 41 clients, 40 fournisseurs.
RACINES = {NatureAuxiliaire.CLIENTS: "41", NatureAuxiliaire.FOURNISSEURS: "40"}


@dataclass(frozen=True)
class LigneBalanceAuxiliaire:
    #: Le code du tiers porté par les lignes d'écriture (son NIU quand l'écriture vient
    #: d'une facture lue), ou `None` pour ce qui n'en a pas.
    tiers: str | None
    report: Decimal
    debit: Decimal
    credit: Decimal

    @property
    def solde(self) -> Decimal:
        """Positif : débiteur. Négatif : créditeur."""
        return self.report + self.debit - self.credit


def balance_auxiliaire(
    ecritures: list[EcritureComptable], nature: NatureAuxiliaire
) -> list[LigneBalanceAuxiliaire]:
    """Une ligne par tiers mouvementé sur les comptes de la nature, triée par code ; la
    ligne sans tiers en dernier."""
    racine = RACINES[nature]
    cumuls: dict[str | None, list[Decimal]] = {}
    for ecriture in ecritures:
        if ecriture.etat is not EtatEcriture.VALIDEE:
            continue
        a_nouveau = ecriture.journal == JOURNAL_DES_A_NOUVEAUX
        for ligne in ecriture.lignes:
            if not ligne.compte.startswith(racine):
                continue
            c = cumuls.setdefault(ligne.tiers, [Decimal(0)] * 3)
            if a_nouveau:
                c[0] += ligne.montant if ligne.sens is Sens.DEBIT else -ligne.montant
            elif ligne.sens is Sens.DEBIT:
                c[1] += ligne.montant
            else:
                c[2] += ligne.montant
    return [
        LigneBalanceAuxiliaire(tiers=t, report=c[0], debit=c[1], credit=c[2])
        for t, c in sorted(cumuls.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
    ]
