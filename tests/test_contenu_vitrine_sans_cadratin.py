"""Aucun tiret cadratin dans le contenu éditorial servi au visiteur.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

Les articles, annonces et institutions du site vivent dans `Contenu_vitrine/`,
en YAML, et le serveur les rend à la vitrine. Ils ne passent par aucun des
contrôles déjà posés : ni celui des gabarits de courriel, ni celui du document
de conception, ni celui des sources du site.

⚠️ **CE SONT POURTANT EUX QUE LE VISITEUR LIT.** La vitrine garde une copie de
secours dans `app/lib/blog.ts`, pour rester debout quand le serveur ne répond
pas, mais tant qu'il répond c'est ce dossier-ci qui sort.

Vu en corrigeant la copie de secours puis en relisant la page servie : le titre
« Pas encore de clients, donc pas d'obligations fiscales (cadratin) vrai ou
faux ? » s'affichait toujours. Corriger le secours et croire la règle appliquée
est exactement le piège que ce fichier ferme.

⚠️ **LES COMMENTAIRES YAML SONT ÉPARGNÉS**, comme les commentaires de code : ils
ne sont lus que par qui édite le fichier, et la ponctuation y est un outil de
lecture. Ce qui est contrôlé, c'est ce qui sort.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pathlib

import pytest

CADRATIN = "—"
DOSSIER = pathlib.Path(__file__).resolve().parent.parent / "Contenu_vitrine"


def _fichiers() -> list[pathlib.Path]:
    return sorted(DOSSIER.glob("*.yaml"))


def test_le_balayage_voit_quelque_chose() -> None:
    """⚠️ Une garde qui ne regarde rien passe toujours."""
    assert len(_fichiers()) >= 3, f"aucun contenu trouvé sous {DOSSIER}"


@pytest.mark.parametrize("fichier", _fichiers(), ids=lambda f: f.name)
def test_aucun_cadratin_hors_commentaire(fichier: pathlib.Path) -> None:
    fautives = [
        f"{fichier.name}:{n}  {ligne.strip()[:110]}"
        for n, ligne in enumerate(fichier.read_text().splitlines(), 1)
        if CADRATIN in ligne and not ligne.lstrip().startswith("#")
    ]
    assert not fautives, "tiret cadratin dans le contenu servi :\n  " + "\n  ".join(fautives)


def test_la_garde_distingue_bien_un_commentaire_d_un_contenu() -> None:
    """⚠️ La contre-épreuve, sans quoi le zéro ci-dessus ne prouve rien."""
    def fautives(lignes: list[str]) -> list[str]:
        return [
            ligne
            for ligne in lignes
            if CADRATIN in ligne and not ligne.lstrip().startswith("#")
        ]

    assert fautives([f'  titre: "Vrai {CADRATIN} ou faux ?"'])
    assert not fautives([f"# une incise {CADRATIN} comme celle-ci {CADRATIN} reste permise"])
