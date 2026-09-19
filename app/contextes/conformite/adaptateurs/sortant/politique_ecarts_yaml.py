"""Charge la politique d'écart des constats depuis le référentiel sur disque (pas 92).

Un adaptateur, et rien d'autre : il convertit le fichier en `PolitiqueDEcart` et ne
décide de rien. Ce que le cabinet permet est écrit dans
`Docs/referentiel/ecarts/politique.yaml`, pas ici.

⚠️ POURQUOI UN FICHIER ABSENT NE LÈVE PAS

Le plan de relance lève quand son fichier manque : relancer selon un calendrier inventé
enverrait de vrais messages. Ici, le sens de l'erreur est inverse. Sans fichier, la
politique est `PolitiqueDEcart.prudente()`, qui **ferme** tout : aucun constat ne peut
être écarté, et l'écran dit pourquoi. Le système reste entier (contrôle, saisie,
déclaration), il perd seulement une indulgence que personne n'a autorisée.

Un fichier **présent mais mal formé**, lui, lève : c'est une décision du cabinet mal
transcrite, et la remplacer silencieusement par la politique prudente ferait croire
au cabinet que son réglage est en vigueur.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.contextes.conformite.domaine.ecarts import PolitiqueDEcart

__all__ = ["DOSSIER_POLITIQUE", "FICHIER_POLITIQUE", "charger_la_politique_d_ecart"]

DOSSIER_POLITIQUE = "ecarts"
FICHIER_POLITIQUE = "politique.yaml"


def charger_la_politique_d_ecart(referentiel: Path) -> PolitiqueDEcart:
    """La politique du cabinet, ou la politique prudente si aucun fichier n'existe."""
    chemin = referentiel / DOSSIER_POLITIQUE / FICHIER_POLITIQUE
    if not chemin.is_file():
        return PolitiqueDEcart.prudente()
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    # `model_validate` et `extra="forbid"` : une clé mal orthographiée
    # (`second_regart`) serait sinon ignorée, et la valeur prudente par défaut
    # s'appliquerait en silence à la place de la décision du cabinet.
    return PolitiqueDEcart.model_validate(
        {**donnees, "source": f"{DOSSIER_POLITIQUE}/{FICHIER_POLITIQUE}"}
    )
