"""Évaluateur JSONLogic restreint.

Pourquoi un évaluateur maison plutôt qu'une dépendance : les portages Python de
JSONLogic disponibles ne sont plus maintenus, et le domaine fiscal exige de savoir
exactement ce qu'un prédicat peut faire. Ici la liste des opérateurs est explicite et
close — toute clé inconnue lève une erreur au lieu d'être silencieusement ignorée.

Aucun `eval`, aucune exécution de code arbitraire. Un prédicat est une structure de
données, pas un programme.

Convention du projet, valable partout : un prédicat de règle **exprime la conformité**.
Vrai = conforme. Faux = constat émis.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["evaluer", "ErreurPredicat", "OPERATEURS_AUTORISES"]


class ErreurPredicat(Exception):
    """Prédicat malformé, opérateur inconnu, ou paramètre non résolu."""


# Cache des expressions régulières compilées : une règle est évaluée sur des milliers
# de factures, recompiler à chaque ligne serait du gaspillage.
_CACHE_REGEX: dict[str, re.Pattern[str]] = {}


def _compile(motif: str) -> re.Pattern[str]:
    compilee = _CACHE_REGEX.get(motif)
    if compilee is None:
        try:
            compilee = re.compile(motif)
        except re.error as exc:  # motif fourni par le référentiel, donc faillible
            raise ErreurPredicat(f"expression régulière invalide : {motif!r} ({exc})") from exc
        _CACHE_REGEX[motif] = compilee
    return compilee


# ── Vérité et comparaison ────────────────────────────────────────────────────────


def _vrai(valeur: Any) -> bool:
    """Vérité au sens JSONLogic : 0, chaîne vide, liste vide et None sont faux."""
    if isinstance(valeur, (list, tuple, dict, str)):
        return len(valeur) > 0
    if valeur is None:
        return False
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, (int, float, Decimal)):
        return valeur != 0
    return True


def _nombre(valeur: Any) -> Decimal | None:
    """Convertit en Decimal si possible. Les booléens ne sont PAS des nombres ici :
    confondre True et 1 dans un contrôle fiscal produit des faux positifs."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, Decimal):
        return valeur
    if isinstance(valeur, int):
        return Decimal(valeur)
    if isinstance(valeur, float):
        return Decimal(str(valeur))
    if isinstance(valeur, str):
        try:
            return Decimal(valeur.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _egal_souple(gauche: Any, droite: Any) -> bool:
    if type(gauche) is type(droite):
        return bool(gauche == droite)
    if isinstance(gauche, bool) or isinstance(droite, bool):
        return gauche is droite
    a, b = _nombre(gauche), _nombre(droite)
    if a is not None and b is not None:
        return a == b
    return bool(gauche == droite)


def _comparer(operateur: str, args: list[Any]) -> bool:
    """Comparaison numérique. Supporte la forme ternaire `a < b < c` de JSONLogic."""
    if len(args) not in (2, 3):
        raise ErreurPredicat(f"« {operateur} » attend 2 ou 3 opérandes, {len(args)} reçus")
    nombres = [_nombre(a) for a in args]
    if any(n is None for n in nombres):
        # Une comparaison contre une donnée absente est FAUSSE, jamais une exception :
        # une facture peut légitimement manquer d'un champ, c'est ce que la règle teste.
        return False
    paires = list(zip(nombres, nombres[1:], strict=False))
    tests = {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
    }
    return all(tests[operateur](a, b) for a, b in paires)  # type: ignore[operator, arg-type]


# ── Accès aux données ────────────────────────────────────────────────────────────


def _lire_var(chemin: Any, donnees: Any, defaut: Any = None) -> Any:
    if chemin is None or chemin == "":
        return donnees
    courant: Any = donnees
    for segment in str(chemin).split("."):
        if isinstance(courant, dict):
            if segment not in courant:
                return defaut
            courant = courant[segment]
        elif isinstance(courant, (list, tuple)):
            try:
                courant = courant[int(segment)]
            except (ValueError, IndexError):
                return defaut
        else:
            return defaut
    return defaut if courant is None else courant


def _manquants(cles: list[Any], donnees: Any) -> list[Any]:
    return [c for c in cles if not _vrai(_lire_var(c, donnees))]


# ── Évaluation ───────────────────────────────────────────────────────────────────

# Opérateurs qui reçoivent leurs arguments déjà évalués.
_SIMPLES = {
    "==",
    "!=",
    "===",
    "!==",
    ">",
    ">=",
    "<",
    "<=",
    "!",
    "!!",
    "+",
    "-",
    "*",
    "/",
    "%",
    "in",
    "cat",
    "substr",
    "regex",
    "min",
    "max",
}
# Opérateurs à évaluation paresseuse ou à portée de données propre.
_SPECIAUX = {"var", "missing", "if", "?:", "and", "or", "some", "none", "all", "map", "filter"}

OPERATEURS_AUTORISES: frozenset[str] = frozenset(_SIMPLES | _SPECIAUX)


def evaluer(regle: Any, donnees: Any = None) -> Any:
    """Évalue une expression JSONLogic sur un dictionnaire de données."""
    if donnees is None:
        donnees = {}

    if isinstance(regle, list):
        return [evaluer(element, donnees) for element in regle]
    if not isinstance(regle, dict):
        return regle
    if len(regle) != 1:
        raise ErreurPredicat(
            f"un nœud JSONLogic porte exactement un opérateur, reçu : {sorted(regle)}"
        )

    operateur, brut = next(iter(regle.items()))

    if operateur == "param":
        raise ErreurPredicat(
            f"paramètre « {brut} » non résolu. Les références au référentiel doivent être "
            "résolues avant évaluation — voir resolution.resoudre_parametres()."
        )
    if operateur not in OPERATEURS_AUTORISES:
        raise ErreurPredicat(
            f"opérateur « {operateur} » non autorisé. "
            f"Autorisés : {', '.join(sorted(OPERATEURS_AUTORISES))}"
        )

    args = brut if isinstance(brut, list) else [brut]

    # ── Opérateurs à portée ou évaluation paresseuse ──────────────────────────
    if operateur == "var":
        chemin = evaluer(args[0], donnees) if args else None
        defaut = evaluer(args[1], donnees) if len(args) > 1 else None
        return _lire_var(chemin, donnees, defaut)

    if operateur == "missing":
        cles = evaluer(args[0], donnees) if len(args) == 1 and isinstance(args[0], dict) else args
        return _manquants(list(cles) if isinstance(cles, (list, tuple)) else [cles], donnees)

    if operateur in ("if", "?:"):
        i = 0
        while i < len(args) - 1:
            if _vrai(evaluer(args[i], donnees)):
                return evaluer(args[i + 1], donnees)
            i += 2
        return evaluer(args[i], donnees) if i < len(args) else None

    if operateur == "and":
        resultat: Any = True
        for arg in args:
            resultat = evaluer(arg, donnees)
            if not _vrai(resultat):
                return resultat
        return resultat

    if operateur == "or":
        resultat = False
        for arg in args:
            resultat = evaluer(arg, donnees)
            if _vrai(resultat):
                return resultat
        return resultat

    if operateur in ("some", "none", "all", "map", "filter"):
        collection = evaluer(args[0], donnees)
        if not isinstance(collection, (list, tuple)):
            collection = []
        expression = args[1] if len(args) > 1 else None
        if operateur == "map":
            return [evaluer(expression, element) for element in collection]
        if operateur == "filter":
            return [e for e in collection if _vrai(evaluer(expression, e))]
        verifies = (_vrai(evaluer(expression, element)) for element in collection)
        if operateur == "some":
            return any(verifies)
        if operateur == "none":
            return not any(verifies)
        # `all` sur une collection vide vaut faux en JSONLogic, contrairement à Python.
        return bool(collection) and all(verifies)

    # ── Opérateurs à arguments évalués ────────────────────────────────────────
    valeurs = [evaluer(arg, donnees) for arg in args]
    return _appliquer(operateur, valeurs)


def _appliquer(operateur: str, v: list[Any]) -> Any:  # noqa: C901 — table d'opérateurs
    if operateur == "==":
        return _egal_souple(v[0], v[1])
    if operateur == "!=":
        return not _egal_souple(v[0], v[1])
    if operateur == "===":
        return type(v[0]) is type(v[1]) and v[0] == v[1]
    if operateur == "!==":
        return not (type(v[0]) is type(v[1]) and v[0] == v[1])
    if operateur in (">", ">=", "<", "<="):
        return _comparer(operateur, v)
    if operateur == "!":
        return not _vrai(v[0] if v else None)
    if operateur == "!!":
        return _vrai(v[0] if v else None)

    if operateur == "+":
        return sum((_nombre(x) or Decimal(0) for x in v), Decimal(0))
    if operateur == "-":
        if len(v) == 1:
            return -(_nombre(v[0]) or Decimal(0))
        return (_nombre(v[0]) or Decimal(0)) - (_nombre(v[1]) or Decimal(0))
    if operateur == "*":
        produit = Decimal(1)
        for x in v:
            produit *= _nombre(x) or Decimal(0)
        return produit
    if operateur in ("/", "%"):
        gauche, droite = _nombre(v[0]), _nombre(v[1])
        if gauche is None or droite is None or droite == 0:
            return None
        return gauche / droite if operateur == "/" else gauche % droite

    if operateur == "min":
        nombres = [n for n in (_nombre(x) for x in v) if n is not None]
        return min(nombres) if nombres else None
    if operateur == "max":
        nombres = [n for n in (_nombre(x) for x in v) if n is not None]
        return max(nombres) if nombres else None

    if operateur == "in":
        aiguille, botte = v[0], v[1]
        if botte is None:
            return False
        if isinstance(botte, str):
            return str(aiguille) in botte
        return aiguille in botte
    if operateur == "cat":
        return "".join("" if x is None else str(x) for x in v)
    if operateur == "substr":
        texte = "" if v[0] is None else str(v[0])
        debut = int(v[1])
        if len(v) < 3:
            return texte[debut:]
        longueur = int(v[2])
        return texte[debut:][:longueur] if longueur >= 0 else texte[debut:longueur]

    if operateur == "regex":
        if len(v) != 2:
            raise ErreurPredicat("« regex » attend exactement 2 opérandes : chaîne et motif")
        sujet, motif = v
        if sujet is None or motif is None:
            return False
        return _compile(str(motif)).search(str(sujet)) is not None

    raise ErreurPredicat(f"opérateur « {operateur} » déclaré autorisé mais non implémenté")
