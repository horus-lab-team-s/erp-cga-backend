"""Un gérant d'essai, qui fixe les prix du catalogue.

⚠️ POURQUOI LES TESTS EN ONT BESOIN

Depuis que le prix est une décision du gérant, un tarif du catalogue est « à
valider » tant que personne ne l'a arrêté, et **il ne se paie pas en ligne**. Les
tests qui règlent un devis supposaient un catalogue payable : ils ne l'étaient
que parce que personne ne contrôlait qui avait fixé le prix.

Ils passent donc par ce gérant, qui fixe chaque prix du catalogue à son montant
en vigueur, comme la direction le ferait dans la console. C'est la réalité
désormais : pas de paiement en ligne sans décision humaine préalable.
"""

from __future__ import annotations

from datetime import date, datetime

from app.contextes.souscription.api import (
    CATALOGUE,
    CatalogueArrete,
    DepotServicesMemoire,
    DepotTarifsFixesMemoire,
    NatureService,
    TarifFixe,
)
from app.contextes.souscription.domaine.ports import DepotTarifsFixes

#: Le compte de la direction dans le jeu de démonstration.
GERANT = "C-001"


def decisions_du_gerant(
    a_partir_du: date = date(2026, 1, 1),
    le: datetime = datetime(2025, 12, 20, 9, 0),
) -> list[TarifFixe]:
    """Une décision par prix du catalogue qui porte un montant à `a_partir_du`."""
    decisions: list[TarifFixe] = []
    for service in CATALOGUE:
        cibles = (
            [(f.code, f.tarifs) for f in service.formules]
            if service.formules
            else [(None, service.tarifs)]
        )
        for formule, tarifs in cibles:
            tarif = next((t for t in tarifs if t.couvre(a_partir_du)), None)
            if tarif is None or tarif.montant is None:
                continue
            if service.nature is NatureService.SUR_ETUDE:
                continue
            decisions.append(
                TarifFixe(
                    identifiant=f"TF-essai-{service.code}-{formule or 'unique'}",
                    service=service.code,
                    formule=formule,
                    montant=tarif.montant,
                    a_partir_du=a_partir_du,
                    motif="Barème 2026 arrêté par la direction (jeu d'essai).",
                    fixe_par=GERANT,
                    fixe_le=le,
                )
            )
    return decisions


def fixer_les_prix(depot: DepotTarifsFixes) -> None:
    """Enregistre les décisions du gérant d'essai dans un dépôt."""
    for d in decisions_du_gerant():
        depot.enregistrer(d)


def catalogue_fixe() -> CatalogueArrete:
    """Le catalogue en mémoire, ses prix fixés par le gérant d'essai."""
    decisions = DepotTarifsFixesMemoire()
    fixer_les_prix(decisions)
    return CatalogueArrete(DepotServicesMemoire(), decisions)


def fixer_les_prix_du_comptoir() -> None:
    """Fixe les prix dans le comptoir de l'API, en mémoire.

    À appeler APRÈS `reinitialiser_comptoir()` : celui-ci repart d'un comptoir neuf,
    donc d'un catalogue dont aucun prix n'est fixé.
    """
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    fixer_les_prix(comptoir().tarifs_fixes)
