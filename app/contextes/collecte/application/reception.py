"""Recevoir une pièce, puis l'identifier.

─────────────────────────────────────────────────────────────────────────────────
DEUX GESTES, ET ILS SONT DISTINCTS

**Recevoir** est un accusé de réception : le fichier est arrivé, il est horodaté,
il est confronté au stock existant. Rien n'est encore su de son contenu.

**Identifier** est la décision d'un humain sur ce que le document dit. C'est là
que les valeurs lues par la machine deviennent des valeurs opposables.

Les confondre — recevoir et lire d'un seul mouvement — aurait une conséquence
précise : une pièce dont l'extraction échoue disparaîtrait des écrans, ou serait
rejetée à l'arrivée. Or une facture illisible reste une facture, et l'adhérent
l'a bien envoyée. Elle doit être reçue, tracée, et rester visible dans le travail
à faire jusqu'à ce qu'un humain la lise.

CE QUE LA RÉCEPTION REFUSE, ET CE QU'ELLE SIGNALE

Elle refuse le fichier déjà reçu à l'octet près, et rien d'autre. Tout le reste —
même émetteur, même numéro, même montant — est **signalé pour arbitrage** : le
refus automatique d'une pièce ressemblante ferait perdre une charge déductible le
jour où un fournisseur réutilise ses numéros d'une année sur l'autre, et personne
ne s'en apercevrait puisque la pièce n'existerait jamais.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.collecte.domaine.doublons import SuspicionDoublon, detecter_doublons
from app.contextes.collecte.domaine.extraction import ExtractionOCR, ValeurNonRetenue
from app.contextes.collecte.domaine.pieces import PieceJustificative, TypePiece

__all__ = [
    "DepotRefuse",
    "ResultatReception",
    "identifier",
    "receptionner",
]


class DepotRefuse(ValueError):
    """La pièce n'entre pas dans le dossier, et l'on dit pourquoi.

    Le message est destiné à être relayé tel quel à l'adhérent. « Erreur lors du
    dépôt » le conduirait à réessayer indéfiniment ; « vous nous avez déjà envoyé
    ce document le 12 juillet » clôt la question.
    """


class ResultatReception(BaseModel):
    """Ce que la réception a produit, y compris quand elle a des réserves."""

    model_config = ConfigDict(frozen=True)

    piece: PieceJustificative
    suspicions: list[SuspicionDoublon] = Field(default_factory=list)
    #: Pas 96 : vrai quand le dépôt **rejoue** une pièce déjà reçue (même fichier, même
    #: dossier). La pièce rendue est celle du dossier, **inchangée** : rien n'est réécrit.
    rejeu: bool = False

    @computed_field
    @property
    def a_arbitrer(self) -> bool:
        """Un humain doit trancher avant que la pièce ne soit comptabilisée."""
        return bool(self.suspicions)

    @computed_field
    @property
    def montant_du_double_emploi(self) -> Decimal:
        """Ce qui serait déduit deux fois si les suspicions étaient ignorées.

        C'est ce chiffre qu'on affiche, pas le nombre de suspicions : « 2 doublons
        possibles » ne déclenche aucune action, « 1 780 500 F déduits deux fois »
        en déclenche une.
        """
        return max(
            (s.montant_en_jeu for s in self.suspicions if s.montant_en_jeu is not None),
            default=Decimal(0),
        )


def receptionner(
    piece: PieceJustificative,
    deja_recues: list[PieceJustificative],
) -> ResultatReception:
    """Accuse réception d'une pièce, après confrontation au dossier.

    Lève `DepotRefuse` sur un doublon certain — même empreinte. La pièce n'est
    alors pas créée : il n'y a rien à créer, elle existe déjà.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ PAS 96 : LE REJEU RÉÉCRIVAIT LA PIÈCE, ET EFFAÇAIT LE TRAVAIL DU CABINET

    L'identifiant dérive de l'empreinte et du dossier : redéposer le même fichier sur le
    même dossier produit la même clé. La route promettait qu'un client dont la connexion
    tombe « peut recommencer sans crainte ». Essai : l'adhérent dépose, le comptable lit
    la pièce (état LUE, type, numéro, montant), l'adhérent rejoue son envoi. Réponse 201,
    et la pièce est **redevenue RECUE, sans type, sans numéro, sans montant**, avec une
    nouvelle heure de réception, celle dont le domaine dit que « seule celle-ci fait foi ».

    `detecter_doublons` écarte, à raison, la pièce de même identifiant (ce n'est pas un
    doublon, c'est elle), et la route l'enregistrait par-dessus. Une file de dépôts hors
    ligne, qui rejoue par construction, aurait effacé la lecture du cabinet à chaque retour
    du réseau.

    Désormais, une pièce de même identifiant déjà au dossier est **rendue telle quelle**,
    avec `rejeu=True`, et l'appelant ne l'enregistre pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    deja = next((p for p in deja_recues if p.identifiant == piece.identifiant), None)
    if deja is not None:
        return ResultatReception(piece=deja, rejeu=True)

    suspicions = detecter_doublons(piece, deja_recues)

    bloquantes = [s for s in suspicions if s.bloquant]
    if bloquantes:
        premiere = bloquantes[0]
        raise DepotRefuse(
            f"Ce document a déjà été reçu — il figure au dossier sous la référence "
            f"{premiere.piece_existante}. Indices : {', '.join(premiere.indices)}. "
            "Inutile de le renvoyer ; s'il s'agit d'un autre document, vérifier que "
            "le bon fichier a été joint."
        )

    return ResultatReception(piece=piece, suspicions=suspicions)


#: Correspondance entre les champs de l'extraction et ceux de la pièce. Explicite
#: plutôt que déduite d'une convention de nommage : le jour où le moteur d'OCR
#: renomme un champ, on veut une erreur ici, pas une pièce silencieusement
#: privée de son montant.
_CHAMPS_PIECE: dict[str, str] = {
    "reference_document": "reference_document",
    "date_emission": "date_document",
    "montant_ttc": "montant_ttc",
    "emetteur": "emetteur",
}


def identifier(
    piece: PieceJustificative,
    extraction: ExtractionOCR,
    *,
    type_confirme: TypePiece | None = None,
) -> PieceJustificative:
    """Reporte sur la pièce les valeurs retenues, puis la passe à LUE.

    Seules les valeurs **exploitables** sont reportées : validées par un humain,
    ou automatiquement acceptables pour un champ non sensible. Les autres sont
    laissées vides, ce qui maintient la pièce à l'état RECUE et la garde sous les
    yeux du comptable.

    Le type peut être confirmé ici. Un type deviné par la machine et jamais
    confirmé commanderait le jeu de règles du contexte D et la forme de l'écriture
    du contexte E — deux décisions trop lourdes pour être prises par défaut.
    """
    if extraction.piece != piece.identifiant:
        raise ValueError(
            f"extraction de {extraction.piece} appliquée à la pièce {piece.identifiant}. "
            "Un rapprochement de ce genre passerait les données d'un adhérent sur le "
            "dossier d'un autre."
        )

    details: dict[str, object] = {}
    for champ, attribut in _CHAMPS_PIECE.items():
        if champ not in extraction.champs:
            continue
        try:
            details[attribut] = extraction.valeur(champ)
        except ValeurNonRetenue:
            continue

    if type_confirme is not None:
        details["type"] = type_confirme

    return piece.marquer_lue(**details)
