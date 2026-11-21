"""Les règles du Centre, lues dans `cabinet/conformite_du_centre.yaml` (10 novembre 2026)."""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.pilotage.domaine.conformite_du_centre import ReglesDuCentre

__all__ = ["charger_les_regles_du_centre", "raison_sociale_du_cabinet"]


def charger_les_regles_du_centre(referentiel: Path) -> ReglesDuCentre:
    """Absent, le fichier rend les valeurs du CGI 2026 et aucun professionnel déclaré."""
    chemin = referentiel / "cabinet" / "conformite_du_centre.yaml"
    if not chemin.is_file():
        return ReglesDuCentre()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return ReglesDuCentre.model_validate(
        {**donnees, "source": "cabinet/conformite_du_centre.yaml"}
    )


def raison_sociale_du_cabinet(referentiel: Path) -> tuple[str, str | None]:
    """La raison sociale et la ligne d'agrément, pour l'en-tête de la liste des adhérents."""
    chemin = referentiel / "cabinet" / "identite_des_documents.yaml"
    if not chemin.is_file():
        return "Centre de gestion agréé", None
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    agrement = next((p for p in donnees.get("pied", []) if "Agrément" in str(p)), None)
    return str(donnees.get("raison_sociale") or "Centre de gestion agréé"), agrement
