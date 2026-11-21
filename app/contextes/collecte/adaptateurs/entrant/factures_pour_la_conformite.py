"""Les factures réelles que la collecte donne à la conformité (20 octobre 2026).

Une pièce lue et confirmée porte sa `FactureLue` : c'est la seule forme réelle d'une
facture dans la plateforme. Elle se met ici dans la forme du contrôle
(`FactureAControler`), la même que `proposer_depuis_la_piece` en comptabilité, et se
branche au démarrage par `brancher_la_source_des_factures`. Voir
`conformite/adaptateurs/sortant/factures_a_controler.py`.

⚠️ Toutes les pièces du locataire, sans filtre de périmètre : ce sont les routes de la
conformité qui restreignent (`restreindre`, `exiger_dossier`), comme elles le faisaient
sur le jeu de démonstration.
"""

from __future__ import annotations

from app.contextes.collecte.adaptateurs.entrant.routes_http import depot_pieces
from app.contextes.collecte.domaine.pieces import PieceJustificative
from app.contextes.conformite.api import (
    Document,
    FactureAControler,
    LigneFacture,
    Montants,
    Partie,
)

__all__ = ["facture_a_controler", "factures_des_pieces_lues"]


def facture_a_controler(piece: PieceJustificative) -> FactureAControler | None:
    """La facture lue d'une pièce, dans la forme du contrôle ; `None` si elle n'est pas lue."""
    lue = piece.facture
    if lue is None:
        return None
    return FactureAControler(
        document=Document(reference=lue.reference, date_emission=lue.date_emission),
        emetteur=Partie(denomination=lue.emetteur, niu=lue.niu_emetteur, niu_actif=lue.niu_actif),
        destinataire=Partie(niu=piece.entreprise),
        montants=Montants(
            total_ht=lue.montant_ht, total_tva=lue.montant_tva, total_ttc=lue.montant_ttc
        ),
        lignes=[
            LigneFacture(designation=ligne.designation, montant_ht=ligne.montant_ht)
            for ligne in lue.lignes
        ],
    )


def factures_des_pieces_lues() -> list[FactureAControler]:
    """Les factures lues de toutes les pièces du locataire, la plus récente d'abord."""
    pieces = sorted(depot_pieces().toutes(), key=lambda p: p.recue_le, reverse=True)
    return [f for f in (facture_a_controler(p) for p in pieces) if f is not None]
