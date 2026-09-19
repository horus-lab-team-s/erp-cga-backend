"""Ce que l'échéancier demande au portail : cette obligation a-t-elle été déposée ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE (pas 58)

L'échéancier se recalcule à chaque lecture et ne lisait pas les accusés : une
déclaration déposée restait « en retard », relancée et comptée au pilotage. Voir
`generer_echeancier`.

⚠️ UNE LECTURE, PUIS DES RÉPONSES

Tous les accusés du locataire sont lus **une fois**, et indexés par référence de
dépôt. Un échéancier compte des dizaines d'obligations, et les relances parcourent
tout le portefeuille : une requête par obligation serait un millier de requêtes pour
une page.

⚠️ PAS DE REPLI SILENCIEUX

Contrairement au personnel (pas 56), une lecture des accusés qui échoue **remonte**.
Le portail appartient au socle, et une réponse inventée serait fausse dans les deux
sens : « rien n'est déposé » relancerait des adhérents à jour, « tout est déposé »
tairait des retards réels.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable

from app.contextes.transverse.api import AccuseReception, atelier

__all__ = ["accuses_du_portail"]


def accuses_du_portail() -> Callable[[str], AccuseReception | None]:
    """La question `accuse_de(reference)` pour le locataire courant, prête à être posée."""
    par_reference = {accuse.reference_document: accuse for accuse in atelier().portail.tous()}
    return par_reference.get
