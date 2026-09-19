"""Lire une pièce reçue : l'identifier et la faire passer à LUE (pas 91).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

La transition `marquer_lue` existait au domaine, avec sa règle : une pièce ne se lit
qu'identifiée (type, numéro, date, montant). Aucune route ne l'exposait, et le bouton
« marquer comme lu » de la boîte de réception, décoratif, avait été retiré au pas 75.
Une pièce déposée sans ses données par l'adhérent restait donc à l'état REÇUE pour
toujours, sans que le comptable puisse dire l'avoir ouverte et identifiée.

⚠️ SEULE UNE PIÈCE REÇUE SE LIT

La transition du domaine accepte de « relire » une pièce déjà lue en changeant ses
données. Ce serait réécrire, après coup, le montant et le numéro d'un document sur
lesquels un contrôle de conformité ou une écriture a pu déjà s'appuyer. La lecture est
refusée hors de l'état REÇUE : corriger une pièce lue est un autre geste, qui laissera
sa propre trace.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.contextes.collecte.domaine.pieces import (
    EtatPiece,
    PieceJustificative,
    TransitionRefusee,
    TypePiece,
)
from app.contextes.collecte.domaine.ports import DepotPieces

__all__ = ["lire_une_piece"]


def lire_une_piece(
    piece: PieceJustificative,
    *,
    type: TypePiece | None,
    reference_document: str | None,
    date_document: date | None,
    montant_ttc: Decimal | None,
    emetteur: str | None,
    pieces: DepotPieces,
) -> PieceJustificative:
    """Identifie la pièce avec ce qui est fourni, et la passe à LUE.

    Seuls les champs fournis remplacent ceux de la pièce : un comptable qui complète la
    date d'une pièce dont l'adhérent avait donné le montant ne doit pas effacer ce montant.
    """
    if piece.etat is not EtatPiece.RECUE:
        raise TransitionRefusee(
            f"{piece.identifiant} est à l'état {piece.etat} : seule une pièce reçue se lit. "
            "Ses données ne se réécrivent pas après coup."
        )
    details = {
        champ: valeur
        for champ, valeur in (
            ("type", type),
            ("reference_document", reference_document),
            ("date_document", date_document),
            ("montant_ttc", montant_ttc),
            ("emetteur", emetteur),
        )
        if valeur is not None
    }
    lue = piece.marquer_lue(**details)
    pieces.enregistrer(lue)
    return lue
