"""Pré-résolution des références au référentiel dans un prédicat.

`{"param": "SEUIL_ESPECES_DEDUCTIBILITE_TVA"}` est remplacé, **avant** évaluation, par la
valeur en vigueur à la date de l'opération.

Pourquoi pré-résoudre plutôt qu'exposer un opérateur dynamique :

1. l'évaluation reste une fonction pure, testable sans référentiel ni base ;
2. le rapport conserve la liste des paramètres employés, leur valeur et leur date d'effet
   — traçabilité défendable devant l'administration ;
3. un paramètre manquant échoue à la résolution, avant l'évaluation, avec un message
   clair, au lieu de produire un résultat faux au milieu d'un arbre booléen.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.contextes.referentiel.api import ParametreResolu, ServiceParametres, Unite

__all__ = ["resoudre_parametres"]


def resoudre_parametres(
    predicat: Any,
    parametres: ServiceParametres,
    a_la_date: date,
) -> tuple[Any, list[ParametreResolu]]:
    """Rend le prédicat débarrassé de ses références et la liste des paramètres employés.

    Les exceptions `ParametreInconnu` et `AucuneVersionApplicable` remontent telles
    quelles : l'appelant les transforme en `RegleEnEchec`.
    """
    employes: dict[str, ParametreResolu] = {}
    resolu = _parcourir(predicat, parametres, a_la_date, employes)
    return resolu, sorted(employes.values(), key=lambda p: p.code)


def _parcourir(
    noeud: Any,
    parametres: ServiceParametres,
    a_la_date: date,
    employes: dict[str, ParametreResolu],
) -> Any:
    if isinstance(noeud, list):
        return [_parcourir(element, parametres, a_la_date, employes) for element in noeud]
    if not isinstance(noeud, dict):
        return noeud

    if set(noeud) == {"param"}:
        code = noeud["param"]
        if not isinstance(code, str):
            raise TypeError(f"« param » attend un code de paramètre, reçu : {code!r}")
        resolu = parametres.resoudre(code, a_la_date)
        employes[code] = resolu
        return _litteral(resolu)

    return {
        cle: _parcourir(valeur, parametres, a_la_date, employes) for cle, valeur in noeud.items()
    }


def _litteral(resolu: ParametreResolu) -> Any:
    """Les montants deviennent des Decimal : comparer un seuil flottant à un montant en
    FCFA introduirait des écarts d'arrondi sur des sommes à sept chiffres."""
    if resolu.unite is Unite.REGEX:
        return str(resolu.valeur)
    if isinstance(resolu.valeur, bool) or isinstance(resolu.valeur, str):
        return resolu.valeur
    return Decimal(str(resolu.valeur))
