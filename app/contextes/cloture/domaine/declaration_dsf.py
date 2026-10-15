"""La DSF d'un exercice : préparée, validée par un second regard, puis déposée.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ (14 octobre 2026) : « le flux jusqu'à la génération de
la DSF, sa prévisualisation et sa soumission ». La liasse se calculait déjà, en
direct, à chaque lecture ; rien ne la figeait, rien ne la faisait valider, rien ne
disait qu'elle était partie.

TROIS GESTES DISTINCTS, COMME POUR LA TVA (`obligations/application/depot.py`)

* **Préparer** — figer la liasse d'un exercice clos en une VERSION, avec son
  empreinte. La version est ce qui se relit, se valide et se dépose : jamais la
  liasse du moment, qu'une écriture de régularisation changerait sous les yeux
  du réviseur.
* **Valider** — le second regard. Le réviseur, et jamais celui qui a préparé :
  la DGI limite à DOUZE soumissions par an et par contribuable (guide de
  télédéclaration DSF, février 2024), le compte se bloque à la treizième. Une
  DSF fausse coûte donc plus qu'une correction.
* **Constater le dépôt** — le numéro de l'accusé de la DGI, confronté à
  l'empreinte de la version validée. Seulement alors l'obligation DSF passe à
  « déclarée » à l'échéancier.

⚠️ UNE VERSION NE SE MODIFIE PAS. On en prépare une nouvelle : l'ancienne passe à
REMPLACÉE, et reste lisible. Une version déposée ne se remplace pas ici : la DSF
rectificative n'est pas encore construite, et le refus le dit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.partage.copie import transiter

__all__ = ["DeclarationDSF", "EtatDSF", "TransitionDSFRefusee"]


class TransitionDSFRefusee(RuntimeError):
    """Le geste n'est pas permis dans l'état de la version : le message dit pourquoi."""


class EtatDSF(StrEnum):
    PREPAREE = "PREPAREE"
    VALIDEE = "VALIDEE"
    DEPOSEE = "DEPOSEE"
    #: Une version plus récente a été préparée avant tout dépôt.
    REMPLACEE = "REMPLACEE"


class DeclarationDSF(BaseModel):
    """Une version de la DSF d'un exercice, figée."""

    model_config = ConfigDict(frozen=True)

    entreprise: str = Field(min_length=1)
    denomination: str = Field(min_length=1)
    exercice: str = Field(min_length=1)
    periode_debut: date
    periode_fin: date
    version: int = Field(ge=1)
    etat: EtatDSF = EtatDSF.PREPAREE

    #: La liasse figée, sérialisée de façon déterministe : c'est elle qui est hachée,
    #: rendue en PDF, et déposée. Voir `application/dsf.py`.
    contenu: str = Field(min_length=2)
    #: SHA-256 du contenu. Calculée à la préparation, jamais recalculée ailleurs.
    empreinte: str = Field(min_length=64, max_length=64)
    systeme: str
    resultat_comptable: Decimal
    resultat_fiscal: Decimal

    preparee_par: str = Field(min_length=1)
    preparee_le: datetime
    validee_par: str | None = None
    validee_le: datetime | None = None

    numero_accuse: str | None = None
    deposee_le: datetime | None = None
    deposee_par: str | None = None
    #: La pièce du dossier qui porte l'accusé de la DGI (le PDF rendu par le portail).
    piece_accuse: str | None = None

    @property
    def identifiant(self) -> str:
        return f"DSF-{self.entreprise}-{self.exercice}-v{self.version}"

    @model_validator(mode="after")
    def _etats_coherents(self) -> DeclarationDSF:
        if self.etat in (EtatDSF.VALIDEE, EtatDSF.DEPOSEE) and not (
            self.validee_par and self.validee_le
        ):
            raise ValueError(f"{self.identifiant} : une DSF {self.etat} porte son valideur.")
        if self.etat is EtatDSF.DEPOSEE and not (
            self.numero_accuse and self.deposee_le and self.deposee_par
        ):
            raise ValueError(f"{self.identifiant} : une DSF déposée porte son accusé.")
        return self

    # ── Les gestes ────────────────────────────────────────────────────────────

    def valider(self, *, par: str, a_l_instant: datetime) -> DeclarationDSF:
        if self.etat is not EtatDSF.PREPAREE:
            raise TransitionDSFRefusee(
                f"{self.identifiant} est {self.etat.value.lower()} : seule une version "
                "préparée se valide."
            )
        if par == self.preparee_par:
            raise TransitionDSFRefusee(
                f"{self.identifiant} a été préparée par vous : la validation est le regard "
                "d'un autre. La DGI n'admet que douze soumissions par an : une DSF fausse "
                "ne se corrige pas sans compter."
            )
        # ⚠️ `transiter`, pas `model_copy` : l'invariant « validée porte son valideur » se
        # rejoue à chaque changement d'état (test_transitions_revalidees).
        return transiter(self, etat=EtatDSF.VALIDEE, validee_par=par, validee_le=a_l_instant)

    def deposer(
        self, *, numero: str, depose_le: datetime, par: str, piece: str | None
    ) -> DeclarationDSF:
        if self.etat is not EtatDSF.VALIDEE:
            raise TransitionDSFRefusee(
                f"{self.identifiant} est {self.etat.value.lower()} : seule une version "
                "validée se dépose. Préparer, faire valider par le réviseur, puis déposer."
            )
        return transiter(
            self,
            etat=EtatDSF.DEPOSEE,
            numero_accuse=numero,
            deposee_le=depose_le,
            deposee_par=par,
            piece_accuse=piece,
        )

    def remplacer(self) -> DeclarationDSF:
        if self.etat is EtatDSF.DEPOSEE:
            raise TransitionDSFRefusee(
                f"{self.identifiant} est déposée (accusé {self.numero_accuse}) : elle ne "
                "se remplace pas. La DSF rectificative n'est pas encore construite ; elle "
                "se fait sur le portail de la DGI."
            )
        return transiter(self, etat=EtatDSF.REMPLACEE)
