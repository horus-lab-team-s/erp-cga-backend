"""Les règles de conformité qu'un cabinet construit et valide à l'écran (pas 97).

─────────────────────────────────────────────────────────────────────────────────
MÊME MODÈLE QUE LES PARAMÈTRES DU PAS 95, ET UNE EXIGENCE EN PLUS

Les règles du référentiel commun restent des fichiers relus en revue. Un cabinet peut en
ajouter **pour lui seul**, avec effet au contrôle suivant, par une proposition qu'une autre
personne valide (circuit `regles` de `validation/circuit.yaml`).

L'exigence en plus : **une règle est éprouvée avant d'être proposée**. Elle est jouée sur
les factures connues, et la proposition garde le résultat (combien ont été éprouvées, sur
lesquelles elle réagit). Le valideur tranche sur ce résultat, pas sur une intuition. Une
règle qui réagirait sur **toutes** les factures éprouvées est refusée à la proposition :
c'est le symptôme d'une condition inversée ou d'un seuil mal choisi, et la valider
accuserait chaque facture du cabinet.

Une règle proposée n'a **aucun effet**. Validée, elle entre au moteur du cabinet avec le
nom du valideur en `valide_par`, comme une règle du fichier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.contextes.conformite.domaine.constructeur import Combinaison, ConditionDAnomalie
from app.contextes.conformite.domaine.entites import Regle
from app.contextes.referentiel.contrats import StatutValidation

__all__ = [
    "EssaiDeRegle",
    "PropositionDeRegle",
    "PropositionRefusee",
    "StatutProposition",
]


class PropositionRefusee(ValueError):
    """L'état ou le circuit s'opposent au geste. Message pour l'écran."""


class StatutProposition(StrEnum):
    PROPOSEE = "PROPOSEE"
    APPLIQUEE = "APPLIQUEE"
    REFUSEE = "REFUSEE"


class EssaiDeRegle(BaseModel):
    """Ce que la règle aurait fait sur les factures connues, au moment de la proposition."""

    model_config = ConfigDict(frozen=True)

    eprouvees: int = Field(ge=0)
    #: Les références des factures sur lesquelles la règle émet un constat.
    reagit_sur: list[str] = Field(default_factory=list)

    @property
    def reagit_partout(self) -> bool:
        return self.eprouvees > 0 and len(self.reagit_sur) == self.eprouvees


class PropositionDeRegle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    identifiant: str
    #: La règle compilée, **à valider** : c'est elle, et non les conditions, qui entrera au moteur.
    regle: Regle
    conditions: list[ConditionDAnomalie]
    combinaison: Combinaison
    #: La phrase que relit le valideur, produite des mêmes conditions que le prédicat.
    phrase: str
    essai: EssaiDeRegle

    motif: str
    propose_par: str
    propose_par_nom: str
    propose_le: datetime
    statut: StatutProposition

    tranche_par: str | None = None
    tranche_par_nom: str | None = None
    tranche_le: datetime | None = None
    motif_de_la_decision: str | None = None

    #: Pas 98 : la règle validée cesse de contrôler **à compter de** cette date.
    fin_d_effet: date | None = None
    retire_par: str | None = None
    retire_par_nom: str | None = None
    retire_le: datetime | None = None
    motif_du_retrait: str | None = None

    def retirer(
        self,
        *,
        par: str,
        nom: str,
        le: datetime,
        a_compter_du: date,
        motif: str,
        motif_minimum: int,
    ) -> PropositionDeRegle:
        """Mettre fin à une règle validée, vers l'avant seulement (pas 98).

        Les contrôles déjà rendus avec elle restent tels quels : une facture de juillet
        contrôlée en août garde son constat. À compter de la date, la règle ne contrôle plus.
        Une règle pas encore entrée en vigueur se retire à sa date d'effet : elle ne
        contrôle alors jamais.
        """
        if self.statut is not StatutProposition.APPLIQUEE or self.fin_d_effet is not None:
            raise PropositionRefusee(
                f"la règle {self.regle.code} n'est pas en vigueur : il n'y a rien à retirer."
            )
        if a_compter_du < le.date() or a_compter_du < self.regle.applicable_du:
            raise PropositionRefusee(
                "un retrait prend effet aujourd'hui au plus tôt, et pas avant la date d'effet de "
                "la règle : les contrôles déjà rendus ne changent pas."
            )
        if len(motif.strip()) < motif_minimum:
            raise PropositionRefusee(
                f"le motif doit compter au moins {motif_minimum} caractères : il dit pourquoi "
                "la règle cesse de valoir."
            )
        return self.model_copy(
            update={
                "fin_d_effet": a_compter_du,
                "retire_par": par,
                "retire_par_nom": nom,
                "retire_le": le,
                "motif_du_retrait": motif.strip(),
            }
        )

    def trancher(
        self,
        *,
        valider: bool,
        par: str,
        nom: str,
        le: datetime,
        motif: str,
        quatre_yeux: bool,
        motif_minimum: int,
    ) -> PropositionDeRegle:
        if self.statut is not StatutProposition.PROPOSEE:
            raise PropositionRefusee(
                f"la proposition {self.identifiant} est {self.statut.value} : seule une "
                "proposition se tranche."
            )
        if quatre_yeux and par == self.propose_par:
            raise PropositionRefusee(
                "le circuit exige qu'une autre personne valide la règle qu'on a construite."
            )
        if len(motif.strip()) < motif_minimum:
            raise PropositionRefusee(
                f"le motif doit compter au moins {motif_minimum} caractères : il dit pourquoi "
                "la règle est juste, au vu de l'essai."
            )
        return self.model_copy(
            update={
                "statut": StatutProposition.APPLIQUEE if valider else StatutProposition.REFUSEE,
                "tranche_par": par,
                "tranche_par_nom": nom,
                "tranche_le": le,
                "motif_de_la_decision": motif.strip(),
            }
        )

    def regle_en_vigueur(self) -> Regle | None:
        """La règle telle que le moteur l'emploie, ou `None` si la proposition n'est pas validée."""
        if self.statut is not StatutProposition.APPLIQUEE or self.tranche_le is None:
            return None
        if self.fin_d_effet is not None and self.fin_d_effet <= self.regle.applicable_du:
            return None
        return self.regle.model_copy(
            update={
                "applicable_au": self.fin_d_effet,
                "statut": StatutValidation.VALIDE,
                "valide_par": self.tranche_par_nom,
                "valide_le": self.tranche_le.date(),
            }
        )
