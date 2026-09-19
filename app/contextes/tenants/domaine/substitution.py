"""Ce qu'une ouverture n'a pas réellement fait, et pourquoi il faut le dire.

─────────────────────────────────────────────────────────────────────────────────
LA DISTINCTION QUE CE MODULE PORTE

« La saga est terminée » et « le tenant est utilisable » ne sont **pas la même
question**, et les confondre ferait annoncer au client un espace qui ne s'ouvre
pas.

Sur les sept étapes d'une ouverture, trois demandent une infrastructure qui peut
ne pas exister dans un environnement donné : créer le schéma, ouvrir le préfixe
de stockage, créer le compte administrateur. Quand elle n'existe pas, l'étape est
**substituée** : elle ne lève pas, elle ne réussit pas silencieusement non plus,
elle inscrit son nom.

Trois comportements possibles, et un seul est honnête :

* **lever** bloquerait toutes les installations de développement sur la
  troisième étape, ce qui n'apprend rien à personne ;
* **réussir en silence** produirait un tenant marqué actif dont le schéma
  n'existe pas, mensonge que la première requête découvrirait ;
* **substituer en le déclarant** laisse la chaîne se dérouler et rend la
  différence lisible.

POURQUOI CE MODULE EST DANS LE DOMAINE

Parce que « cette ouverture est-elle réelle ? » est une question métier, posée
par le cas d'usage et par la console, et non un détail du provisionneur. Le
laisser chez l'adaptateur obligerait la couche application à importer une couche
plus externe, ce que le garde-fou d'architecture refuse, et il aurait raison :
c'est le sens des dépendances qui garantit qu'on peut changer d'infrastructure
sans toucher au métier.

Le défaut a d'ailleurs été trouvé ainsi, par le test d'architecture, au moment
d'écrire le cas d'usage.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "CLE_SUBSTITUEES",
    "avec_substitution",
    "ouverture_reellement_complete",
    "substituees",
]

#: La clé sous laquelle une étape substituée inscrit son nom dans le contexte de
#: la saga. Nommée ici pour que personne ne la retape.
CLE_SUBSTITUEES = "etapes_substituees"


def substituees(contexte: Mapping[str, Any]) -> tuple[str, ...]:
    """Les étapes qui n'ont rien fait, dans l'ordre où elles ont été traversées."""
    return tuple(contexte.get(CLE_SUBSTITUEES, ()))


def avec_substitution(contexte: Mapping[str, Any], etape: str) -> Mapping[str, Any]:
    """Inscrit une étape substituée. **Rejouable** : elle n'y figure qu'une fois.

    Sans cette garde, une reprise après incident ferait grossir la liste à chaque
    tour, et le décompte des étapes manquantes cesserait d'être lisible.
    """
    deja = list(substituees(contexte))
    if etape not in deja:
        deja.append(etape)
    return {**contexte, CLE_SUBSTITUEES: tuple(deja)}


def ouverture_reellement_complete(contexte: Mapping[str, Any]) -> bool:
    """Le tenant est-il utilisable, et pas seulement marqué actif ?

    ⚠️ **Ce n'est pas la même question que « la saga est-elle terminée ».** Une
    saga peut se terminer avec trois étapes substituées : le tenant existe en
    base, son sous-domaine répond, et il n'a ni schéma, ni stockage, ni compte.
    """
    return not substituees(contexte)
