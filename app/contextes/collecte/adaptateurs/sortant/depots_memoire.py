"""Réalisations en mémoire des ports du contexte C.

Cible : PostgreSQL pour les métadonnées, un stockage d'objets pour les fichiers.
La séparation entre les deux n'est pas cosmétique — voir le port `MagasinFichiers` :
les métadonnées se requêtent et se répliquent en quelques mégaoctets, les fichiers
pèsent des téraoctets et ne se requêtent jamais.

**Une règle est appliquée ici : une pièce ne se supprime pas.** Le port n'expose
aucune méthode pour cela, et le dépôt n'en offre pas non plus — pas même pour un
doublon. Une pièce s'archive avec son motif, et la trace de ce qui a été reçu reste
entière. C'est la même discipline qu'au registre des écritures.
"""

from __future__ import annotations

from datetime import date

from app.contextes.collecte.domaine.demandes import DemandePiece
from app.contextes.collecte.domaine.pieces import PieceJustificative
from app.partage.depot_memoire import EntrepotMemoire

__all__ = [
    "DepotDemandesMemoire",
    "DepotPiecesMemoire",
    "MagasinFichiersMemoire",
    "PieceIntrouvable",
]


class PieceIntrouvable(LookupError):
    """Une pièce désignée par son identifiant et absente du dépôt."""


class DepotPiecesMemoire:
    """Les pièces reçues, en mémoire, pour un locataire."""

    def __init__(self, locataire: str = "CGA-BRCG") -> None:
        self._entrepot: EntrepotMemoire[PieceJustificative] = EntrepotMemoire(
            locataire, cle=lambda piece: piece.identifiant
        )

    @classmethod
    def avec_demonstration(cls, locataire: str = "CGA-BRCG") -> DepotPiecesMemoire:
        from app.contextes.collecte.adaptateurs.sortant.donnees_demo import PIECES_DEMO

        depot = cls(locataire)
        depot._entrepot.poser_tout(list(PIECES_DEMO.values()))
        return depot

    # ── Port `DepotPieces` ──────────────────────────────────────────────────

    def enregistrer(self, piece: PieceJustificative) -> None:
        self._entrepot.poser(piece)

    def par_identifiant(self, identifiant: str) -> PieceJustificative | None:
        return self._entrepot.prendre(identifiant)

    def du_dossier(self, entreprise: str) -> list[PieceJustificative]:
        return self._trier(self._entrepot.filtrer(lambda p: p.entreprise == entreprise))

    def recues_entre(
        self, entreprise: str, debut: date, fin: date
    ) -> list[PieceJustificative]:
        return self._trier(
            self._entrepot.filtrer(
                lambda p: p.entreprise == entreprise and debut <= p.recue_le.date() <= fin
            )
        )

    def par_empreinte(self, entreprise: str, empreinte: str) -> list[PieceJustificative]:
        return self._trier(
            self._entrepot.filtrer(
                lambda p: p.entreprise == entreprise and p.empreinte == empreinte
            )
        )

    # ── Commodités pour les adaptateurs entrants ────────────────────────────

    def lire(self, identifiant: str) -> PieceJustificative:
        piece = self.par_identifiant(identifiant)
        if piece is None:
            raise PieceIntrouvable(f"pièce « {identifiant} » absente du dépôt.")
        return piece

    def toutes(self) -> list[PieceJustificative]:
        return self._trier(self._entrepot.tout())

    @staticmethod
    def _trier(pieces: list[PieceJustificative]) -> list[PieceJustificative]:
        """La plus récente d'abord : c'est l'ordre de la boîte de réception."""
        return sorted(pieces, key=lambda p: (p.recue_le, p.identifiant), reverse=True)


class DepotDemandesMemoire:
    """Les demandes de pièces émises par le cabinet."""

    def __init__(self, locataire: str = "CGA-BRCG") -> None:
        self._entrepot: EntrepotMemoire[DemandePiece] = EntrepotMemoire(
            locataire, cle=lambda demande: demande.identifiant
        )

    @classmethod
    def avec_demonstration(cls, locataire: str = "CGA-BRCG") -> DepotDemandesMemoire:
        from app.contextes.collecte.adaptateurs.sortant.donnees_demo import DEMANDES_DEMO

        depot = cls(locataire)
        depot._entrepot.poser_tout(list(DEMANDES_DEMO.values()))
        return depot

    def enregistrer(self, demande: DemandePiece) -> None:
        self._entrepot.poser(demande)

    def par_identifiant(self, identifiant: str) -> DemandePiece | None:
        return self._entrepot.prendre(identifiant)

    def ouvertes(self, entreprise: str | None = None) -> list[DemandePiece]:
        return sorted(
            self._entrepot.filtrer(
                lambda d: d.ouverte and (entreprise is None or d.entreprise == entreprise)
            ),
            key=lambda d: (d.demandee_le, d.identifiant),
        )

    def toutes(self, entreprise: str | None = None) -> list[DemandePiece]:
        return sorted(
            self._entrepot.filtrer(
                lambda d: entreprise is None or d.entreprise == entreprise
            ),
            key=lambda d: (d.demandee_le, d.identifiant),
        )


class MagasinFichiersMemoire:
    """Le contenu des fichiers, en mémoire.

    Réalisation de commodité, et rien de plus : elle rend les tests de bout en
    bout possibles sans stockage d'objets. En production, un magasin en mémoire
    saturerait le processus au premier exercice — et perdrait tout au premier
    redémarrage, alors que la conservation des pièces comptables est de **dix
    ans** selon le SYSCOHADA révisé.
    """

    def __init__(self) -> None:
        self._fichiers: dict[str, tuple[bytes, str]] = {}

    def deposer(self, cle: str, contenu: bytes, *, type_mime: str) -> None:
        self._fichiers[cle] = (contenu, type_mime)

    def lire(self, cle: str) -> bytes:
        if cle not in self._fichiers:
            raise KeyError(
                f"fichier « {cle} » absent du magasin. Une pièce dont le fichier a "
                "disparu n'est plus une pièce justificative."
            )
        return self._fichiers[cle][0]

    def type_mime(self, cle: str) -> str:
        return self._fichiers[cle][1]

    def existe(self, cle: str) -> bool:
        return cle in self._fichiers
