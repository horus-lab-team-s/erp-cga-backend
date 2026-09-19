"""La boucle : appliquer un jeu de règles à un sujet, à une date.

C'est le dernier morceau à quitter le domaine. Tant qu'elle vivait dans le contexte
Conformité, « le même moteur » était une figure de style : les quatre usages auraient
partagé des briques, pas un mécanisme.

CE QUE LA BOUCLE FAIT, ET RIEN D'AUTRE

    filtrer par date de vigueur    une facture de 2024 se juge sur les règles de 2024
    filtrer par portée             une règle hors sujet n'est ni évaluée ni comptée
    résoudre les paramètres        les valeurs légales sont lues à la date, pas aujourd'hui
    évaluer le prédicat            vrai = conforme, faux = déclenchement
    chiffrer                       par la valorisation du domaine
    collecter les échecs           une règle qui casse est signalée, jamais tue

Elle ne construit aucun constat et ne conclut rien. Elle rend des **déclenchements** que
le domaine habille à sa façon, et que son agrégateur résume. C'est ce qui lui permet de
servir un rapport de conformité riche et un simple score de charge sans les connaître.

POURQUOI TROIS PORTS ET NON TROIS ARGUMENTS

`resolveur`, `valorisation` et `ignorer` sont des fonctions fournies par le domaine.
Chacune remplace un endroit où la boucle savait quelque chose de la fiscalité : comment
lire un paramètre daté, comment chiffrer une conséquence, et ce qui rend une règle
inapplicable. Les passer plutôt que les coder est exactement ce qui distingue ce module
d'un moteur de conformité déguisé.

CE QUI N'EST PAS ICI, ET POURQUOI

La notion de statut de validation. Une règle abrogée doit être écartée, mais « abrogée »
est un vocabulaire de référentiel, pas de moteur. Le domaine passe `ignorer`, et la boucle
ne sait pas ce qu'elle écarte.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.moteur.consequence import Consequence, Valorisation, sans_enjeu
from app.moteur.jsonlogic import ErreurPredicat, evaluer

__all__ = [
    "Declenchement",
    "Echec",
    "Evaluation",
    "RegleEvaluable",
    "Resolveur",
    "evaluer_regles",
    "sans_resolution",
]


@runtime_checkable
class RegleEvaluable(Protocol):
    """Ce que la boucle exige d'une règle.

    Le libellé, la sévérité, le message et la remédiation n'y figurent pas : ils servent
    à présenter un constat, pas à l'établir. Chaque domaine les porte comme il l'entend.
    """

    code: str
    predicat: Mapping[str, Any]
    consequence: Consequence

    def en_vigueur(self, a_la_date: date) -> bool: ...

    def concerne(self, faits: Mapping[str, Any]) -> bool: ...


class Resolveur(Protocol):
    """Comment un domaine remplace les références du prédicat par des valeurs datées.

    Rend le prédicat résolu et ce qui a servi à le résoudre. Le second sert à rendre
    l'évaluation rejouable : sans la liste des valeurs employées, un résultat de l'an
    dernier n'est plus explicable.
    """

    def __call__(
        self, predicat: Mapping[str, Any], a_la_date: date
    ) -> tuple[Mapping[str, Any], Sequence[Any]]: ...


def sans_resolution(
    predicat: Mapping[str, Any], a_la_date: date
) -> tuple[Mapping[str, Any], Sequence[Any]]:
    """Résolveur par défaut : le prédicat se suffit à lui-même.

    C'est le cas d'un domaine dont les seuils sont dans les règles plutôt qu'au
    référentiel — une grille de charge interne, par exemple, dont les paliers
    appartiennent au centre et non à la loi.
    """
    return predicat, ()


@dataclass(frozen=True)
class Declenchement:
    """Une règle dont le prédicat a rendu faux, avec son enjeu chiffré.

    On garde la règle entière plutôt que son code : le domaine en a besoin pour bâtir
    son constat, et la rechercher par code obligerait chaque appelant à tenir un index.
    """

    regle: Any
    enjeu: Decimal | None


@dataclass(frozen=True)
class Echec:
    """Une règle qui n'a pas pu être évaluée.

    Signalée plutôt qu'ignorée : un contrôle silencieusement absent est plus dangereux
    qu'un contrôle en erreur, parce que rien ne le distingue d'un contrôle réussi.
    """

    code_regle: str
    motif: str


@dataclass(frozen=True)
class Evaluation:
    """Ce que la boucle rend. Le domaine en tire son rapport, l'agrégateur sa conclusion."""

    declenchements: tuple[Declenchement, ...]
    echecs: tuple[Echec, ...]
    #: Combien de règles ont réellement été évaluées. Distinct du nombre de règles
    #: fournies : une règle hors portée ou hors vigueur ne compte pas, et le dire évite
    #: de croire qu'un sujet a passé cent contrôles quand il en a passé trois.
    regles_appliquees: int
    #: Ce qui a servi à résoudre les prédicats, dédoublonné par le domaine s'il le
    #: souhaite. Opaque ici : la boucle transporte sans regarder.
    references_employees: tuple[Any, ...]


def evaluer_regles(
    regles: Sequence[RegleEvaluable],
    faits: Mapping[str, Any],
    a_la_date: date,
    *,
    resolveur: Resolveur = sans_resolution,
    valorisation: Valorisation = sans_enjeu,
    ignorer: Callable[[Any], bool] | None = None,
) -> Evaluation:
    """Applique les règles en vigueur à cette date, sur ces faits.

    ⚠️ **Convention du projet : un prédicat exprime la conformité.** Vrai = rien à
    signaler. Faux = déclenchement. Elle vaut pour les quatre domaines, y compris ceux
    où « conforme » n'a pas de sens : une règle de charge rend faux quand le critère est
    atteint, ce qui se lit « ce dossier n'est pas dans le cas ordinaire ».
    """
    declenchements: list[Declenchement] = []
    echecs: list[Echec] = []
    references: list[Any] = []
    appliquees = 0

    for regle in regles:
        if not regle.en_vigueur(a_la_date) or not regle.concerne(faits):
            continue
        if ignorer is not None and ignorer(regle):
            continue

        try:
            predicat, employees = resolveur(regle.predicat, a_la_date)
            references.extend(employees)
            conforme = bool(evaluer(predicat, faits))
        except (ErreurPredicat, LookupError, TypeError, ValueError) as exc:
            # On attrape large et on continue : une règle fautive ne doit pas empêcher
            # les autres de s'appliquer. Le sujet mérite les quatre-vingt-dix-neuf
            # contrôles qui fonctionnent, et l'échec du centième est dit.
            echecs.append(Echec(code_regle=regle.code, motif=str(exc)))
            continue

        appliquees += 1
        if conforme:
            continue

        declenchements.append(
            Declenchement(regle=regle, enjeu=valorisation(regle.consequence, faits))
        )

    return Evaluation(
        declenchements=tuple(declenchements),
        echecs=tuple(echecs),
        regles_appliquees=appliquees,
        references_employees=tuple(references),
    )
