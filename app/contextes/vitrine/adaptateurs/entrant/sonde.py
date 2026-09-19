"""La sonde du contexte L : le site a-t-il quelque chose à montrer ?"""

from __future__ import annotations

from app.contextes.vitrine.adaptateurs.sortant.depot_yaml import (
    DepotContenuVitrineYaml,
)
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_vitrine"]


def sonde_de_la_vitrine() -> Verdict | None:
    """Le contenu éditorial se charge-t-il ?

    ⚠️ **Suspect et non panne.** Un site sans article reste un site : les pages
    fixes répondent, le formulaire de contact fonctionne, et rien de ce qui est déjà
    vendu ne s'interrompt. Ce qui cesse, c'est l'arrivée de nouveaux prospects,
    et cela mérite un regard sans mériter une alarme.
    """
    dossier = configuration().dossier_contenu_vitrine
    try:
        depot = DepotContenuVitrineYaml(dossier)
        articles = depot.charger_articles()
    except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
        return Verdict.suspect(
            f"le contenu de la vitrine ne se charge pas ({type(panne).__name__}) : "
            f"le site ne montrera rien de neuf. Vérifier {dossier}"
        )
    if not articles:
        return Verdict.suspect(
            f"aucun article publié dans {dossier} : le site répond mais ne montre "
            "rien de neuf, et aucun prospect n'arrivera par ce chemin"
        )
    return None
