"""Comment le domaine fiscal chiffre l'enjeu de ses constats.

C'est l'implémentation du port `app.moteur.consequence.Valorisation` pour la conformité
documentaire. Le noyau ne sait pas ce que coûte une facture non conforme ; ce module le
sait, et lui seul.

RÈGLE DE CHIFFRAGE, § 4 de `Docs/architecture/03-moteur-conformite.md`

    taxe seule rejetée      → le montant de la taxe
    charge seule rejetée    → le montant hors taxes
    les deux                → le total toutes taxes comprises
    aucune                  → aucun enjeu

Le dernier cas est le plus important à respecter. Un constat qui ne rejette rien reste
qualitatif : il signale une irrégularité de forme sans conséquence chiffrable. Afficher
zéro laisserait croire à un enjeu nul là où il n'y a pas d'enjeu du tout, et un réviseur
qui voit « 0 FCFA » en déduit qu'il peut passer.

POURQUOI LIRE LES FAITS ET NON LA FACTURE

La valorisation reçoit ce que le sujet a exposé, sous les chemins que déclare
`SCHEMA_FACTURE`. Elle ne peut donc pas aller chercher un champ que les règles n'ont pas
le droit d'interroger. Sans cette contrainte, un chiffrage finirait par dépendre d'une
donnée invisible aux règles, et deux constats issus de la même facture cesseraient d'être
explicables par les mêmes faits.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.contextes.conformite.domaine.entites import ConsequenceFiscale
from app.moteur.consequence import Consequence
from app.moteur.jsonlogic import lire

__all__ = ["valoriser_fiscalement"]


def valoriser_fiscalement(
    consequence: Consequence, faits: Mapping[str, Any]
) -> Decimal | None:
    """L'enjeu d'un constat de conformité, en unités monétaires du document."""
    if not isinstance(consequence, ConsequenceFiscale):
        # Une conséquence d'un autre domaine n'a rien à faire ici, mais si elle arrive on
        # ne l'invente pas : on rend None plutôt que de chiffrer au hasard.
        return None

    taxe, charge = consequence.rejette_tva, consequence.rejette_charge
    if taxe and charge:
        return lire(faits, "montants.total_ttc")
    if taxe:
        return lire(faits, "montants.total_tva")
    if charge:
        return lire(faits, "montants.total_ht")
    return None
