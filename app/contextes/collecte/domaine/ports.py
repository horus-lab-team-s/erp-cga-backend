"""Les sources dont la collecte a besoin, décrites et non implémentées.

Le domaine énonce ce qu'il lui faut ; l'infrastructure le fournit. C'est ce qui
permet de tester tout le contexte sans base de données, sans stockage d'objets et
sans moteur d'extraction — et donc de le tester à chaque enregistrement de
fichier plutôt qu'une fois par jour.

**`ServiceExtraction` est un port et le restera.** L'OCR est le composant le plus
susceptible d'être remplacé de tout le système : moteur local aujourd'hui, service
distant demain, modèle spécialisé sur les factures camerounaises après-demain. Le
figer dans le domaine obligerait à réécrire le contexte à chaque changement de
fournisseur.

Aucun port ne prend de paramètre `locataire`. Le cloisonnement multi-tenant est
appliqué par la couche de persistance, jamais rappelé à chaque appel : un filtre
qu'on doit penser à passer est un filtre qu'on oubliera — et l'oublier, ici, c'est
montrer les pièces d'un adhérent à un autre.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.contextes.collecte.domaine.demandes import DemandePiece
from app.contextes.collecte.domaine.extraction import ExtractionOCR
from app.contextes.collecte.domaine.pieces import PieceJustificative, TypePiece

__all__ = [
    "DepotDemandes",
    "DepotPieces",
    "MagasinFichiers",
    "ServiceExtraction",
]


class DepotPieces(Protocol):
    """Les pièces reçues.

    Aucune méthode `supprimer` : une pièce justificative reçue ne s'efface pas,
    pas même un doublon. Elle s'archive avec son motif, et la trace de ce qui a
    été reçu reste entière. C'est la même règle qu'au contexte E pour les
    écritures, et elle a la même raison d'être.
    """

    def enregistrer(self, piece: PieceJustificative) -> None: ...

    def par_identifiant(self, identifiant: str) -> PieceJustificative | None: ...

    def du_dossier(self, entreprise: str) -> list[PieceJustificative]: ...

    def recues_entre(
        self, entreprise: str, debut: date, fin: date
    ) -> list[PieceJustificative]: ...

    def par_empreinte(self, entreprise: str, empreinte: str) -> list[PieceJustificative]:
        """Confrontation exacte, déléguée au stockage.

        Elle est ici plutôt que dans le domaine parce qu'un index sur l'empreinte
        la rend instantanée, là où le domaine devrait tout charger en mémoire.
        """
        ...


class DepotDemandes(Protocol):
    def enregistrer(self, demande: DemandePiece) -> None: ...

    def par_identifiant(self, identifiant: str) -> DemandePiece | None: ...

    def ouvertes(self, entreprise: str | None = None) -> list[DemandePiece]: ...


class MagasinFichiers(Protocol):
    """Le stockage du fichier lui-même, séparé de ses métadonnées.

    La séparation n'est pas cosmétique : les métadonnées se requêtent, se
    sauvegardent et se répliquent en quelques mégaoctets, là où les fichiers
    pèsent des téraoctets et ne se requêtent jamais. Les mélanger rendrait toute
    la base ingérable au bout de deux exercices.
    """

    def deposer(self, cle: str, contenu: bytes, *, type_mime: str) -> None: ...

    def lire(self, cle: str) -> bytes: ...

    def existe(self, cle: str) -> bool: ...


class ServiceExtraction(Protocol):
    """Le moteur de lecture automatique, quel qu'il soit."""

    def extraire(self, contenu: bytes, *, piece: str, type_attendu: TypePiece) -> ExtractionOCR:
        ...
