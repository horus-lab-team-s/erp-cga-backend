"""Ce qu'on fait d'une liste de constats une fois qu'elle est produite.

LE MOTEUR CONSTATE, L'AGRÉGATEUR CONCLUT

Évaluer un sujet rend une liste de constats. Ce qu'on en tire dépend entièrement du
domaine, et c'est là que se trouve la dernière différence entre les quatre usages :

    conformité        l'enjeu le plus élevé, sans additionner
    charge            la somme des points
    contrôle interne  le niveau de risque le plus haut, et le denombrement par niveau
    tarification      un intervalle autour d'une base

POURQUOI LA CONFORMITÉ NE FAIT PAS DE SOMME

C'est le cas qui a motivé cette séparation. Deux règles qui rejettent toutes deux la taxe
d'une même facture ne la rejettent pas deux fois : l'enjeu reste le montant de la taxe.
Additionner produirait un chiffre supérieur au préjudice réel, et un chiffre faux dans ce
sens-là est celui qu'un adhérent conteste en premier — à raison.

La charge, elle, additionne : deux critères qui pèsent chacun dix points en pèsent vingt.
Aucune règle générale ne départage ces deux comportements ; seul le domaine sait.

CE QUE L'AGRÉGATEUR N'EST PAS

Il ne décide pas. Un agrégateur qui suspendrait un dossier au delà d'un seuil mêlerait
le constat et la sanction, et la partie « constat » cesserait d'être rejouable sans effet
de bord. Il rend un `Agregat` ; ce qu'on en fait se décide ailleurs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.moteur.consequence import Consequence, TypeConsequence

__all__ = [
    "Agregat",
    "Agregateur",
    "ConstatEvalue",
    "denombrer",
    "enjeu_maximal",
    "intervalle_autour",
    "niveau_le_plus_eleve",
    "somme_des_enjeux",
]


@runtime_checkable
class ConstatEvalue(Protocol):
    """Ce qu'un agrégateur exige d'un constat, quel que soit le domaine qui l'a produit.

    Trois attributs, pas un de plus. Le libellé, le fondement et la remédiation
    intéressent celui qui lit le rapport ; ils n'entrent dans aucun calcul.
    """

    severite: Any
    enjeu: Decimal | None
    consequence: Consequence


class Agregat(BaseModel):
    """La conclusion tirée d'une liste de constats.

    Les quatre champs sont facultatifs parce qu'aucun domaine ne les emploie tous : la
    charge remplit `valeur`, le contrôle interne remplit `nominal`, la conformité les
    deux, la tarification remplit `valeur` et `intervalle`.
    """

    model_config = ConfigDict(frozen=True)

    #: Le résultat chiffré : une somme, un maximum, un prix de référence.
    valeur: Decimal | None = None
    #: Le résultat classé : un niveau de risque, une sévérité, une tranche.
    nominal: str | None = None
    #: L'unité de `valeur`. Le noyau ne convertit jamais, il transporte.
    unite: str | None = None
    #: Les bornes, quand le domaine en produit. Plancher et plafond.
    intervalle: tuple[Decimal, Decimal] | None = None
    #: Combien de constats par sévérité. Ce qu'on affiche en tête d'un rapport.
    denombrement: Mapping[str, int] = Field(default_factory=dict)


class Agregateur(Protocol):
    """Comment un domaine conclut de ses constats."""

    def __call__(self, constats: Sequence[ConstatEvalue]) -> Agregat: ...


def denombrer(constats: Sequence[ConstatEvalue]) -> dict[str, int]:
    """Combien de constats par sévérité, du plus fréquent au moins fréquent.

    Rendu par tous les agrégateurs : c'est l'information qu'on lit en premier sur un
    rapport, avant même le chiffre.
    """
    compte: dict[str, int] = {}
    for constat in constats:
        cle = str(constat.severite)
        compte[cle] = compte.get(cle, 0) + 1
    return dict(sorted(compte.items(), key=lambda paire: (-paire[1], paire[0])))


def enjeu_maximal(unite: str | None = None) -> Agregateur:
    """Le plus grand enjeu, sans addition. L'agrégateur de la conformité documentaire.

    Deux règles qui rejettent la même taxe ne la rejettent pas deux fois. Prendre le
    maximum est la seule lecture qui reste vraie quel que soit le nombre de règles qui se
    déclenchent sur la même cause.
    """

    def agreger(constats: Sequence[ConstatEvalue]) -> Agregat:
        enjeux = [c.enjeu for c in constats if c.enjeu is not None]
        return Agregat(
            valeur=max(enjeux, default=Decimal(0)),
            unite=unite,
            denombrement=denombrer(constats),
        )

    return agreger


def somme_des_enjeux(unite: str | None = None) -> Agregateur:
    """La somme des enjeux. L'agrégateur de l'évaluation de charge.

    Chaque critère pèse indépendamment des autres : un dossier qui cumule un gros volume
    de pièces et un régime complexe cumule bien les deux charges de travail.
    """

    def agreger(constats: Sequence[ConstatEvalue]) -> Agregat:
        total = sum((c.enjeu for c in constats if c.enjeu is not None), Decimal(0))
        return Agregat(valeur=total, unite=unite, denombrement=denombrer(constats))

    return agreger


def niveau_le_plus_eleve(ordre: Sequence[str]) -> Agregateur:
    """Le niveau le plus haut atteint. L'agrégateur du contrôle interne.

    `ordre` va du moins grave au plus grave. Le passer plutôt que de le deviner évite au
    noyau de supposer qu'« ELEVE » précède « CRITIQUE » alphabétiquement, ce qui est faux,
    ou que les niveaux sont des entiers, ce qui est faux aussi.

    Un niveau absent de l'ordre est ignoré du classement mais reste dénombré : mieux vaut
    un rapport incomplet sur ce point qu'une exception au moment de conclure.
    """
    rang = {niveau: position for position, niveau in enumerate(ordre)}

    def agreger(constats: Sequence[ConstatEvalue]) -> Agregat:
        connus = [c for c in constats if str(c.severite) in rang]
        plus_haut = max(
            (str(c.severite) for c in connus), key=lambda niveau: rang[niveau], default=None
        )
        return Agregat(nominal=plus_haut, denombrement=denombrer(constats))

    return agreger


def intervalle_autour(
    base: Decimal,
    baisse_maximale: Decimal,
    hausse_maximale: Decimal,
    unite: str | None = None,
) -> Agregateur:
    """Une base ajustée par les constats, encadrée par ses bornes. L'agrégateur tarifaire.

    Les constats portent des ajustements — un capital dans telle tranche, un nombre
    d'associés au delà de tant. On les applique à la base, puis on encadre.

    Les amplitudes sont des proportions, `0.20` pour vingt pour cent. Elles viennent du
    barème du domaine, pas d'ici : un intervalle en dur serait exactement la valeur
    commerciale codée dans le moteur que ce chantier cherche à éviter.
    """

    def agreger(constats: Sequence[ConstatEvalue]) -> Agregat:
        ajustements = sum(
            (
                c.enjeu
                for c in constats
                if c.enjeu is not None and c.consequence.type is TypeConsequence.AJUSTEMENT
            ),
            Decimal(0),
        )
        reference = base + ajustements
        return Agregat(
            valeur=reference,
            unite=unite,
            intervalle=(
                reference * (Decimal(1) - baisse_maximale),
                reference * (Decimal(1) + hausse_maximale),
            ),
            denombrement=denombrer(constats),
        )

    return agreger
