"""Les magasins mémoire du contexte. **Un seul par sorte, et ils vivent ici.**

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE FICHIER EXISTE

`routes_acquisition` portait déjà cette mise en garde, à côté de sa propre
fabrique : « En mémoire, ceci **est** la persistance : deux instances seraient deux
bases sans lien. »

Il y en avait deux. `abonne_de_relance` avait déclaré la sienne, et la
conséquence était mesurable : un dossier déposé par le formulaire public était
écrit dans le magasin des routes, l'abonné le cherchait dans le sien, et
`poster_une_relance` levait `DossierIntrouvable` sur **chaque** relance. Le relais
comptait l'échec, retenait les paliers suivants de la même proforma, puis mettait
en quarantaine. Aucune erreur de programmation n'apparaissait nulle part : deux
appels parfaitement corrects lisaient deux vérités différentes.

⚠️ **C'est la panne la plus coûteuse de sa catégorie**, parce qu'elle ne casse
rien. Un magasin vide et un magasin sans le dossier cherché se ressemblent, et la
seule trace est une quarantaine que quelqu'un doit penser à regarder.

La fabrique vit donc ici, au sortant, là où vivent les dépôts, et non chez l'un
des trois appelants. Chez l'un d'eux, les deux autres devraient l'importer, et
c'est exactement le raisonnement qui fait qu'on en déclare une seconde « pour ne
pas dépendre des routes ».

⚠️ **Ce n'est pas un mode d'exploitation.** La persistance mémoire sert au poste de
développement et aux tests ; en production la base est là et rien de ceci n'est
appelé. Cela ne rend pas le défaut anodin : c'est en développement qu'on décide
qu'un comportement est normal.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from functools import lru_cache

from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotDossiersMemoire,
    DepotProformasMemoire,
)

__all__ = [
    "dossiers_memoire",
    "proformas_memoire",
    "vider_les_dossiers_memoire",
    "vider_les_proformas_memoire",
]


@lru_cache
def dossiers_memoire() -> DepotDossiersMemoire:
    """L'unique magasin mémoire des dossiers commerciaux.

    ⚠️ `lru_cache` sans argument : la fonction n'en prend aucun, donc le cache tient
    exactement une entrée, et cette entrée est le magasin. Ce n'est pas une
    optimisation, c'est le mécanisme d'unicité lui-même.
    """
    return DepotDossiersMemoire()


def vider_les_dossiers_memoire() -> None:
    """Repart d'un magasin vierge. Destiné aux tests.

    ⚠️ Les appelants qui gardent d'autres magasins liés aux dossiers, comme les
    qualifications, doivent vider les leurs en même temps : une qualification qui
    survivrait à son dossier ferait qu'un test reprendrait des réponses données par
    le test précédent, sur une référence recyclée.
    """
    dossiers_memoire.cache_clear()


@lru_cache
def proformas_memoire() -> DepotProformasMemoire:
    """L'unique magasin mémoire des proformas.

    ⚠️ **Il était dupliqué lui aussi**, entre `routes_acquisition` et
    `travail_de_relance`, sous un commentaire qui affirmait le contraire : « Ce
    sont les seuls magasins mémoire de proformas et de suivis du projet. »

    La conséquence était la panne parfaite de sa catégorie : la route émettait une
    proforma dans un magasin, le balayage en lisait un autre, et il trouvait
    **zéro proforma à relancer** quel que soit le nombre émis. Pas d'exception,
    pas de journal, pas de quarantaine — un balayage qui rend « rien à faire » et
    un balayage qui ne voit rien se ressemblent exactement.

    Il n'a pas été trouvé en le cherchant : c'est le garde écrit pour les dossiers
    qui l'a nommé au premier passage.
    """
    return DepotProformasMemoire()


def vider_les_proformas_memoire() -> None:
    """Repart d'un magasin vierge. Destiné aux tests."""
    proformas_memoire.cache_clear()
