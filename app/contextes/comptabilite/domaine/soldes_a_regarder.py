"""Les soldes à regarder dans une balance : ceux dont le sens contredit la nature du
compte (18 octobre 2026, point 15).

─────────────────────────────────────────────────────────────────────────────────
« Un agent IA comptable. » Avant l'agent, ce que le plan SYSCOHADA dit à lui seul : une
caisse ne peut pas être créditrice, un fournisseur débiteur est une avance ou un double
paiement, un compte d'attente se solde avant la clôture. Ces règles ne demandent ni clé
ni réseau ; elles tournent sur chaque balance, et l'agent les reçoit comme point de
départ.

⚠️ UN SOLDE À REGARDER N'EST PAS UNE ERREUR. Un découvert bancaire existe ; un client
peut avoir versé une avance. Le relevé dit où regarder et pourquoi, le comptable juge.
Aucune écriture ne s'en déduit toute seule.

⚠️ LES EXCEPTIONS DU PLAN sont tenues ici, et elles sont la raison d'être du module :
les rabais obtenus (`60x9`) sont créditeurs par nature, les rabais accordés (`70x9`)
débiteurs, et les variations de stocks (`603`, `73`) vont dans les deux sens.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contextes.comptabilite.domaine.projections import SoldeCompte

__all__ = ["SoldeARegarder", "soldes_a_regarder"]


@dataclass(frozen=True)
class SoldeARegarder:
    compte: str
    solde: Decimal
    constat: str
    suggestion: str


def _rabais(compte: str, racine: str) -> bool:
    """`6019`, `6029`… ou `7019`… : les rabais, remises et ristournes, de sens contraire."""
    return compte.startswith(racine) and len(compte) >= 4 and compte[3] == "9"


def _regle(s: SoldeCompte, *, cloture: bool) -> tuple[str, str] | None:
    c, debiteur, crediteur = s.compte, s.solde > 0, s.solde < 0
    if c.startswith("57") and crediteur:
        return (
            "La caisse est créditrice : on ne sort pas d'une caisse plus qu'on n'y a mis.",
            "Chercher une sortie saisie sans son entrée, ou un encaissement oublié.",
        )
    if c.startswith("40") and not c.startswith("409") and debiteur:
        return (
            "Un compte fournisseur est débiteur.",
            "Avance versée ou paiement en double : à vérifier, et une avance se reclasse en 409.",
        )
    if c.startswith("41") and not c.startswith("419") and crediteur:
        return (
            "Un compte client est créditeur.",
            "Avance reçue ou facture non saisie : à vérifier, et une avance se reclasse en 419.",
        )
    if c.startswith("52") and crediteur:
        return (
            "Le compte bancaire est créditeur : c'est un découvert.",
            "Le rapprocher du relevé ; à la clôture, le présenter en trésorerie passif.",
        )
    if c.startswith("58") and s.solde != 0:
        return (
            "Un virement interne n'est pas soldé.",
            "Chercher l'autre côté du virement (la banque ou la caisse qui l'a reçu).",
        )
    if c.startswith("471") and s.solde != 0 and cloture:
        return (
            "Le compte d'attente n'est pas soldé à la clôture.",
            "Imputer chaque montant à son compte définitif avant d'arrêter les comptes.",
        )
    if c.startswith(("28", "29", "39", "49", "59")) and debiteur:
        return (
            "Un compte d'amortissement ou de dépréciation est débiteur.",
            "Reprise supérieure à la dotation, ou erreur de sens : à vérifier.",
        )
    if c.startswith("6") and crediteur and not c.startswith("603") and not _rabais(c, "60"):
        return (
            "Un compte de charges est créditeur.",
            "Extourne sans écriture d'origine, ou erreur de sens : à vérifier.",
        )
    if c.startswith("7") and debiteur and not c.startswith("73") and not _rabais(c, "70"):
        return (
            "Un compte de produits est débiteur.",
            "Avoir sans vente d'origine, ou erreur de sens : à vérifier.",
        )
    return None


def soldes_a_regarder(soldes: list[SoldeCompte], *, cloture: bool) -> list[SoldeARegarder]:
    """`cloture` : la balance arrête un exercice clos, et ce qui doit s'y solder compte."""
    releve = []
    for s in soldes:
        regle = _regle(s, cloture=cloture)
        if regle is not None:
            releve.append(SoldeARegarder(s.compte, s.solde, *regle))
    return releve
