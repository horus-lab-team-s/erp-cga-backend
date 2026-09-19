"""Ce qui interdit de déposer, et ce qui devrait faire hésiter.

─────────────────────────────────────────────────────────────────────────────────
LE TRANSPORT N'EST PAS LE SUJET

Brancher un appel réseau sur un portail est une demi-journée le jour où les
spécifications existent. Ce qui prend du temps, et ce qui a de la valeur, est de
produire une déclaration **qui ne sera pas rejetée, et qui ne sera pas
redressée**. Ce module s'en occupe.

Un CGA n'est pas un tuyau vers la DGI. Son agrément l'engage sur la sincérité de
ce qu'il transmet, et un adhérent redressé sur une déclaration visée par son
centre de gestion se retourne contre le centre. Refuser un dépôt est donc un
service, pas une entrave.

TROIS NIVEAUX, ET LA DISTINCTION EST LE SUJET

**`BLOQUANT`** — la déclaration serait fausse ou irrecevable. On ne dépose pas.
Une balance déséquilibrée, une période déjà déposée, un dossier hors régime.

**`RESERVE`** — la déclaration peut partir, mais quelque chose devra être assumé.
Une TVA calculée sur des pièces que le moteur de conformité déclare non
déductibles part avec un montant que l'administration contestera.

**`INFORMATION`** — ce qu'il faut savoir sans que cela change la décision.

Confondre bloquant et réserve produirait l'un des deux échecs symétriques : soit
un système qui ne dépose jamais rien parce qu'un détail traîne toujours, soit un
système qui dépose tout et ne sert à rien.

LA RÉSERVE LA PLUS IMPORTANTE EST CELLE DU RÉFÉRENTIEL

Aucune valeur légale n'a été validée sur le Code général des impôts. Tant que
c'est le cas, **toute déclaration produite par ce système porte une réserve**, et
elle est nommée. Laisser déposer sans le dire ferait passer pour opposable un
chiffre qui ne l'est pas — c'est le manquement que tout le reste du projet est
construit pour éviter, et il serait absurde qu'il resurgisse au dernier mètre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

__all__ = [
    "Anomalie",
    "NiveauRecevabilite",
    "Recevabilite",
]


class NiveauRecevabilite(StrEnum):
    BLOQUANT = "BLOQUANT"
    RESERVE = "RESERVE"
    INFORMATION = "INFORMATION"


class Anomalie(BaseModel):
    """Un constat de recevabilité, avec ce qu'il faut faire pour le lever."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    niveau: NiveauRecevabilite
    libelle: str = Field(min_length=1)

    #: Ce qu'il faut faire. Un constat sans remède oblige celui qui le lit à
    #: deviner, et il devinera mal : c'est la même discipline que les constats du
    #: moteur de conformité, qui portent tous leur remédiation.
    remediation: str = Field(min_length=1)

    #: Le montant en jeu, quand il est chiffrable. Ce qui décide qu'on s'arrête
    #: n'est jamais le nombre d'anomalies, c'est ce qu'elles coûtent.
    enjeu: Decimal | None = None

    #: L'objet visé — une écriture, une pièce, un compte. Pour aller le corriger
    #: sans le chercher.
    reference: str | None = None


class Recevabilite(BaseModel):
    """Le verdict d'ensemble avant dépôt."""

    model_config = ConfigDict(frozen=True)

    anomalies: list[Anomalie] = Field(default_factory=list)

    @computed_field
    @property
    def deposable(self) -> bool:
        """Aucun bloquant. Les réserves n'empêchent pas — elles s'assument."""
        return not any(a.niveau is NiveauRecevabilite.BLOQUANT for a in self.anomalies)

    @computed_field
    @property
    def bloquants(self) -> list[Anomalie]:
        return [a for a in self.anomalies if a.niveau is NiveauRecevabilite.BLOQUANT]

    @computed_field
    @property
    def reserves(self) -> list[Anomalie]:
        return [a for a in self.anomalies if a.niveau is NiveauRecevabilite.RESERVE]

    @computed_field
    @property
    def enjeu_total(self) -> Decimal:
        """La somme de ce qui est chiffré.

        C'est ce qu'on affiche, pas le nombre d'anomalies : « 3 réserves » ne
        déclenche aucune décision, « 1 240 500 F de TVA contestable » en
        déclenche une.
        """
        return sum((a.enjeu for a in self.anomalies if a.enjeu is not None), Decimal(0))

    @computed_field
    @property
    def exige_une_decision(self) -> bool:
        """Déposable, mais pas sans que quelqu'un assume.

        Sérialisé : c'est ce champ qui commande l'écran de confirmation. Le
        distinguer de `deposable` évite que le réviseur ne clique par réflexe sur
        un bouton qui ressemble à tous les autres.
        """
        return self.deposable and bool(self.reserves)

    def avec(self, *anomalies: Anomalie) -> Recevabilite:
        return self.model_copy(update={"anomalies": [*self.anomalies, *anomalies]})
