"""Imputation : comment une opération se traduit en comptes.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME

Une facture dit « Ciment CPJ 42,5 — 250 sacs, 1 970 650 FCFA ». La comptabilité
doit dire « compte 604 — Achats stockés de matières et fournitures ». Entre les
deux, il y a une décision que personne n'a formalisée : **quel compte pour quelle
nature d'achat ?**

Cette décision n'est ni légale ni structurelle. Elle relève de la pratique du
cabinet, et elle diffère d'un dossier à l'autre : une entreprise de BTP et une
clinique n'imputent pas les mêmes achats sur les mêmes comptes.

CE QUE CE MODULE FAIT

Il rend cette décision **déclarative et éditable**, au lieu de la laisser
s'éparpiller dans du code. Une règle d'imputation est un motif et un compte ; on
les évalue dans l'ordre de priorité, et la première qui correspond gagne.

C'est le même parti pris que pour les règles de conformité, et pour la même
raison : le jour où le cabinet veut imputer le carburant sur un compte dédié,
il ajoute une règle — personne ne recompile.

CE QU'IL NE FAIT PAS

Aucune évaluation de code arbitraire. Le motif est une expression régulière
appliquée à une désignation, rien d'autre. Le domaine ne connaît ni facture, ni
fournisseur, ni contexte D : il reçoit une chaîne et rend un numéro de compte.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["PlanImputation", "RegleImputation", "resoudre_compte"]

#: Cache des expressions compilées. Une règle est évaluée sur des milliers de
#: lignes ; recompiler à chaque fois serait du gaspillage.
_CACHE: dict[str, re.Pattern[str]] = {}


def _compile(motif: str) -> re.Pattern[str]:
    compilee = _CACHE.get(motif)
    if compilee is None:
        try:
            compilee = re.compile(motif)
        except re.error as exc:
            raise ValueError(f"motif d'imputation invalide : {motif!r} ({exc})") from exc
        _CACHE[motif] = compilee
    return compilee


class RegleImputation(BaseModel):
    """« Si la désignation ressemble à ceci, alors ce compte. »

    Le motif accepte le préfixe `(?i)` pour être insensible à la casse — les
    désignations de facture sont saisies au petit bonheur, parfois tout en
    capitales.
    """

    model_config = ConfigDict(frozen=True)

    motif: str = Field(min_length=1)
    compte: str = Field(pattern=r"^[1-9][0-9]{0,7}$")
    libelle: str = Field(min_length=1)

    #: Les règles sont éprouvées de la priorité la plus haute à la plus basse.
    #: Une règle très spécifique doit passer avant une règle générale, faute de
    #: quoi elle ne se déclencherait jamais.
    priorite: int = 0

    @field_validator("motif")
    @classmethod
    def _motif_compilable(cls, valeur: str) -> str:
        _compile(valeur)
        return valeur

    def correspond(self, designation: str) -> bool:
        return _compile(self.motif).search(designation) is not None


class PlanImputation(BaseModel):
    """Les comptes que le cabinet emploie pour un dossier donné.

    ⚠️ Les numéros par défaut — 401 pour les fournisseurs, 4451 pour la TVA
    récupérable — sont des conventions du plan OHADA, structurelles et
    supranationales. Ce ne sont **pas** des valeurs légales : elles ne changent
    pas à la loi de finances, et elles ont donc leur place ici. Elles deviendront
    des attributs du plan importé au référentiel quand le contexte A portera
    `PlanComptableReference`.
    """

    model_config = ConfigDict(frozen=True)

    #: Le compte de charge employé quand aucune règle ne correspond. Obligatoire :
    #: il n'existe pas de choix universellement raisonnable, et deviner produirait
    #: une imputation fausse plutôt qu'une absence d'imputation.
    compte_charge_par_defaut: str = Field(pattern=r"^[1-9][0-9]{0,7}$")

    compte_fournisseur: str = Field(default="401", pattern=r"^[1-9][0-9]{0,7}$")
    compte_tva_deductible: str = Field(default="4451", pattern=r"^[1-9][0-9]{0,7}$")

    #: Compte recevant la TVA que l'entreprise ne récupère pas — parce qu'elle
    #: n'est pas assujettie, ou parce qu'un constat l'a rejetée.
    #:
    #: ⚠️ POINT DE VARIATION, À TRANCHER PAR LE CABINET. Laissé à `None`, la TVA
    #: non récupérable est incorporée au compte de charge d'origine, ce qui la
    #: noie dans l'achat. Renseigné, elle est portée à part et reste traçable
    #: jusqu'au tableau de passage. Le second traitement est préférable pour le
    #: produit ; le premier est la pratique la plus répandue.
    compte_tva_non_recuperable: str | None = Field(
        default=None, pattern=r"^[1-9][0-9]{0,7}$"
    )

    regles: list[RegleImputation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _comptes_coherents(self) -> PlanImputation:
        if not self.compte_charge_par_defaut.startswith("6"):
            raise ValueError(
                f"compte_charge_par_defaut {self.compte_charge_par_defaut} : un compte de "
                "charge appartient à la classe 6."
            )
        return self

    @property
    def regles_ordonnees(self) -> list[RegleImputation]:
        """De la plus prioritaire à la moins prioritaire, puis par ordre stable.

        À priorité égale, l'ordre de déclaration est conservé : le cabinet doit
        pouvoir lire son jeu de règles de haut en bas et prévoir le résultat.
        """
        return sorted(self.regles, key=lambda r: -r.priorite)


def resoudre_compte(designation: str, plan: PlanImputation) -> str:
    """Le compte de charge qui convient à cette désignation.

    La première règle qui correspond gagne. Si aucune ne correspond, le compte
    par défaut est rendu — jamais une erreur : une facture doit pouvoir être
    saisie même quand le cabinet n'a pas encore écrit la règle qui la concerne.
    C'est au réviseur de corriger l'imputation, pas au logiciel de bloquer.
    """
    for regle in plan.regles_ordonnees:
        if regle.correspond(designation):
            return regle.compte
    return plan.compte_charge_par_defaut
