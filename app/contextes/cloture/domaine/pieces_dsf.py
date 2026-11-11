"""Les pièces à joindre à la DSF, et où en est chacune (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
D'OÙ VIENT LA LISTE

Guide utilisateur DSF 2025 de la DGI, § 12 « Tableau de correspondance des pièces à
joindre à la DSF » : dix-neuf pièces, chacune rattachée à une page de la DSF, selon le
système. Le portail garde le bouton « Soumettre » inactif tant qu'il en manque une
(§ 13) : cette liste dit au cabinet, AVANT le dépôt, ce qui lui manque encore.

    Système Normal : pièces 1 à 9, puis 16 à 19.
    Système Minimal de Trésorerie (SMT) : pièces 1 à 3, puis 8 à 18.

OÙ EN EST UNE PIÈCE
    PRODUITE   la plateforme la fabrique : balances auxiliaires, modèles FOURNISSEURS et
               TIERS de la DGI, tableau des amortissements (depuis le registre des
               immobilisations, 17 octobre 2026) ; rien à faire, sinon la télécharger ;
    JOINTE     le cabinet l'a versée (un PDF ou un scan) ;
    NEANT      sans objet pour ce dossier (pas de prix de transfert, pas d'accises…),
               avec le motif, qui va au journal ;
    A_FOURNIR  le reste.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.contextes.cloture.domaine.entites import SystemeDsf

__all__ = ["PIECES_DSF", "EtatPiece", "LignePiece", "Marque", "PieceDsf", "etat_des_pieces"]


class EtatPiece(StrEnum):
    PRODUITE = "PRODUITE"
    JOINTE = "JOINTE"
    NEANT = "NEANT"
    A_FOURNIR = "A_FOURNIR"


@dataclass(frozen=True)
class PieceDsf:
    numero: int
    libelle: str
    #: La page de la DSF où le portail l'attend, par système ; `None` : pas demandée.
    page_normal: str | None
    page_smt: str | None
    #: Ce que la plateforme produit elle-même : la clé du téléchargement, sinon `None`.
    produite: str | None = None


#: Le tableau du § 12 du guide de la DGI (colonnes « DSF NORMAL » et « DSF SMT »).
PIECES_DSF: tuple[PieceDsf, ...] = (
    PieceDsf(
        1, "Attestation de visa (expert-comptable, CGA ou conseil fiscal)", "Fiche R1", "Fiche R1"
    ),
    PieceDsf(2, "Balance auxiliaire clients, en Excel", "Note 7", "Note 2", "balance-clients"),
    PieceDsf(
        3,
        "Balance auxiliaire fournisseurs, en Excel",
        "Note 17",
        "T4 (extrait)",
        "balance-fournisseurs",
    ),
    PieceDsf(4, "Déclaration des dépenses fiscales", "Fiche R2", None),
    PieceDsf(5, "Déclaration des prix de transfert, selon le modèle annexé", "Fiche R2", None),
    PieceDsf(6, "Attestation de dématérialisation des titres", "Fiche R3", None),
    PieceDsf(
        7, "Tableau des amortissements, en Excel", "Note 3C", None, "tableau-amortissements"
    ),
    PieceDsf(
        8, "Déclaration annuelle des sommes versées aux tiers", "Note 24", "Note 6", "dgi-tiers"
    ),
    PieceDsf(
        9, "Déclaration annuelle des achats par fournisseur", "Note 17", "T4", "dgi-fournisseurs"
    ),
    PieceDsf(10, "Tableau de suivi du matériel, du mobilier et des cautions", None, "Note 1"),
    PieceDsf(11, "État des stocks", None, "Note 2"),
    PieceDsf(12, "État des créances et dettes non échues au 31 décembre", None, "Note 3"),
    PieceDsf(13, "Journal mensuel par banque et journal mensuel par caisse", None, "Note 4"),
    PieceDsf(14, "Journal de suivi des créances impayées", None, "Note 5"),
    PieceDsf(15, "Journal de suivi des dettes à payer", None, "Note 6"),
    PieceDsf(16, "DIPE (document d'information sur le personnel employé)", "C1 Note 27A", "Note 4"),
    PieceDsf(17, "Précomptes et acomptes retenus, à reverser", "CF1 Quater", "Tableau T9"),
    PieceDsf(18, "Précomptes et acomptes retenus, à déduire", "CF1 Quater", "Tableau T9"),
    PieceDsf(19, "Droits d'accises", "C2 Note 25", None),
)


@dataclass(frozen=True)
class Marque:
    """Ce que le cabinet a dit d'une pièce : versée, ou sans objet. La dernière compte."""

    etat: EtatPiece
    par: str
    le: datetime
    motif: str | None = None
    nom_du_fichier: str | None = None
    empreinte: str | None = None


@dataclass(frozen=True)
class LignePiece:
    piece: PieceDsf
    page: str
    etat: EtatPiece
    marque: Marque | None


def etat_des_pieces(systeme: SystemeDsf, marques: dict[int, Marque]) -> list[LignePiece]:
    """Les pièces que le système demande, chacune avec son état."""
    lignes = []
    for piece in PIECES_DSF:
        page = piece.page_normal if systeme is SystemeDsf.NORMAL else piece.page_smt
        if page is None:
            continue
        marque = marques.get(piece.numero)
        if piece.produite:
            etat = EtatPiece.PRODUITE
        elif marque is not None and marque.etat in (EtatPiece.JOINTE, EtatPiece.NEANT):
            etat = marque.etat
        else:
            etat = EtatPiece.A_FOURNIR
        lignes.append(LignePiece(piece=piece, page=page, etat=etat, marque=marque))
    return lignes
