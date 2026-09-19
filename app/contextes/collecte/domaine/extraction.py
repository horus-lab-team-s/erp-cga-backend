"""L'extraction automatique, et la limite qu'on lui pose.

─────────────────────────────────────────────────────────────────────────────────
LA RÈGLE QUI COMMANDE CE MODULE

**Une valeur lue par une machine n'est pas une valeur tant qu'un humain ne l'a pas
retenue.**

Le score de confiance ne dit pas « ce montant est juste ». Il dit « j'ai bien lu
ces caractères-là ». Un moteur d'extraction lit `1 500 000` avec 98 % de confiance
sur une facture qui porte `1 800 000` mal imprimé : il est très sûr de sa lecture,
et il a tort. La confiance mesure la netteté de l'image, pas la véracité du
document.

Conséquence : le score sert à trier le travail humain, jamais à le supprimer.

DEUX FAMILLES DE CHAMPS, ET ELLES NE SE TRAITENT PAS PAREIL

Un libellé de ligne mal lu produit une écriture au libellé approximatif. C'est
regrettable et ça se corrige.

Un **montant** ou un **NIU** mal lu produit une déclaration fausse. Le montant part
dans la TVA du mois ; le NIU décide de la déductibilité même. Ces champs-là sont
donc à validation humaine obligatoire, **quel que soit le score** — y compris à
100 %. Ce n'est pas de la défiance envers la technique : c'est que l'erreur y est
irrattrapable en aval, et qu'aucun gain de productivité ne vaut une déclaration
fausse signée par le Centre.

LE SEUIL EST UNE POLITIQUE DU CABINET, PAS UNE VALEUR LÉGALE

`SEUIL_ACCEPTATION_AUTOMATIQUE` peut donc porter une valeur par défaut, à la
différence d'un taux ou d'un abattement. Un cabinet prudent le montera, un cabinet
débordé le baissera — et c'est un arbitrage entre le coût de la relecture et le
coût de l'erreur, qui lui appartient.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CHAMPS_A_VALIDATION_OBLIGATOIRE",
    "SEUIL_ACCEPTATION_AUTOMATIQUE",
    "ExtractionOCR",
    "ValeurExtraite",
    "ValeurNonRetenue",
]

#: En deçà, la valeur est soumise à relecture. Politique du cabinet, révisable.
SEUIL_ACCEPTATION_AUTOMATIQUE = Decimal("0.90")

#: Champs dont l'erreur se propage jusqu'à la déclaration. Relus quel que soit le
#: score — voir l'en-tête du module.
CHAMPS_A_VALIDATION_OBLIGATOIRE = frozenset(
    {
        "montant_ht",
        "montant_tva",
        "montant_ttc",
        "niu_emetteur",
        "date_emission",
    }
)


class ValeurNonRetenue(ValueError):
    """On a demandé une valeur qu'aucun humain n'a encore retenue.

    L'erreur est levée plutôt que la valeur rendue avec un avertissement : c'est
    le seul moyen d'empêcher qu'une lecture automatique se retrouve dans une
    déclaration sans que personne ne l'ait décidé. Un avertissement se contourne
    par distraction ; une exception, non.
    """


class ValeurExtraite(BaseModel):
    """Ce que la machine a lu, et ce qu'un humain en a fait."""

    model_config = ConfigDict(frozen=True)

    champ: str = Field(min_length=1)
    #: Ce que le moteur a lu. Peut être faux même à confiance maximale.
    valeur_lue: Any
    confiance: Decimal = Field(ge=0, le=1)

    #: Ce que l'humain a retenu. `None` tant que personne n'a tranché — et
    #: distinct d'une valeur retenue qui vaudrait `None`, d'où `retenue_par`.
    valeur_retenue: Any = None
    retenue_par: str | None = None
    retenue_le: datetime | None = None
    #: Vrai quand l'humain a corrigé la lecture. Ce compteur est la seule mesure
    #: honnête de la qualité réelle du moteur d'extraction — le score de confiance
    #: moyen, lui, ne mesure que la netteté des images reçues.
    corrigee: bool = False

    @property
    def validation_obligatoire(self) -> bool:
        return self.champ in CHAMPS_A_VALIDATION_OBLIGATOIRE

    @property
    def validee(self) -> bool:
        return self.retenue_par is not None

    @property
    def acceptable_automatiquement(self) -> bool:
        """Le score suffit à se passer de relecture — sauf champ sensible."""
        return (
            not self.validation_obligatoire
            and self.confiance >= SEUIL_ACCEPTATION_AUTOMATIQUE
        )

    @property
    def exploitable(self) -> bool:
        return self.validee or self.acceptable_automatiquement

    @property
    def valeur(self) -> Any:
        """La valeur à employer en aval, ou une exception.

        Il n'existe pas d'accès à la valeur qui contourne ce contrôle : c'est
        volontaire, et c'est ce qui rend la règle du module opposable plutôt que
        déclarative.
        """
        if not self.exploitable:
            raison = (
                "champ à validation obligatoire"
                if self.validation_obligatoire
                else f"confiance {self.confiance} inférieure au seuil "
                f"{SEUIL_ACCEPTATION_AUTOMATIQUE}"
            )
            raise ValeurNonRetenue(
                f"« {self.champ} » : {raison}. La lecture automatique donne "
                f"{self.valeur_lue!r}, mais personne ne l'a encore retenue. "
                "Faire valider avant d'employer cette valeur en comptabilité."
            )
        return self.valeur_retenue if self.validee else self.valeur_lue

    def retenir(self, valeur: Any, *, par: str, le: datetime) -> ValeurExtraite:
        """Un humain tranche. Rend une copie ; l'originale est gelée."""
        return self.model_copy(
            update={
                "valeur_retenue": valeur,
                "retenue_par": par,
                "retenue_le": le,
                "corrigee": valeur != self.valeur_lue,
            }
        )

    def confirmer(self, *, par: str, le: datetime) -> ValeurExtraite:
        """L'humain confirme la lecture telle quelle."""
        return self.retenir(self.valeur_lue, par=par, le=le)


class ExtractionOCR(BaseModel):
    """Le résultat complet d'une lecture automatique sur une pièce."""

    model_config = ConfigDict(frozen=True)

    piece: str = Field(min_length=1)
    moteur: str = Field(min_length=1)
    extraite_le: datetime
    champs: dict[str, ValeurExtraite] = Field(default_factory=dict)
    #: Message du moteur quand la lecture a échoué en tout ou partie. Conservé :
    #: une extraction vide sans explication est indiscernable d'une extraction
    #: jamais lancée, et l'écran de travail ne saurait laquelle relancer.
    incident: str | None = None

    @property
    def confiance_moyenne(self) -> Decimal | None:
        if not self.champs:
            return None
        total = sum((c.confiance for c in self.champs.values()), Decimal(0))
        return (total / Decimal(len(self.champs))).quantize(Decimal("0.01"))

    @property
    def champs_a_relire(self) -> list[str]:
        """Ce qu'il reste à faire valider, dans l'ordre où l'écran doit le poser :
        les champs sensibles d'abord."""
        restants = [nom for nom, c in self.champs.items() if not c.exploitable]
        return sorted(restants, key=lambda nom: (nom not in CHAMPS_A_VALIDATION_OBLIGATOIRE, nom))

    @property
    def exploitable(self) -> bool:
        """Tous les champs sont exploitables : la pièce peut alimenter un contrôle."""
        return not self.champs_a_relire

    def valeur(self, champ: str) -> Any:
        """La valeur retenue pour ce champ. Lève si elle ne l'est pas."""
        if champ not in self.champs:
            raise ValeurNonRetenue(
                f"« {champ} » n'a pas été extrait de la pièce {self.piece}. "
                "Le saisir à la main plutôt que le supposer."
            )
        return self.champs[champ].valeur

    def retenir(self, champ: str, valeur: Any, *, par: str, le: datetime) -> ExtractionOCR:
        if champ not in self.champs:
            raise ValeurNonRetenue(f"« {champ} » n'existe pas dans l'extraction {self.piece}.")
        champs = dict(self.champs)
        champs[champ] = champs[champ].retenir(valeur, par=par, le=le)
        return self.model_copy(update={"champs": champs})

    def confirmer_tout(self, *, par: str, le: datetime) -> ExtractionOCR:
        """Confirme en bloc ce que le moteur a lu.

        Cette porte existe parce que le refuser conduirait à la contourner : un
        comptable qui doit valider quinze champs un par un finira par cliquer
        quinze fois sans lire. Le geste est donc offert, tracé, et il porte le nom
        de l'humain qui l'a fait — ce qui est exactement ce qu'on lui demandera de
        justifier si le montant se révèle faux.
        """
        return self.model_copy(
            update={
                "champs": {
                    nom: (c if c.validee else c.confirmer(par=par, le=le))
                    for nom, c in self.champs.items()
                }
            }
        )
