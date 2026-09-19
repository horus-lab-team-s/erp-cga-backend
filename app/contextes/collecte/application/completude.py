"""La complétude d'un dossier, et la limite de ce qu'elle mesure.

─────────────────────────────────────────────────────────────────────────────────
LA PHRASE À NE JAMAIS DIRE

« Le dossier est complet. »

Un logiciel ne peut pas le savoir. Il connaît les pièces qu'il a reçues et les
demandes qu'il a émises ; il ignore les factures que l'adhérent n'a jamais
mentionnées à personne. Un taux de 100 % ne prouve donc qu'une chose : **tout ce
qu'on savait attendre est arrivé.** Il ne prouve pas l'exhaustivité.

La distinction n'est pas de la prudence oratoire. Elle est ce qui sépare une
information utile d'une fausse assurance : un comptable qui lit « dossier complet »
cesse de chercher, et c'est précisément à ce moment-là que la facture oubliée
devient un redressement. La propriété s'appelle donc `attentes_satisfaites`, et le
libellé destiné aux écrans le dit en toutes lettres.

CE QUI DÉCIDE SI ON PEUT DÉPOSER

Pas le taux. Une demande **bloquante** ouverte suffit à empêcher un dépôt, quand
bien même le taux afficherait 95 %. Inversement, dix demandes non bloquantes en
souffrance n'empêchent rien : elles pèsent sur la qualité du dossier, pas sur la
possibilité de déclarer.

C'est ce que le contexte F consomme avant d'établir une déclaration — l'arête
`obligations → collecte` du graphe.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.collecte.domaine.demandes import DemandePiece
from app.contextes.collecte.domaine.pieces import PieceJustificative

__all__ = ["CompletudeDossier", "evaluer_completude"]


class CompletudeDossier(BaseModel):
    """L'état d'avancement de la collecte pour un dossier et une période."""

    model_config = ConfigDict(frozen=True)

    entreprise: str
    periode_debut: date
    periode_fin: date

    pieces_recues: int = Field(ge=0)
    pieces_traitees: int = Field(ge=0)
    pieces_en_souffrance: list[str] = Field(default_factory=list)

    demandes_ouvertes: int = Field(ge=0)
    demandes_satisfaites: int = Field(default=0, ge=0)
    demandes_bloquantes_ouvertes: list[str] = Field(default_factory=list)
    demandes_en_retard: list[str] = Field(default_factory=list)

    #: Ancienneté de la pièce reçue non traitée la plus vieille. C'est cet
    #: indicateur-là qui révèle un dossier abandonné, bien avant le taux : un
    #: dossier peut afficher 100 % de pièces traitées et n'avoir rien reçu depuis
    #: quatre mois.
    plus_ancienne_en_souffrance: int | None = None

    @computed_field
    @property
    def attentes_satisfaites(self) -> Decimal | None:
        """Part des demandes émises qui ont trouvé leur pièce.

        ⚠️ Ce n'est **pas** un taux d'exhaustivité du dossier — voir l'en-tête du
        module. `None` quand aucune demande n'a été émise : un taux de 100 %
        calculé sur zéro attente serait le plus trompeur de tous.
        """
        total = self.demandes_ouvertes + self.demandes_satisfaites
        if total == 0:
            return None
        return (Decimal(self.demandes_satisfaites) / Decimal(total)).quantize(Decimal("0.01"))

    @computed_field
    @property
    def depot_possible(self) -> bool:
        """Aucune pièce indispensable ne manque.

        Le taux n'entre pas dans ce calcul : seule compte l'absence de demande
        bloquante ouverte.
        """
        return not self.demandes_bloquantes_ouvertes

    @computed_field
    @property
    def libelle(self) -> str:
        """Ce que l'écran affiche, formulé pour ne pas endormir la vigilance."""
        if self.demandes_bloquantes_ouvertes:
            return (
                f"{len(self.demandes_bloquantes_ouvertes)} pièce(s) indispensable(s) "
                "manquante(s) — dépôt impossible en l'état"
            )
        if self.demandes_ouvertes:
            return f"{self.demandes_ouvertes} pièce(s) attendue(s), non bloquante(s)"
        if self.pieces_en_souffrance:
            return f"{len(self.pieces_en_souffrance)} pièce(s) reçue(s) restant à traiter"
        return "Tout ce qui était attendu est arrivé et traité"


def evaluer_completude(
    entreprise: str,
    pieces: list[PieceJustificative],
    demandes: list[DemandePiece],
    *,
    periode_debut: date,
    periode_fin: date,
    a_la_date: date,
) -> CompletudeDossier:
    """Établit l'état de la collecte sur une période.

    Les pièces sont retenues sur leur **date de réception**, pas sur la date du
    document. Une facture de mars reçue en juin appartient au travail de juin :
    c'est en juin qu'elle a été traitée, et c'est en juin qu'elle a pesé sur la
    charge du cabinet. La date du document, elle, décide de l'exercice de
    rattachement — c'est une autre question, et elle appartient au contexte E.
    """
    du_dossier = [p for p in pieces if p.entreprise == entreprise]
    de_la_periode = [
        p for p in du_dossier if periode_debut <= p.recue_le.date() <= periode_fin
    ]
    en_souffrance = [p for p in de_la_periode if p.en_attente_de_traitement]

    demandes_dossier = [d for d in demandes if d.entreprise == entreprise]
    ouvertes = [d for d in demandes_dossier if d.ouverte]

    return CompletudeDossier(
        entreprise=entreprise,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        pieces_recues=len(de_la_periode),
        pieces_traitees=sum(1 for p in de_la_periode if p.traitee),
        pieces_en_souffrance=sorted(p.identifiant for p in en_souffrance),
        demandes_ouvertes=len(ouvertes),
        demandes_satisfaites=sum(
            1 for d in demandes_dossier if d.satisfaite_le is not None
        ),
        demandes_bloquantes_ouvertes=sorted(d.identifiant for d in ouvertes if d.bloquante),
        demandes_en_retard=sorted(d.identifiant for d in ouvertes if d.en_retard(a_la_date)),
        plus_ancienne_en_souffrance=(
            max(p.anciennete(a_la_date) for p in en_souffrance) if en_souffrance else None
        ),
    )
