"""Le contrôle de conformité déclenché à l'arrivée de la pièce.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CONTRÔLER SI TÔT

C'est l'arête `collecte → conformite` du graphe, et elle existe pour une raison
de délai, pas d'architecture.

Une facture non conforme découverte au moment de la saisie comptable — deux mois
après sa réception — se corrige rarement : le fournisseur a été payé, la relation
commerciale est passée à autre chose, et personne ne va rouvrir le sujet pour une
mention manquante. La TVA est perdue.

La même facture contrôlée le jour de sa réception se corrige souvent : le
fournisseur est encore en contact, la facture rectificative coûte un appel
téléphonique. **Le seul moment où une facture est corrigeable, c'est tout de
suite.** Toute la valeur du contexte D dépend de ce que le contrôle arrive tôt.

CE QUE LA COLLECTE FAIT DU VERDICT, ET CE QU'ELLE N'EN FAIT PAS

Elle **retient la référence du rapport**, et rien de plus. Elle ne recopie ni la
sévérité, ni l'enjeu, ni le nombre de constats. Dupliquer le verdict le ferait
diverger du rapport dès la première réévaluation — et c'est la copie périmée, pas
le rapport, qui s'afficherait sur l'écran de la boîte de réception.

Elle ne change pas non plus l'état de la pièce. Une facture non conforme suit
exactement le même cycle : elle sera comptabilisée, et sa TVA sera rejetée au
moment de la déclaration. Le rejet est une conséquence fiscale, pas un état de
traitement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.contextes.collecte.domaine.pieces import PieceJustificative
from app.contextes.conformite.api import (
    FactureAControler,
    MoteurConformite,
    RapportConformite,
)

__all__ = ["ControleAReception", "controler_a_reception"]


class ControleAReception(BaseModel):
    """La pièce mise à jour, et le rapport qui vient d'être produit."""

    model_config = ConfigDict(frozen=True)

    piece: PieceJustificative
    rapport: RapportConformite

    @property
    def correction_a_demander(self) -> bool:
        """Il y a quelque chose à demander au fournisseur, et c'est maintenant.

        Un constat purement informatif n'appelle pas de démarche ; un constat qui
        coûte de la TVA ou une charge en appelle une, et son coût est chiffré dans
        le rapport.
        """
        return not self.rapport.conforme and self.rapport.enjeu_total > 0

    @property
    def comptabilisation_interdite(self) -> bool:
        return self.rapport.comptabilisation_interdite


def controler_a_reception(
    piece: PieceJustificative,
    facture: FactureAControler,
    moteur: MoteurConformite,
) -> ControleAReception:
    """Contrôle la pièce dès sa réception et lui rattache son rapport.

    Le contrôle exige une pièce **identifiée**. Contrôler un document dont on
    ignore le montant et l'émetteur produirait un rapport sur des valeurs devinées,
    et ce rapport porterait pourtant la même autorité qu'un vrai : il finirait
    dans un dossier de défense.
    """
    if not piece.identifiee:
        raise ValueError(
            f"{piece.identifiant} : contrôle impossible sur une pièce non identifiée. "
            "Faire valider la lecture avant de contrôler — un rapport établi sur des "
            "valeurs supposées est plus dangereux qu'une absence de rapport."
        )
    if facture.document.reference != piece.reference_document:
        raise ValueError(
            f"{piece.identifiant} : la facture soumise porte la référence "
            f"{facture.document.reference} alors que la pièce porte "
            f"{piece.reference_document}. Le rapport se rattacherait au mauvais "
            "document."
        )

    rapport = moteur.controler(facture)
    return ControleAReception(
        piece=piece.model_copy(update={"reference_rapport": rapport.reference_document}),
        rapport=rapport,
    )
