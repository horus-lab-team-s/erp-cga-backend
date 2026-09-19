"""La sonde du contexte B : la grille de charge se lit-elle ?"""

from __future__ import annotations

from app.contextes.portefeuille.adaptateurs.sortant.grille_de_charge import (
    charger_la_grille,
    charger_les_tranches,
)
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_du_portefeuille"]


def sonde_du_portefeuille() -> Verdict | None:
    """La grille d'évaluation de charge et ses tranches se chargent-elles ?

    ⚠️ **Suspect et non panne.** Le portefeuille sait dire qui est qui sans sa
    grille de charge : une grille absente empêche d'évaluer un dossier, pas de le
    rattacher. Rendre une panne retirerait du trafic un service qui répond encore à
    l'essentiel.
    """
    dossier = configuration().dossier_referentiel / "charge"
    try:
        grille = charger_la_grille(dossier)
        tranches = charger_les_tranches(dossier)
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.suspect(
            f"la grille de charge ne se charge pas ({type(panne).__name__}) : "
            f"l'évaluation d'un dossier échouera. Vérifier {dossier}"
        )
    if not grille or not tranches:
        return Verdict.suspect(
            f"grille de charge ou tranches vides dans {dossier} : aucun dossier ne "
            "pourra être évalué"
        )
    return None
