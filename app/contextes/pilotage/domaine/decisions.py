"""Les décisions de la direction sur un dossier à risque (pas 100).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI UN SCORE NE SUFFIT PAS

Jusqu'au pas 99, le pilotage **lisait** : un score décomposé, des éléments cités. Mais la
maquette de la vue risque se termine par des actions (« Renforcer le suivi », « Exiger une
régularisation datée »…), et une note : « les décisions sont datées, signées, et visibles
dans la chronologie de la fiche adhérent ». Une direction qui décide oralement en comité
ne laisse aucune trace ; six mois plus tard, personne ne sait si la fin d'adhésion avait
été envisagée, par qui, ni sur quel score.

Ce module porte donc trois choses :

* `MesureDeDirection` et `CatalogueDesMesures` : **ce que la direction peut décider**. Le
  catalogue est un fichier du référentiel (`pilotage/mesures.yaml`), jamais du code. Un
  cabinet qui veut ajouter « Proposer un échéancier d'honoraires » ajoute cinq lignes.
* `DecisionDeDirection` : **ce qui a été décidé**. Datée, signée par la personne connectée,
  motivée, et accompagnée de l'instantané du score au moment où elle a été prise.
* `InstantaneDuScore` : le score **gelé dans la décision**, et seulement là.

⚠️ LE SCORE RESTE CALCULÉ, L'INSTANTANÉ N'EST PAS UN SCORE STOCKÉ

L'en-tête de `ScoreRisque` interdit de stocker le score : le figer produirait un tableau
de bord qui vieillit sans le dire. L'instantané ne contredit pas cette règle. Il n'est
jamais affiché comme le risque du dossier ; il répond à une autre question : « sur quoi la
direction s'est-elle fondée ? ». Une fin d'adhésion envisagée à 88 points, relue quand le
dossier est redescendu à 20, doit dire 88, sinon la décision paraît arbitraire.

⚠️ POURQUOI UNE MESURE EST REFUSÉE HORS DE SES NIVEAUX

« Envisager la fin d'adhésion » sur un dossier sous contrôle n'est pas une décision de
pilotage, c'est un différend commercial, et il ne passe pas par le score. Chaque mesure dit
pour quels niveaux elle est proposée, et le geste est refusé ailleurs, avec un message qui
le dit. Élargir est une ligne du catalogue, pas un contournement à l'écran.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.pilotage.domaine.entites import NiveauRisque

__all__ = [
    "CatalogueDesMesures",
    "DecisionDeDirection",
    "DecisionIntrouvable",
    "DecisionRefusee",
    "InstantaneDuScore",
    "MesureDeDirection",
    "StatutDecisionDeDirection",
    "verifier_le_motif_de_direction",
]


class DecisionRefusee(ValueError):
    """Le geste demandé n'est pas permis : mesure inconnue, niveau, motif, échéance, doublon.

    Le message est destiné à l'écran : il dit ce qui manque et comment y remédier.
    """


class DecisionIntrouvable(LookupError):
    """Aucune décision de ce nom sur ce dossier. Rendue en 404 par la route."""


def verifier_le_motif_de_direction(motif: str, minimum: int) -> str:
    """Le motif nettoyé de ses espaces de bord, ou `DecisionRefusee` s'il est trop court.

    Un motif est exigé à la prise **et** à la clôture : « fait » ne dit pas pourquoi une
    régularisation exigée n'a plus lieu d'être.
    """
    propre = motif.strip()
    if len(propre) < minimum:
        raise DecisionRefusee(
            f"le motif compte {len(propre)} caractère(s), le cabinet en exige au moins "
            f"{minimum} : il reste au dossier et au journal d'audit."
        )
    return propre


class MesureDeDirection(BaseModel):
    """Une mesure que la direction peut décider sur un dossier. Lue au référentiel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Le code stable, employé au journal et dans l'identifiant de la décision. Il ne
    #: change jamais : renommer une mesure, c'est changer son `libelle`.
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,39}$")
    libelle: str = Field(min_length=3, max_length=80)
    #: Ce que la mesure engage, en une ou deux phrases, affiché sous le bouton.
    description: str = Field(min_length=10, max_length=400)
    #: Les niveaux de risque pour lesquels la mesure est proposée, et permise.
    niveaux: tuple[NiveauRisque, ...] = Field(min_length=1)
    #: Vrai si la décision n'a de sens qu'avec une date butoir (« régularisation datée »).
    echeance_requise: bool = False


class CatalogueDesMesures(BaseModel):
    """Le catalogue du cabinet : les mesures possibles et la longueur minimale du motif."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Au moins 20 : une décision de direction se relit en comité, pas un « vu ».
    motif_minimum: int = Field(30, ge=20, le=400)
    mesures: tuple[MesureDeDirection, ...] = ()
    #: D'où vient le catalogue, pour que l'écran puisse le dire.
    source: str = "aucun catalogue : aucune mesure ne peut être décidée"

    @model_validator(mode="after")
    def _codes_uniques(self) -> CatalogueDesMesures:
        codes = [m.code for m in self.mesures]
        doublons = sorted({c for c in codes if codes.count(c) > 1})
        if doublons:
            # Deux mesures de même code se confondraient au journal : on ne saurait plus
            # laquelle a été décidée.
            raise ValueError(f"codes de mesure en double : {', '.join(doublons)}")
        return self

    @classmethod
    def prudent(cls) -> CatalogueDesMesures:
        """Sans fichier : aucune mesure. La vue risque reste entière (score, éléments,
        décisions passées) ; seul le geste de décider disparaît, et l'écran dit pourquoi."""
        return cls()

    def proposees_pour(self, niveau: NiveauRisque) -> tuple[MesureDeDirection, ...]:
        """Les mesures permises à ce niveau, dans l'ordre du fichier (celui que le cabinet
        a choisi, de la plus légère à la plus lourde)."""
        return tuple(m for m in self.mesures if niveau in m.niveaux)

    def mesure(self, code: str) -> MesureDeDirection:
        for mesure in self.mesures:
            if mesure.code == code:
                return mesure
        raise DecisionRefusee(
            f"la mesure « {code} » n'existe pas au catalogue du cabinet ({self.source})."
        )


class InstantaneDuScore(BaseModel):
    """Le score **au moment de la décision**. Voir l'en-tête : ce n'est pas un score stocké."""

    model_config = ConfigDict(frozen=True)

    a_la_date: date
    total: Decimal
    niveau: NiveauRisque
    #: Les occurrences par composante (`ANOMALIES_BLOQUANTES: 3`), pour relire la décision
    #: sans recalculer un état du dossier qui n'existe plus.
    occurrences: dict[str, int] = Field(default_factory=dict)


class StatutDecisionDeDirection(StrEnum):
    #: La mesure est en vigueur : le dossier est suivi à ce titre.
    EN_COURS = "EN_COURS"
    #: La mesure a produit son effet ou n'a plus lieu d'être. Close, jamais effacée.
    CLOSE = "CLOSE"


class DecisionDeDirection(BaseModel):
    """Une mesure décidée sur un dossier. Immuable : `clore` rend une nouvelle décision."""

    model_config = ConfigDict(frozen=True)

    #: `{dossier}:{mesure}:{rang}` : une même mesure reprise après clôture fait une
    #: nouvelle ligne, et l'histoire garde la première.
    identifiant: str
    dossier: str
    mesure: str
    #: Le libellé **au moment de la décision** : si le catalogue renomme la mesure, la
    #: décision dit toujours ce qui a été décidé ce jour-là.
    libelle: str
    motif: str
    echeance: date | None = None
    prise_par: str
    prise_par_nom: str
    prise_le: datetime
    score: InstantaneDuScore

    statut: StatutDecisionDeDirection = StatutDecisionDeDirection.EN_COURS
    close_par: str | None = None
    close_par_nom: str | None = None
    close_le: datetime | None = None
    motif_de_cloture: str | None = None

    @property
    def en_cours(self) -> bool:
        return self.statut is StatutDecisionDeDirection.EN_COURS

    def echue(self, jour: date) -> bool:
        """Vrai si la mesure est encore en cours alors que son échéance est dépassée.

        C'est l'alerte utile : une régularisation exigée pour le 30 et toujours ouverte le
        2 du mois suivant demande une nouvelle décision, pas l'oubli.
        """
        return self.en_cours and self.echeance is not None and self.echeance < jour

    def clore(
        self, *, par: str, par_nom: str, le: datetime, motif: str, motif_minimum: int
    ) -> DecisionDeDirection:
        if not self.en_cours:
            raise DecisionRefusee(
                f"la décision {self.identifiant} est déjà close depuis le "
                f"{self.close_le:%d/%m/%Y} : on ne clôt pas deux fois."
            )
        return self.model_copy(
            update={
                "statut": StatutDecisionDeDirection.CLOSE,
                "close_par": par,
                "close_par_nom": par_nom,
                "close_le": le,
                "motif_de_cloture": verifier_le_motif_de_direction(motif, motif_minimum),
            }
        )
