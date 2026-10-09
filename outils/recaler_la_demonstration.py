"""Remet le dossier de démonstration à jour de ses déclarations, sans rien effacer.

    CGA_PERSISTANCE=postgresql CGA_URL_BASE_DE_DONNEES=… python -m outils.recaler_la_demonstration

Pour une base DÉJÀ amorcée : l'amorçage ne se rejoue pas sur une base qui a des
données, et recréer la base effacerait tout ce qui y a été saisi. Cet outil ajoute
seulement les accusés qui manquent au dossier SARL BATIMENT PLUS pour être à jour
au jour dit (voir `app/demonstration_a_jour.py`). Rejouable : une seconde exécution
le même jour n'ajoute rien.
"""

from __future__ import annotations

from app.demonstration_a_jour import mettre_la_demonstration_a_jour


def main() -> None:
    ajoutes = mettre_la_demonstration_a_jour()
    print(f"{ajoutes} accusé(s) de dépôt ajouté(s) au dossier de démonstration.")


if __name__ == "__main__":
    main()
