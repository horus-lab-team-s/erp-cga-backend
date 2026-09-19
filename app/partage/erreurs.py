"""Dire un refus en français, sans l'enrobage du validateur.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Pydantic enveloppe toute `ValueError` levée par un validateur dans une
`ValidationError`, **qui est elle-même une `ValueError`**. Une route qui écrit

    except ValueError as refus:
        raise HTTPException(status_code=409, detail=str(refus))

croit rendre la phrase du domaine, et rend ceci :

    1 validation error for DemandeDeContact
    telephone
      Value error, « +33612345678 » n'est pas un numéro camerounais exploitable.
      [type=value_error, input_value='+33612345678', input_type=str]
        For further information visit https://errors.pydantic.dev/2.13/v/value_error

La phrase utile est au milieu, entre un décompte d'erreurs, des noms de types et
une adresse web qui ne concerne personne.

⚠️ LE DÉFAUT ÉTAIT CONNU, ET CORRIGÉ À UN SEUL ENDROIT

La comptabilité avait écrit sa fonction de déballage, avec un commentaire décrivant
exactement cette trace. Elle était privée à ses routes. Onze autres sites de refus
renvoyaient `str(refus)`, dont la route publique de dépôt d'une demande de contact :
un visiteur anonyme du site qui tapait un numéro étranger lisait la trace entière.

C'est un essai réel de la vitrine, au pas 51, qui l'a montré. *Une correction qui
reste privée corrige un écran, et laisse le défaut à tous les autres.*
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pydantic import ValidationError

__all__ = ["message_lisible"]


def message_lisible(refus: Exception) -> str:
    """La phrase du domaine, dégagée de l'enrobage du validateur.

    Pour une `ValidationError`, les messages de chaque erreur, débarrassés du
    préfixe « Value error, ». Pour toute autre exception, son texte tel quel : il a
    été écrit par le domaine pour être lu.
    """
    if isinstance(refus, ValidationError):
        motifs = [
            str(erreur.get("msg", "")).removeprefix("Value error, ")
            for erreur in refus.errors()
        ]
        return " ".join(motif for motif in motifs if motif) or "requête refusée"
    return str(refus)
