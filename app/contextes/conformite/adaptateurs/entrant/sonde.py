"""La sonde du contexte D : le moteur peut-il contrôler quelque chose ?"""

from __future__ import annotations

from app.contextes.conformite.adaptateurs.sortant.depot_regles_yaml import charger_regles
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_conformite"]


def sonde_de_la_conformite() -> Verdict | None:
    """Les paquets de règles se chargent-ils, et y en a-t-il ?

    ⚠️ **Une panne franche, et non une suspicion.** Sans règles, rien n'est vérifié
    avant enregistrement, et le centre engage son agrément à l'aveugle. Aucune
    explication innocente : un dossier de règles vide est une installation
    incomplète.
    """
    dossier = configuration().dossier_referentiel / "regles"
    try:
        regles = charger_regles(dossier)
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.panne(
            f"les paquets de règles ne se chargent pas ({type(panne).__name__}) : "
            f"aucune facture ne sera contrôlée. Vérifier {dossier}"
        )
    if not regles:
        return Verdict.panne(
            f"aucune règle chargée depuis {dossier} : rien ne serait vérifié avant "
            "enregistrement, et le centre engagerait son agrément à l'aveugle"
        )
    return None
