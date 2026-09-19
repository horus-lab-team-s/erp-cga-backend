"""La recherche globale : le contrat commun et la mécanique de pertinence (pas 93).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UNE RECHERCHE FÉDÉRÉE, ET NON UN INDEX CENTRAL

La barre de recherche de la coquille cherche « BATIMENT », « F-2026-0412 » ou
« fotso » partout à la fois. La tentation serait un module qui lit les dossiers, les
pièces, les comptes et les prospects pour les chercher lui-même. Il devrait importer
tous les contextes, et deviendrait **le seul endroit du système qui voit tout** :

* chaque contexte perdrait la garde de son périmètre, que ce module devrait recopier
  (un comptable ne voit que son portefeuille, un adhérent que son dossier) ;
* une panne de la collecte ferait tomber la recherche des dossiers ;
* ajouter une source obligerait à modifier ce module, puis à le redéployer.

La recherche est donc **fédérée**. Chaque service qui a quelque chose à trouver expose
sa propre route de recherche, sous son préfixe, avec sa permission et son périmètre,
et la **déclare au registre des services** (`app/registre/services.py`). L'écran lit
le registre, interroge chaque source en parallèle, et affiche ce qui répond. Une source
en panne manque à l'affichage ; les autres restent.

CE QUE CE MODULE PORTE, ET POURQUOI ICI

Le **contrat** que toutes les sources rendent (`ReponseDeRecherche`) et la **mécanique**
de correspondance (`normaliser`, `pertinence`). Il vit dans `app/partage` parce qu'il
ne connaît aucun métier : il compare des textes. S'il vivait dans un contexte, les
autres devraient l'importer, et le graphe des dépendances le refuserait.

⚠️ Deux sources qui normaliseraient chacune à leur façon rendraient « Bâtiment »
trouvable dans les dossiers et introuvable dans les pièces. Une seule fonction.

LES RÉGLAGES SONT AU RÉFÉRENTIEL

Longueur minimale d'une requête et nombre de résultats par source :
`Docs/referentiel/recherche/reglages.yaml`. Sans fichier, les valeurs prudentes de
`ReglagesDeRecherche` s'appliquent (trois caractères, dix résultats) : une requête
d'une lettre parcourrait tout le portefeuille à chaque frappe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RequeteTropCourte",
    "ReglagesDeRecherche",
    "ReponseDeRecherche",
    "ResultatDeRecherche",
    "charger_les_reglages_de_recherche",
    "classer",
    "normaliser",
    "pertinence",
    "verifier_la_requete",
]


class RequeteTropCourte(ValueError):
    """La requête compte moins de caractères significatifs que le réglage n'en exige."""


class ReglagesDeRecherche(BaseModel):
    """Ce que le cabinet règle de la recherche. Chaque défaut est le plus sobre."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Caractères significatifs minimaux (espaces et ponctuation exclus).
    longueur_minimale: int = Field(default=3, ge=2, le=10)
    #: Résultats rendus par source au plus. L'écran dit quand il y en a davantage.
    resultats_par_source: int = Field(default=10, ge=1, le=50)


def charger_les_reglages_de_recherche(referentiel: Path) -> ReglagesDeRecherche:
    """Les réglages du référentiel, ou les valeurs sobres s'il n'y a pas de fichier.

    Un fichier présent mais mal formé lève : c'est un réglage mal transcrit, et le
    remplacer en silence ferait croire au cabinet que le sien est en vigueur.
    """
    chemin = referentiel / "recherche" / "reglages.yaml"
    if not chemin.is_file():
        return ReglagesDeRecherche()
    return ReglagesDeRecherche.model_validate(
        yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    )


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents, espaces réduits. `None` devient une chaîne vide.

    « Société BÂTIMENT  Plus » et « societe batiment plus » sont la même recherche :
    au Cameroun, un nom saisi au clavier d'un téléphone perd souvent ses accents, et
    une raison sociale se retrouve écrite en capitales sur une facture.
    """
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(sans_accents.casefold().split())


def _termes(requete: str, longueur_minimale: int) -> list[str]:
    termes = normaliser(requete).split()
    significatifs = sum(len("".join(ch for ch in t if ch.isalnum())) for t in termes)
    if significatifs < longueur_minimale:
        raise RequeteTropCourte(
            f"la recherche demande au moins {longueur_minimale} caractères significatifs."
        )
    return termes


def verifier_la_requete(requete: str, reglages: ReglagesDeRecherche) -> None:
    """Lève `RequeteTropCourte` **une fois**, avant de parcourir quoi que ce soit.

    Chaque route l'appelle en tête : la refuser au premier élément comparé ferait
    dépendre la réponse (422 ou liste vide) de ce qu'il y a dans le dépôt.
    """
    _termes(requete, reglages.longueur_minimale)


def pertinence(
    requete: str,
    *,
    identifiants: Iterable[str | None] = (),
    libelles: Iterable[str | None] = (),
    longueur_minimale: int = 3,
) -> int | None:
    """La pertinence d'un élément pour cette requête, ou `None` s'il ne correspond pas.

    ⚠️ **Tous les termes doivent se trouver** quelque part dans l'élément : « batiment
    douala » ne rend pas tous les dossiers de Douala. C'est ce qu'attend quelqu'un qui
    précise sa recherche en ajoutant un mot.

    Le rang, du plus fort au plus faible, et c'est tout ce qu'il y a à savoir :

    * 100 : la requête **est** un identifiant (NIU, référence de pièce, courriel) ;
    * 80  : un identifiant **commence** par la requête ;
    * 60  : un libellé commence par la requête ;
    * 40  : chaque terme figure quelque part.

    Les identifiants passent devant parce qu'une personne qui tape une référence sait
    ce qu'elle cherche, et qu'un libellé qui la contient par hasard ne doit pas la
    devancer.
    """
    termes = _termes(requete, longueur_minimale)
    entiere = " ".join(termes)
    ids = [normaliser(i) for i in identifiants if i]
    libs = [normaliser(x) for x in libelles if x]
    tout = " ".join(ids + libs)
    if not all(terme in tout for terme in termes):
        return None
    if entiere in ids:
        return 100
    if any(i.startswith(entiere) for i in ids):
        return 80
    if any(x.startswith(entiere) for x in libs):
        return 60
    return 40


class ResultatDeRecherche(BaseModel):
    """Un élément trouvé, tel que l'écran l'affiche. **Le même pour toutes les sources.**"""

    #: La nature en clair, au singulier : « Dossier », « Pièce », « Compte ».
    nature: str
    #: Ce qui identifie l'élément dans son service : NIU, identifiant de pièce…
    identifiant: str
    titre: str
    #: Une ligne de contexte : forme juridique et centre, émetteur et montant…
    detail: str | None = None
    #: Le NIU du dossier concerné, quand il y en a un. Sert à grouper à l'écran.
    dossier: str | None = None
    #: Le chemin de l'écran qui ouvre l'élément, **sans préfixe de langue**
    #: (« /portefeuille/M081234567890P »). `None` quand aucun écran ne l'ouvre.
    lien: str | None = None
    pertinence: int = Field(ge=0, le=100)


class ReponseDeRecherche(BaseModel):
    """Ce que rend chaque route de recherche déclarée au registre."""

    #: Le nom du service au registre : l'écran groupe par source.
    service: str
    resultats: list[ResultatDeRecherche]
    #: Vrai s'il y avait plus de résultats que le réglage n'en rend : l'écran le dit,
    #: pour qu'on précise sa recherche plutôt que de croire la liste complète.
    tronque: bool = False


def classer(
    service: str, resultats: Iterable[ResultatDeRecherche], reglages: ReglagesDeRecherche
) -> ReponseDeRecherche:
    """Du plus pertinent au moins pertinent, puis par titre ; borné au réglage."""
    tries = sorted(resultats, key=lambda r: (-r.pertinence, normaliser(r.titre), r.identifiant))
    limite = reglages.resultats_par_source
    return ReponseDeRecherche(
        service=service, resultats=tries[:limite], tronque=len(tries) > limite
    )
