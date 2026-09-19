"""Entités du contexte H · Clôture et DSF.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

La fin d'exercice : la balance définitive devient une liasse fiscale, et le
résultat comptable devient un résultat fiscal.

C'est **la sortie de toute la chaîne de valeur du produit**. Le moteur de
conformité chiffre une anomalie sur une facture d'octobre — « TVA non déductible :
379 350 FCFA » ; F la porte à la déclaration mensuelle ; et H la réintègre au
résultat fiscal de l'exercice. Sans ce dernier maillon, le chiffrage de D reste
une indication ; avec lui, il devient une ligne opposable du tableau de passage.

LE TABLEAU DE PASSAGE EST LE CŒUR

    résultat comptable
      + réintégrations   (ce que la comptabilité a déduit et que le fisc refuse)
      − déductions       (ce que le fisc admet en plus, dont l'abattement CGA)
      = résultat fiscal

Toute la difficulté du métier tient dans ces deux listes. Une réintégration
oubliée sous-estime l'impôt et expose l'adhérent au redressement ; une déduction
oubliée le fait payer trop. Chaque ligne porte donc **son origine** — quelle
facture, quelle règle, quel paramètre — parce qu'un vérificateur demandera « d'où
sort ce montant ? » et que « le logiciel l'a calculé » n'est pas une réponse.

CE QU'IL NE MODÉLISE PAS

Ni la saisie comptable, ni le contrôle de conformité : H les **lit**. Ni le dépôt
lui-même — la télétransmission n'existe pas côté administration, et H produit un
état à déposer, pas un envoi.

Aucun taux, aucun seuil en dur. Le seuil du Système Normal, l'abattement CGA et
son éventuel plafond vivent au référentiel et se lisent à la date de clôture.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ControleCoherence",
    "LigneLiasse",
    "LignePassage",
    "NaturePassage",
    "PosteLiasse",
    "SensPoste",
    "SystemeDsf",
]


class SystemeDsf(StrEnum):
    """Le référentiel de présentation de la liasse.

    ⚠️ **Le système ne se choisit pas, il se constate** — à partir du chiffre
    d'affaires de l'exercice et du seuil en vigueur. Le laisser au choix du
    comptable inviterait à retenir le plus léger, et une entité au-dessus du seuil
    déposant un SMT verrait sa liasse rejetée.
    """

    #: Système Normal : bilan, compte de résultat, TAFIRE, annexes.
    NORMAL = "NORMAL"
    #: Système Minimal de Trésorerie, réservé aux très petites entités.
    MINIMAL = "MINIMAL"


class SensPoste(StrEnum):
    """De quel côté un poste figure aux états."""

    ACTIF = "ACTIF"
    PASSIF = "PASSIF"
    CHARGE = "CHARGE"
    PRODUIT = "PRODUIT"


class PosteLiasse(BaseModel):
    """Un poste de la liasse, et les comptes SYSCOHADA qui l'alimentent.

    ─────────────────────────────────────────────────────────────────────────
    LES PRÉFIXES PLUTÔT QU'UNE LISTE DE COMPTES

    Un poste est alimenté par une famille de comptes — « 60 » pour les achats,
    « 40 » pour les fournisseurs. Énumérer les comptes un à un obligerait à
    modifier le mapping chaque fois qu'un adhérent ouvre un sous-compte, et le
    premier oubli ferait disparaître un montant de la liasse **sans que rien ne
    le signale** : le bilan resterait équilibré, il serait seulement faux.

    Le préfixe le plus long l'emporte : « 401 » gagne sur « 40 ». C'est ce qui
    permet d'isoler un sous-ensemble sans réécrire la famille entière.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    sens: SensPoste
    #: Les préfixes de compte SYSCOHADA qui alimentent ce poste.
    #:
    #: ⚠️ Peut être **vide**, et ce n'est pas une omission : les postes de tiers
    #: — fournisseurs, clients, État — ne sont pas atteints par préfixe mais par
    #: le sens du solde. Voir `COMPTES_BIDIRECTIONNELS`. Exiger au moins un
    #: préfixe obligerait à en inventer un qui ne servirait jamais, et le premier
    #: lecteur croirait que le poste s'alimente par là.
    prefixes: tuple[str, ...] = ()
    #: Le système où ce poste existe. `None` = les deux.
    systeme: SystemeDsf | None = None

    def couvre(self, compte: str) -> bool:
        return any(compte.startswith(prefixe) for prefixe in self.prefixes)

    def longueur_du_prefixe(self, compte: str) -> int:
        """La longueur du plus long préfixe correspondant, pour arbitrer."""
        return max(
            (len(p) for p in self.prefixes if compte.startswith(p)),
            default=0,
        )


class LigneLiasse(BaseModel):
    """Un poste et son montant, à la clôture."""

    model_config = ConfigDict(frozen=True)

    poste: str
    libelle: str
    sens: SensPoste
    montant: Decimal
    #: Les comptes qui l'ont alimenté — la trace vers la balance.
    comptes: tuple[str, ...] = ()


class NaturePassage(StrEnum):
    """Le sens d'une ligne du tableau de passage."""

    #: Ajoutée au résultat comptable : le fisc refuse cette déduction.
    REINTEGRATION = "REINTEGRATION"
    #: Retranchée : le fisc admet une déduction que la comptabilité n'a pas faite.
    DEDUCTION = "DEDUCTION"


class LignePassage(BaseModel):
    """Une ligne du tableau de passage du résultat comptable au résultat fiscal.

    ⚠️ `origine` n'est pas décorative. Un vérificateur demandera « d'où sort ce
    montant ? », et le tableau doit répondre sans qu'on rouvre le dossier :
    quelle facture, quelle règle de conformité, ou quel paramètre du référentiel.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    nature: NaturePassage
    montant: Decimal = Field(ge=0)
    #: La référence de la pièce, de la règle ou du paramètre à l'origine.
    origine: str | None = None
    #: Vrai si le montant repose sur une valeur légale non encore validée.
    non_valide: bool = False

    @property
    def signe(self) -> Decimal:
        """Le montant signé, pour la sommation."""
        return (
            self.montant
            if self.nature is NaturePassage.REINTEGRATION
            else -self.montant
        )


class ControleCoherence(BaseModel):
    """Un contrôle inter-états, et son verdict.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI DES CONTRÔLES ET NON DES ASSERTIONS

    Une liasse incohérente ne doit pas faire échouer le calcul : le comptable a
    besoin de la **voir** pour la corriger. Une exception lui rendrait un écran
    vide et un message technique, là où il lui faut le montant de l'écart et le
    poste qui le porte.

    Le blocage vient plus tard, au dépôt : c'est là que l'incohérence devient
    disqualifiante, parce qu'une liasse déséquilibrée est rejetée par
    l'administration.
    ─────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(frozen=True)

    code: str
    libelle: str
    satisfait: bool
    #: L'écart constaté, nul si le contrôle passe.
    ecart: Decimal = Decimal(0)
    explication: str | None = None

    @model_validator(mode="after")
    def _un_controle_satisfait_n_a_pas_d_ecart(self) -> ControleCoherence:
        if self.satisfait and self.ecart != 0:
            raise ValueError(
                f"{self.code} : contrôle déclaré satisfait avec un écart de "
                f"{self.ecart}. Un contrôle qui passe n'a pas d'écart."
            )
        return self


class Exercice(BaseModel):
    """L'exercice clôturé, réduit à ce dont H a besoin.

    Une copie locale plutôt qu'un emprunt à `portefeuille.contrats` : H a besoin
    de trois champs — les bornes et le libellé — là où l'entité du portefeuille
    en porte huit. Emprunter obligerait à construire un exercice complet pour
    calculer une liasse, y compris dans un test.
    """

    model_config = ConfigDict(frozen=True)

    libelle: str = Field(min_length=1)
    ouverture: date
    cloture: date

    @model_validator(mode="after")
    def _bornes_coherentes(self) -> Exercice:
        if self.cloture <= self.ouverture:
            raise ValueError(
                f"exercice {self.libelle} : clôture {self.cloture} antérieure ou égale "
                f"à l'ouverture {self.ouverture}."
            )
        return self
