"""Ce qu'une règle déclare qu'il advient, et comment on le chiffre.

LE PRINCIPE : LA CONSÉQUENCE DÉCRIT, ELLE N'APPLIQUE PAS

Une règle ne rejette pas une taxe, n'ajoute pas de points et ne suspend personne. Elle
*décrit* ce qui devrait advenir, et un service en aval décide d'appliquer ou non. C'est
ce qui rend le noyau testable sans base et sans effet de bord, et c'est aussi ce qui
permet à un réviseur d'écarter un constat sans que le système ait déjà agi.

QUATRE NATURES, UN SEUL MÉCANISME

    MONTANT      une somme, calculée à partir des faits du sujet
    POINTS       un score, déclaré par la règle
    NIVEAU       une classification ordonnée, déclarée par la règle
    AJUSTEMENT   une variation appliquée à une base, déclarée par la règle

Trois des quatre portent leur valeur dans la règle elle-même : elles se chiffrent sans
regarder le sujet. Seul MONTANT réclame une **valorisation**, parce que la somme en jeu
dépend de ce que porte le sujet — pour une facture, selon que la taxe, la charge ou les
deux sont rejetées, l'enjeu vaut la taxe, le montant hors taxes ou le total.

C'est pourquoi la valorisation est un port et non une fonction : chaque domaine sait
chiffrer les siennes, le noyau n'a pas à le savoir.

CE QUE LE NOYAU IGNORE VOLONTAIREMENT

Ce qu'est une taxe, une monnaie, un barème. `unite` est une chaîne libre parce qu'une
unité est une affaire de domaine : « FCFA » ici, « points » là, « jours-homme » ailleurs.
Le noyau ne convertit rien et ne compare jamais deux unités.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = [
    "Consequence",
    "TypeConsequence",
    "Valorisation",
    "enjeu_declare",
    "sans_enjeu",
]


class TypeConsequence(StrEnum):
    """La nature de ce qu'une règle déclare.

    Elle commande la façon de chiffrer, pas la façon d'appliquer : appliquer reste
    l'affaire du service en aval, quel que soit le type.
    """

    #: Une somme d'argent, calculée à partir des faits du sujet.
    MONTANT = "MONTANT"
    #: Un score, additionné par l'agrégateur. Employé par l'évaluation de charge.
    POINTS = "POINTS"
    #: Une classification ordonnée. L'agrégateur retient la plus élevée.
    NIVEAU = "NIVEAU"
    #: Une variation appliquée à une base, en valeur absolue ou en proportion.
    AJUSTEMENT = "AJUSTEMENT"


class Consequence(BaseModel):
    """Ce qu'une règle déclare qu'il advient, sans l'appliquer.

    Un domaine étend cette classe pour y ajouter ce qui lui est propre. La conséquence
    fiscale y ajoute la déductibilité de la taxe et de la charge ; une conséquence de
    contrôle interne y ajouterait le propriétaire du risque.
    """

    model_config = ConfigDict(frozen=True)

    type: TypeConsequence
    #: Ce qu'on affiche à l'utilisateur. « TVA non déductible », « Séparation des tâches
    #: non respectée ». Sans lui, un constat chiffré n'apprend rien à qui le lit.
    libelle: str | None = None
    #: L'unité de l'enjeu. Une valeur sans unité invite à comparer des francs à des
    #: pourcentages, ce qui finit toujours par arriver.
    unite: str | None = None
    #: Pour POINTS et AJUSTEMENT : la valeur que la règle déclare.
    valeur_numerique: Decimal | None = None
    #: Pour NIVEAU : la classification que la règle déclare. « ELEVE », « MODERE ».
    valeur_nominale: str | None = None

    @model_validator(mode="after")
    def _la_valeur_correspond_au_type(self) -> Consequence:
        if self.type is TypeConsequence.NIVEAU and not self.valeur_nominale:
            raise ValueError("une conséquence de type NIVEAU déclare une valeur nominale")
        if self.type is TypeConsequence.POINTS and self.valeur_numerique is None:
            raise ValueError("une conséquence de type POINTS déclare une valeur numérique")
        return self


class Valorisation(Protocol):
    """Comment un domaine chiffre l'enjeu d'une de ses conséquences.

    Reçoit les faits du sujet, pas le sujet : c'est ce qui empêche une valorisation
    d'aller chercher autre chose que ce que le schéma déclare, et donc de dépendre en
    cachette d'un champ que les règles n'ont pas le droit d'interroger.

    Rend `None` quand le constat reste qualitatif. C'est un cas normal, pas un échec :
    tout ce qui est vrai ne se chiffre pas, et afficher zéro laisserait croire à un
    enjeu nul là où il n'y a pas d'enjeu du tout.
    """

    def __call__(
        self, consequence: Consequence, faits: Mapping[str, Any]
    ) -> Decimal | None: ...


def sans_enjeu(consequence: Consequence, faits: Mapping[str, Any]) -> Decimal | None:
    """Valorisation par défaut : le constat reste qualitatif.

    C'est le comportement à retenir tant qu'un domaine n'a pas décidé comment chiffrer.
    Mieux vaut un constat sans montant qu'un montant inventé.
    """
    return None


def enjeu_declare(consequence: Consequence, faits: Mapping[str, Any]) -> Decimal | None:
    """Valorisation des conséquences qui portent leur valeur : POINTS et AJUSTEMENT.

    Elle ne regarde pas les faits, et c'est normal — un critère de charge qui vaut dix-huit
    points en vaut dix-huit quel que soit le dossier. Ce sont les prédicats qui décident
    si la règle s'applique ; la valeur, elle, est fixée par le barème.
    """
    return consequence.valeur_numerique
