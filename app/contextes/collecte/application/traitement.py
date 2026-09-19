"""Le traitement d'une pièce se termine : comptabilisée, ou classée sans suite (pas 107).

─────────────────────────────────────────────────────────────────────────────────
⚠️ LE DÉFAUT QUE LA CLÔTURE MENSUELLE A MIS AU JOUR

Le domaine de la pièce avait ses deux fins de traitement depuis le début :
`comptabiliser` (l'écriture est passée) et `archiver` (classée, avec un motif). **Aucune
route ne les appelait.** Seul le jeu de démonstration posait des pièces COMPTABILISEE.

Conséquence, invisible jusqu'au pas 107 : une pièce lue, saisie, dont l'écriture était
validée, restait « en attente de traitement » pour toujours. La boîte de réception ne se
vidait jamais, la charge du pilotage (pas 105) comptait ces pièces comme du travail restant,
et le point « pièces du mois à comptabiliser » de la clôture mensuelle aurait bloqué chaque
mois de chaque dossier. Un doublon ou un relevé bancaire, qui ne produisent pas d'écriture,
n'avaient aucune issue du tout.

CE QUE CE MODULE FAIT

* `comptabiliser_la_piece` : appelée quand une écriture est **validée**. La pièce que
  l'écriture cite (`piece_justificative`, par identifiant de pièce ou par référence du
  document) passe à COMPTABILISEE, rattachée à l'écriture. À la validation et non à la
  saisie : un brouillon peut encore changer de pièce.
* `classer_une_piece` : le comptable dit, avec un motif, qu'une pièce ne produira pas
  d'écriture (doublon, relevé déjà rapproché, document hors sujet). Elle passe à ARCHIVEE.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from app.contextes.collecte.domaine.pieces import PieceJustificative, TransitionRefusee
from app.contextes.collecte.domaine.ports import DepotPieces

__all__ = ["MOTIF_DE_CLASSEMENT_MINIMUM", "classer_une_piece", "comptabiliser_la_piece"]

#: Un classement sans motif lisible ne s'explique plus six mois plus tard.
MOTIF_DE_CLASSEMENT_MINIMUM = 10


def comptabiliser_la_piece(
    pieces: DepotPieces,
    *,
    entreprise: str,
    piece_justificative: str | None,
    cle_ecriture: str,
) -> PieceJustificative | None:
    """Rattache à l'écriture validée la pièce qu'elle cite, si on la reconnaît **sans doute**.

    La pièce est cherchée parmi celles **du dossier** encore en attente de traitement, par son
    identifiant (`PJ-2026-0001`) ou par la référence du document (`F-2026-0412`), qui est ce
    que la proposition d'écriture inscrit.

    Rend `None`, sans rien changer, dans trois cas, et c'est voulu :

    * aucune pièce ne correspond (une écriture de paie, une pièce tenue hors plateforme) ;
    * **plusieurs** pièces correspondent : un doublon non classé porte la même référence que
      l'original. Choisir au hasard rattacherait peut-être la copie ; la pièce reste visible,
      et le comptable classe le doublon ;
    * la pièce ne produit pas d'écriture par elle-même (un relevé) : elle se classe.
    """
    if not piece_justificative:
        return None
    candidates = [
        p
        for p in pieces.du_dossier(entreprise)
        if p.en_attente_de_traitement
        and piece_justificative in {p.identifiant, p.reference_document}
    ]
    if len(candidates) != 1 or not candidates[0].comptabilisable:
        return None
    comptabilisee = candidates[0].comptabiliser(cle_ecriture)
    pieces.enregistrer(comptabilisee)
    return comptabilisee


def classer_une_piece(
    piece: PieceJustificative, *, motif: str, pieces: DepotPieces
) -> PieceJustificative:
    """Termine le traitement d'une pièce qui ne produira pas d'écriture."""
    motif = motif.strip()
    if len(motif) < MOTIF_DE_CLASSEMENT_MINIMUM:
        raise TransitionRefusee(
            f"{piece.identifiant} : dire pourquoi la pièce est classée sans écriture "
            f"({MOTIF_DE_CLASSEMENT_MINIMUM} caractères au moins). « Doublon de PJ-2026-0001 », "
            "« relevé rapproché en juillet »."
        )
    if piece.traitee:
        fin = "comptabilisée" if piece.etat.value == "COMPTABILISEE" else "classée"
        raise TransitionRefusee(f"{piece.identifiant} est déjà {fin} : son traitement est terminé.")
    classee = piece.archiver(motif)
    pieces.enregistrer(classee)
    return classee
