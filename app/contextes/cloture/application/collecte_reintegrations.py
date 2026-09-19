"""D'où viennent les réintégrations : des écritures de l'exercice.

─────────────────────────────────────────────────────────────────────────────────
LE MAILLON 4 DE LA CHAÎNE DE TRAÇABILITÉ

    PieceJustificative → RapportConformite → Constat
        → LigneEcriture (attribut fiscal) → **LignePassage** → liasse

`AttributFiscal` annonçait déjà l'échéance en toutes lettres : « ce qui remontera
au tableau de passage à la clôture, et le poste où il atterrira. Renseigné par le
contexte H le moment venu. » Le moment est venu, et ce module est le rendez-vous.

⚠️ UNE TVA REJETÉE N'EST PAS UNE RÉINTÉGRATION AU RÉSULTAT FISCAL

C'est la distinction que ce module existe pour tenir, et se tromper coûte cher
dans les deux sens.

**Une charge refusée** — une facture dont la désignation ne permet pas
d'identifier la prestation, une dépense somptuaire — a diminué le résultat
comptable sans que le fisc l'admette. Elle se **réintègre** : c'est une ligne du
tableau de passage.

**Une TVA non déductible** est un tout autre sujet. Elle ne touche pas le résultat
mais la déclaration de TVA : le montant sort de la TVA récupérable du mois, ce que
F · Obligations traite déjà. Comptablement, cette TVA rejoint le coût du bien ou
du service, et devient une charge qui, elle, est le plus souvent déductible.

Les additionner au tableau de passage réintégrerait une somme qui n'a jamais
diminué le résultat — l'adhérent paierait l'impôt deux fois sur le même montant,
une fois en TVA non récupérée et une fois en base imposable majorée. Le module
sépare donc les deux et **signale** les TVA rejetées sans les réintégrer, pour que
le réviseur vérifie qu'elles ont bien été reclassées en charge.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contextes.cloture.application.passage_fiscal import ConsequenceAReintegrer
from app.contextes.comptabilite.api import EcritureComptable, EtatEcriture

__all__ = ["MoissonReintegrations", "moissonner"]


@dataclass(frozen=True)
class MoissonReintegrations:
    """Ce que les écritures de l'exercice portent de conséquences fiscales."""

    #: Les charges refusées : elles se réintègrent.
    a_reintegrer: tuple[ConsequenceAReintegrer, ...]
    #: Les TVA rejetées : à vérifier, jamais à réintégrer. Voir l'en-tête.
    tva_rejetee: Decimal
    #: Les références de pièces portant une TVA rejetée, pour le contrôle.
    pieces_a_verifier: tuple[str, ...]

    @property
    def total_a_reintegrer(self) -> Decimal:
        return sum((c.montant for c in self.a_reintegrer), Decimal(0))


def moissonner(ecritures: list[EcritureComptable]) -> MoissonReintegrations:
    """Relève, dans les écritures de l'exercice, ce qui remonte à la clôture.

    Le montant réintégré est `montant_a_reintegrer` s'il est renseigné, sinon le
    montant de la ligne. La distinction compte : une règle peut refuser une
    **fraction** d'une charge — la quote-part privée d'un véhicule, par exemple —
    et réintégrer la ligne entière ferait payer l'adhérent sur une dépense
    partiellement professionnelle.

    Une écriture non validée est ignorée. Un brouillon n'est pas de la
    comptabilité : le réintégrer ferait dépendre le résultat fiscal d'une saisie
    que personne n'a arrêtée, et le montant changerait entre deux consultations.
    """
    a_reintegrer: list[ConsequenceAReintegrer] = []
    tva_rejetee = Decimal(0)
    pieces: set[str] = set()

    for ecriture in ecritures:
        if not _validee(ecriture):
            continue
        for ligne in ecriture.lignes:
            attribut = ligne.attribut_fiscal
            if attribut is None:
                continue
            if attribut.rejette_charge:
                montant = attribut.montant_a_reintegrer
                if montant is None:
                    montant = ligne.montant
                a_reintegrer.append(
                    ConsequenceAReintegrer(
                        reference_piece=_reference(ecriture),
                        motif=attribut.motif_non_deductibilite
                        or "Charge refusée par le contrôle de conformité",
                        montant=abs(montant),
                        poste=attribut.poste_reintegration,
                        regles=tuple(filter(None, [attribut.code_regle_origine])),
                    )
                )
            if attribut.rejette_tva:
                tva_rejetee += abs(ligne.montant)
                pieces.add(_reference(ecriture))

    return MoissonReintegrations(
        a_reintegrer=tuple(a_reintegrer),
        tva_rejetee=tva_rejetee,
        pieces_a_verifier=tuple(sorted(pieces)),
    )


def _validee(ecriture: EcritureComptable) -> bool:
    """Vrai si l'écriture est arrêtée."""
    return ecriture.etat is EtatEcriture.VALIDEE


def _reference(ecriture: EcritureComptable) -> str:
    """La référence lisible d'une écriture, pour le tableau de passage.

    ⚠️ La **pièce justificative** d'abord, le numéro d'écriture ensuite. C'est la
    facture que le vérificateur demandera, pas l'écriture qui l'enregistre : lui
    présenter « AC-000012 » l'obligerait à un aller-retour par le journal pour
    retrouver « F-2026-0418 », qui est ce qu'il cherche.
    """
    if ecriture.piece_justificative:
        return ecriture.piece_justificative
    return f"{ecriture.journal}-{ecriture.numero:06d}"
