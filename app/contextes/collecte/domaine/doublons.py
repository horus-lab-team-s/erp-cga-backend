"""La détection des doublons — le contrôle qui rapporte le plus, et qu'on oublie.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE

Un adhérent photographie sa facture et l'envoie par WhatsApp. Trois jours plus
tard, sans souvenir de l'avoir fait, il la redépose sur le portail. Deux pièces,
un seul achat.

Si personne ne s'en aperçoit : la charge est passée deux fois, et **la TVA est
déduite deux fois**. C'est la première chose qu'un vérificateur retrouve, parce
que c'est la plus facile à retrouver — un rapprochement sur le numéro de facture
du fournisseur suffit. Le redressement porte alors sur la TVA indûment déduite,
majorée des pénalités, et sur la charge réintégrée au résultat.

Le contrôle est trivial à écrire et il est presque toujours absent des logiciels
de gestion, parce que le doublon ne ressemble pas à une anomalie : les deux pièces
sont parfaitement conformes, chacune prise séparément. C'est leur coexistence qui
est fautive, et aucune règle du contexte D ne peut la voir — le moteur de
conformité contrôle **une** facture, jamais un ensemble.

DEUX NIVEAUX, ET IL FAUT LES DISTINGUER

**Certain** — même empreinte SHA-256. Ce n'est pas une ressemblance, c'est le même
fichier, octet pour octet. Le dépôt est refusé.

**Probable** — même émetteur, même numéro de document, même montant. Les deux
fichiers diffèrent (deux photographies du même papier, ou un scan et une photo),
mais la facture est la même. C'est le cas fréquent, et c'est celui qui échappe à
une comparaison d'empreintes. Le dépôt est accepté et **soumis à arbitrage
humain** : refuser automatiquement ferait perdre une pièce le jour où un
fournisseur réutilise un numéro d'une année sur l'autre.

Le seuil entre « je refuse » et « je signale » n'est pas un réglage cosmétique :
refuser à tort fait perdre une charge déductible, accepter à tort en crée une qui
n'existe pas. On refuse donc uniquement sur la certitude mathématique.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.collecte.domaine.pieces import EtatPiece, PieceJustificative

__all__ = [
    "NiveauSuspicion",
    "SuspicionDoublon",
    "detecter_doublons",
]


class NiveauSuspicion(StrEnum):
    """Ce que l'on sait, et non ce que l'on croit."""

    #: Même fichier, à l'octet près. Aucune interprétation possible.
    CERTAIN = "CERTAIN"
    #: Même document, fichiers différents. Demande un arbitrage.
    PROBABLE = "PROBABLE"


class SuspicionDoublon(BaseModel):
    """Une pièce déjà présente qui paraît être la même que celle qui arrive."""

    model_config = ConfigDict(frozen=True)

    piece_existante: str
    niveau: NiveauSuspicion
    #: Ce qui a déclenché la suspicion, en clair, pour que l'arbitre décide sans
    #: avoir à rouvrir les deux documents.
    indices: tuple[str, ...]
    #: Montant qui serait déduit deux fois si le doublon passait. C'est le chiffre
    #: qu'on met sous les yeux du comptable ; « doublon possible » tout seul ne
    #: déclenche aucune action.
    montant_en_jeu: Decimal | None = None

    @computed_field
    @property
    def bloquant(self) -> bool:
        return self.niveau is NiveauSuspicion.CERTAIN


def _meme_document(arrivante: PieceJustificative, existante: PieceJustificative) -> bool:
    """Même émetteur, même numéro, même montant.

    Les trois sont exigés ensemble. Deux suffiraient à produire des faux positifs
    en série : un même fournisseur émet beaucoup de factures du même montant, et
    deux fournisseurs différents numérotent tous deux « 001 ».
    """
    if arrivante.reference_document is None or existante.reference_document is None:
        return False
    if arrivante.emetteur is None or existante.emetteur is None:
        return False
    if arrivante.montant_ttc is None or existante.montant_ttc is None:
        return False
    return (
        arrivante.reference_document.strip().casefold()
        == existante.reference_document.strip().casefold()
        and arrivante.emetteur.strip().casefold() == existante.emetteur.strip().casefold()
        and arrivante.montant_ttc == existante.montant_ttc
    )


def detecter_doublons(
    arrivante: PieceJustificative,
    existantes: list[PieceJustificative],
) -> list[SuspicionDoublon]:
    """Confronte une pièce qui arrive au stock déjà reçu pour le même adhérent.

    Les pièces d'autres adhérents ne sont pas confrontées : deux clients d'un même
    fournisseur reçoivent des factures distinctes, et rien n'interdit qu'elles se
    ressemblent. La comparaison se fait donc à l'intérieur d'un dossier, ce qui la
    rend aussi beaucoup moins coûteuse.

    Les pièces archivées **restent** dans le champ de la comparaison. Une pièce
    archivée a été traitée : c'est justement pour cela qu'un second exemplaire
    serait un doublon. Les exclure serait la faute exacte que ce module doit
    empêcher.
    """
    suspicions: list[SuspicionDoublon] = []

    for existante in existantes:
        if existante.identifiant == arrivante.identifiant:
            continue
        if existante.entreprise != arrivante.entreprise:
            continue

        if (
            arrivante.empreinte is not None
            and existante.empreinte is not None
            and arrivante.empreinte == existante.empreinte
        ):
            suspicions.append(
                SuspicionDoublon(
                    piece_existante=existante.identifiant,
                    niveau=NiveauSuspicion.CERTAIN,
                    indices=(
                        f"empreinte identique ({arrivante.empreinte[:12]}…)",
                        f"pièce déjà reçue le {existante.recue_le.date()} "
                        f"par {existante.canal}",
                    ),
                    montant_en_jeu=existante.montant_ttc or arrivante.montant_ttc,
                )
            )
            continue

        if _meme_document(arrivante, existante):
            indices = [
                f"même émetteur ({existante.emetteur})",
                f"même numéro ({existante.reference_document})",
                f"même montant ({existante.montant_ttc})",
                f"reçue le {existante.recue_le.date()} par {existante.canal}",
            ]
            if existante.etat is EtatPiece.COMPTABILISEE:
                indices.append(f"déjà comptabilisée sous {existante.reference_ecriture}")
            suspicions.append(
                SuspicionDoublon(
                    piece_existante=existante.identifiant,
                    niveau=NiveauSuspicion.PROBABLE,
                    indices=tuple(indices),
                    montant_en_jeu=existante.montant_ttc,
                )
            )

    #: Le certain d'abord : c'est le seul qui appelle une décision immédiate.
    return sorted(suspicions, key=lambda s: (not s.bloquant, s.piece_existante))
