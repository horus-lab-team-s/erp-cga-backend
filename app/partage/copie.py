"""Recopier une entité gelée **sans perdre ses invariants**.

─────────────────────────────────────────────────────────────────────────────────
LE PIÈGE, EN TROIS LIGNES

    validee = brouillon.model_copy(update={"etat": VALIDEE})
    validee.etat                 # VALIDEE
    validee.piece_justificative  # None

`EcritureComptable` porte pourtant un invariant explicite : *« une écriture validée
porte sa pièce justificative. Sans elle, la traçabilité est rompue dès le premier
maillon. »* Il n'a pas joué.

⚠️ **`model_copy` ne rejoue aucun validateur.** C'est écrit dans la documentation
de pydantic, et c'est même son intérêt : la copie est rapide parce qu'elle ne
vérifie rien. Le contrat est donc exactement l'inverse de celui qu'on lui prête en
le lisant dans du code métier, où il ressemble à un constructeur.

CE QUE CELA COÛTE

L'entité reste gelée, l'écriture reste immuable, tout paraît sain. L'invariant ne
se réveille qu'à la **relecture** depuis PostgreSQL, parce que le dépôt reconstruit
l'objet par `model_validate` — c'est-à-dire des jours plus tard, sur une donnée
déjà écrite, dans une pile d'appel qui ne dit pas d'où elle vient.

⚠️ Et en persistance mémoire, l'objet n'est jamais reconstruit : **l'invariant
n'existe tout simplement pas.** Les cas passent, la démonstration passe, et le
défaut n'apparaît qu'en production.

C'EST UNE TRANSITION, PAS UNE COPIE

Une entité de domaine qui change d'état ne se « recopie » pas : elle **transite**,
et une transition qui mène à un état interdit doit être refusée. Ce module ne fait
rien d'autre que de rendre cette phrase exécutable.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

__all__ = ["transiter"]

M = TypeVar("M", bound=BaseModel)


def transiter(modele: M, **champs: Any) -> M:
    """Une copie du modèle avec ces champs changés, **revalidée en entier**.

    Remplace `model_copy(update=...)` partout où le modèle porte un invariant.

    ⚠️ **Les champs sont relus depuis l'instance, pas sérialisés.** Un
    `model_dump()` traverserait les modèles imbriqués pour les reconstruire, ce qui
    est lent, et surtout il embarquerait les champs calculés — que la validation
    refuserait ensuite comme inconnus. On prend donc les valeurs telles quelles :
    pydantic accepte une instance là où il attend un modèle.

    ⚠️ Un champ inconnu lève, plutôt que d'être ignoré. `model_copy` accepte
    silencieusement `update={"etat_": ...}` et rend une copie inchangée, si bien
    qu'une faute de frappe sur un nom de champ produit une transition qui ne
    transite pas.
    """
    connus = type(modele).model_fields
    inconnus = sorted(set(champs) - set(connus))
    if inconnus:
        raise ValueError(
            f"{type(modele).__name__} n'a pas de champ {inconnus}. "
            "Une faute de frappe ici produirait une transition sans effet, et "
            "`model_copy` l'accepterait sans rien dire."
        )
    valeurs = {nom: getattr(modele, nom) for nom in connus}
    return type(modele).model_validate({**valeurs, **champs})
