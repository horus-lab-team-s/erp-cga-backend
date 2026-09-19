"""Le sujet évalué, réduit à des faits, et le schéma qui les déclare.

CE QUE LE MOTEUR ATTEND D'UN SUJET

Presque rien : qu'il sache se réduire à un dictionnaire de faits. Une facture, un
dossier client, une écriture comptable ou une demande de prestation deviennent ainsi
évaluables par le même code, sans que ce code sache lequel il tient.

C'est cette ignorance qui permet à un seul moteur de servir la conformité, la
tarification, l'évaluation de charge et le contrôle interne.

À QUOI SERT LE SCHÉMA

Il déclare les faits qu'un domaine expose : leur chemin, leur type, leur unité. Il rend
possible ce qu'aucune relecture ne garantit — **refuser une règle mal écrite au
chargement**, avant qu'elle ne rencontre un sujet réel.

Sans lui, une règle qui interroge `montants.total_htt` au lieu de `montants.total_ht`
ne fait rien de visible : le fait manquant vaut absent, la comparaison rend faux, et le
moteur émet un constat sur chaque pièce contrôlée. L'erreur est un caractère ; le
préjudice est un adhérent à qui l'on reproche une anomalie inexistante.

LA CONVENTION DES CHEMINS

Un fait se nomme par son chemin pointé depuis la racine du sujet, `montants.total_ht`.
Un fait porté par les éléments d'une collection prend le suffixe `[]` sur celle-ci,
`lignes[].designation` — la même convention que celle de l'extracteur de chemins, de
sorte que les deux se comparent sans traduction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ErreurSchema",
    "Fait",
    "SchemaDeFaits",
    "Sujet",
    "TypeFait",
]


class ErreurSchema(ValueError):
    """Le schéma est incohérent, ou une règle cite un fait qu'il ne déclare pas.

    Volontairement une erreur et non un avertissement : une règle qui interroge un fait
    inexistant produit des constats faux, et un constat faux coûte plus cher qu'une
    règle absente. On refuse le chargement.

    ⚠️ Elle ne remonte telle quelle que depuis `valider_predicat`. Levée dans un
    validateur de modèle, pydantic l'enveloppe dans sa propre `ValidationError` et n'en
    conserve que le message. C'est le comportement attendu de la bibliothèque, et le
    message reste ce qui compte : on ne se bat pas contre elle pour un type d'exception.
    """


class TypeFait(StrEnum):
    """Le type d'un fait, tel que le schéma le déclare.

    Il sert à la relecture humaine et à la validation ; le noyau ne s'en sert pas pour
    convertir quoi que ce soit. Les valeurs arrivent telles que le sujet les expose.
    """

    BOOLEEN = "BOOLEEN"
    ENTIER = "ENTIER"
    DECIMAL = "DECIMAL"
    TEXTE = "TEXTE"
    DATE = "DATE"
    ENUM = "ENUM"
    LISTE = "LISTE"


class Fait(BaseModel):
    """La déclaration d'un fait : ce que le sujet expose, sous quel nom, de quel type."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    type: TypeFait
    libelle: str = Field(min_length=1)
    #: Unité de mesure, pour les grandeurs qui en ont une. « FCFA », « jours », « pièces ».
    #: Un montant sans unité est une invitation à comparer des francs à des pourcentages.
    unite: str | None = None
    #: Valeurs admises, obligatoire pour un ENUM. Déclarées ici, elles permettent de
    #: refuser une règle qui compare à une valeur qui n'existe pas — la faute de frappe
    #: la plus fréquente après le nom du fait lui-même.
    valeurs: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _enum_declare_ses_valeurs(self) -> Fait:
        if self.type is TypeFait.ENUM and not self.valeurs:
            raise ErreurSchema(f"le fait « {self.code} » est un ENUM sans valeurs déclarées")
        if self.type is not TypeFait.ENUM and self.valeurs:
            raise ErreurSchema(f"le fait « {self.code} » déclare des valeurs sans être un ENUM")
        return self


class SchemaDeFaits(BaseModel):
    """Les faits qu'un domaine expose au moteur.

    ⚠️ **Un schéma vit là où vit son sujet.**

    La formulation d'origine disait « un schéma vit dans le code », et l'argument
    était celui-ci : un schéma décrit ce que le **sujet** expose, or le sujet est du
    code, donc ajouter un fait suppose que quelque chose l'expose désormais. Les
    sortir en donnée créerait une dérive entre deux artefacts qui doivent bouger
    ensemble, et cette dérive ne se verrait qu'à l'évaluation suivante.

    L'argument est juste, et sa conclusion trop large. Elle repose sur une prémisse
    — « le sujet est du code » — qui est vraie de la facture, du dossier à évaluer et
    de la candidature, et **fausse de la qualification commerciale**, dont le sujet
    est un questionnaire rempli par un humain. Y ajouter une question ne suppose
    aucun code : cela suppose qu'on pose une question de plus.

    La règle générale est donc :

    * **sujet en code → schéma en code**, à côté de lui, et bougeant avec lui. C'est
      le cas des trois domaines qui tournent aujourd'hui sur ce noyau ;
    * **sujet en configuration → schéma en configuration**, produit à partir d'elle.
      C'est le cas du questionnaire de qualification, dont les questions, l'ordre et
      le caractère obligatoire dépendent du service demandé et changent avec l'offre.
      Voir `app/contextes/souscription/domaine/qualification.py`.

    Ce qui ne change pas, c'est que le schéma et son sujet doivent **bouger
    ensemble**. C'est vrai des deux côtés : un schéma de code se vérifie par un test
    qui le confronte à ce que le sujet expose, un schéma configuré se vérifie parce
    que la même configuration produit les deux.

    Le noyau, lui, ne voit pas la différence : il reçoit un `SchemaDeFaits`, d'où
    qu'il vienne.
    """

    model_config = ConfigDict(frozen=True)

    domaine: str = Field(min_length=1)
    faits: tuple[Fait, ...]

    @model_validator(mode="after")
    def _aucun_code_en_double(self) -> SchemaDeFaits:
        codes = [fait.code for fait in self.faits]
        doublons = sorted({code for code in codes if codes.count(code) > 1})
        if doublons:
            raise ErreurSchema(f"faits déclarés deux fois dans « {self.domaine} » : {doublons}")
        return self

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(fait.code for fait in self.faits)

    def fait(self, code: str) -> Fait | None:
        return next((fait for fait in self.faits if fait.code == code), None)

    def inconnus(self, chemins: Iterable[str]) -> tuple[str, ...]:
        """Les chemins cités que le schéma ne déclare pas, triés."""
        return tuple(sorted(set(chemins) - self.codes))

    def valider_predicat(self, chemins: Iterable[str], origine: str) -> None:
        """Refuse un prédicat qui cite un fait non déclaré.

        `origine` nomme la règle fautive : sans elle, le message dit qu'une règle est
        fausse sans dire laquelle, ce qui oblige à les relire toutes.
        """
        manquants = self.inconnus(chemins)
        if manquants:
            proches = {chemin: self._proche(chemin) for chemin in manquants}
            details = ", ".join(
                f"« {chemin} »" + (f" (voulez-vous dire « {proche} » ?)" if proche else "")
                for chemin, proche in proches.items()
            )
            raise ErreurSchema(
                f"{origine} cite un fait que le domaine « {self.domaine} » ne déclare pas : "
                f"{details}. Déclarer le fait au schéma, ou corriger le prédicat."
            )

    def _proche(self, chemin: str) -> str | None:
        """Le fait déclaré le plus ressemblant, s'il l'est assez pour être suggéré.

        Une faute de frappe sur un chemin est l'erreur la plus fréquente et la plus
        pénible à trouver à l'œil. Suggérer coûte trois lignes et fait gagner un quart
        d'heure à chaque fois.
        """
        candidats = [(_distance(chemin, code), code) for code in sorted(self.codes)]
        if not candidats:
            return None
        distance, code = min(candidats)
        return code if distance <= max(2, len(chemin) // 4) else None


@runtime_checkable
class Sujet(Protocol):
    """Ce que le moteur exige de tout objet qu'il évalue.

    Une seule méthode, délibérément. Chaque exigence supplémentaire serait une raison de
    plus pour un domaine de ne pas pouvoir employer le moteur.
    """

    def faits(self) -> Mapping[str, Any]:
        """Le sujet réduit à des données, sous les chemins que déclare son schéma."""
        ...


def _distance(gauche: str, droite: str) -> int:
    """Distance d'édition, en gardant deux lignes plutôt que la matrice entière.

    Employée uniquement pour suggérer un nom dans un message d'erreur : la qualité de
    l'approximation importe peu, son coût non plus, mais la dépendance qu'on éviterait
    en l'important compte.
    """
    if gauche == droite:
        return 0
    precedente = list(range(len(droite) + 1))
    for index_gauche, caractere_gauche in enumerate(gauche, start=1):
        courante = [index_gauche]
        for index_droite, caractere_droite in enumerate(droite, start=1):
            courante.append(
                min(
                    precedente[index_droite] + 1,
                    courante[index_droite - 1] + 1,
                    precedente[index_droite - 1] + (caractere_gauche != caractere_droite),
                )
            )
        precedente = courante
    return precedente[-1]
