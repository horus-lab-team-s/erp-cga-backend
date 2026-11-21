"""Les factures que les écrans de conformité contrôlent (20 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
⚠️ CONSTATÉ À L'AUDIT AVANT LA MISE EN PRODUCTION

La boîte de réception, la file des anomalies, les relances, l'essai d'une règle du
cabinet et l'écart en masse lisaient le seul jeu de démonstration (`FACTURES_DEMO`),
en production comme ailleurs. La direction y aurait vu des factures inventées, et les
vraies pièces, lues et confirmées, n'auraient porté aucun verdict.

Désormais ces écrans lisent `factures_a_controler()` : les factures RÉELLES, que la
collecte branche au démarrage (une pièce lue porte sa `FactureLue`), et, hors des
environnements exposés seulement, le jeu de démonstration en plus.

⚠️ L'INVERSION, ET NON UN IMPORT. La conformité ne dépend d'aucun autre contexte
(`ARETES_AUTORISEES`) : c'est la collecte qui lui donne ses factures, comme une prise
qu'on branche. Sans branchement (un essai qui monte la conformité seule), la liste
réelle est vide, jamais fausse.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.contextes.conformite.adaptateurs.sortant.donnees_demo import FACTURES_DEMO
from app.contextes.conformite.domaine.entites import FactureAControler

__all__ = [
    "SourceDesFactures",
    "brancher_la_source_des_factures",
    "facture_par_reference",
    "factures_a_controler",
]

#: Rend les factures réelles lues et confirmées, de tout le portefeuille du locataire.
SourceDesFactures = Callable[[], Iterable[FactureAControler]]

_source: SourceDesFactures | None = None


def brancher_la_source_des_factures(source: SourceDesFactures | None) -> None:
    """Posée par `main` au démarrage ; `None` la débranche (essais)."""
    global _source
    _source = source


def _destinataire(facture: FactureAControler) -> str | None:
    return facture.destinataire.niu if facture.destinataire is not None else None


def _avec_la_demonstration() -> bool:
    """Le jeu fictif, partout sauf là où de vrais clients regardent."""
    from app.infrastructure.config import configuration

    return not configuration().en_production


def factures_a_controler() -> list[FactureAControler]:
    """Les factures réelles d'abord ; le jeu de démonstration ensuite, hors production.

    Une facture réelle qui porterait la référence et le dossier d'une facture fictive
    la remplace : deux lignes pour une seule pièce feraient croire à deux factures.
    """
    reelles = list(_source()) if _source is not None else []
    if not _avec_la_demonstration():
        return reelles
    vues = {(f.document.reference, _destinataire(f)) for f in reelles}
    fictives = [
        f for f in FACTURES_DEMO.values() if (f.document.reference, _destinataire(f)) not in vues
    ]
    return reelles + fictives


def facture_par_reference(
    reference: str, *, voit: Callable[[str | None], bool] = lambda _niu: True
) -> FactureAControler | None:
    """La facture de ce numéro, de préférence dans un dossier que l'appelant voit.

    ⚠️ Deux fournisseurs peuvent numéroter pareil : un numéro ne désigne pas une
    facture seule. On rend d'abord celle d'un dossier visible ; à défaut, la première,
    et c'est à la route de refuser le dossier (`exiger_dossier`), avec le même 404
    qu'une référence inconnue.
    """
    candidates = [f for f in factures_a_controler() if f.document.reference == reference]
    return next((f for f in candidates if voit(_destinataire(f))), None) or next(
        iter(candidates), None
    )
