"""Le catalogue des documents à échéance (lot 4).

Lu dans `Docs/referentiel/portefeuille/documents_a_echeance.yaml`.

Sans fichier, un catalogue vide : aucune sorte, aucune durée — rien n'est inventé.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.portefeuille.domaine.documents_a_echeance import CatalogueDesDocuments

__all__ = ["charger_le_catalogue_des_documents"]


def charger_le_catalogue_des_documents(referentiel: Path) -> CatalogueDesDocuments:
    chemin = referentiel / "portefeuille" / "documents_a_echeance.yaml"
    if not chemin.is_file():
        return CatalogueDesDocuments()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return CatalogueDesDocuments.model_validate(
        {**donnees, "source": "portefeuille/documents_a_echeance.yaml"}
    )
