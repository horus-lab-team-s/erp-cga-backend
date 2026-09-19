"""Les tables du contexte Conformité (pas 92).

Jusqu'au pas 92, la conformité n'écrivait rien : un rapport se recalcule, il ne se
conserve pas (voir `RapportConformite`). Ce qui se conserve, c'est **ce que le cabinet
a décidé** du rapport, c'est-à-dire les écarts de constats. Ils ne se recalculent pas :
ils engagent la personne qui les a posés.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableEcartDeConstat", "TablePropositionDeRegle"]


class TableEcartDeConstat(Cloisonne, Document, Base):
    """Une décision d'écart, avec son histoire complète dans le document.

    ─────────────────────────────────────────────────────────────────────────────
    UNE LIGNE PAR DÉCISION, ET NON PAR CONSTAT

    Un écart refusé puis reproposé fait deux lignes : le rang est dans l'identifiant.
    Remplacer la première effacerait le refus, et c'est précisément le refus qu'un
    vérificateur voudra lire.

    ⚠️ **La clé porte le locataire.** L'identifiant dérive de la référence de la
    facture, choisie par le fournisseur : `FA-2026-0001:CGI-TVA-001:1` peut exister
    chez deux cabinets. Voir la règle établie au pas 17.

    `dossier` et `reference_document` sont promues pour la lecture de l'écran de la
    pièce ; `statut` et `propose_le` pour la file du second regard.
    ─────────────────────────────────────────────────────────────────────────────
    """

    __tablename__ = "ecart_de_constat"
    __table_args__ = (
        Index("ix_ecart_piece", "locataire", "dossier", "reference_document"),
        Index("ix_ecart_statut", "locataire", "statut", "propose_le"),
    )

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(200), primary_key=True)
    dossier: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_document: Mapped[str] = mapped_column(String(128), nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)
    propose_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TablePropositionDeRegle(Cloisonne, Document, Base):
    """Une règle construite par un cabinet, avec son essai et sa décision (pas 97).

    ⚠️ La clé porte le locataire : une règle du cabinet ne vaut que pour lui. Une proposition
    refusée reste une ligne : c'est avec elle qu'on comprend pourquoi la règle n'existe pas.
    """

    __tablename__ = "proposition_de_regle"
    __table_args__ = (Index("ix_proposition_regle_statut", "locataire", "statut", "propose_le"),)

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(40), primary_key=True)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)
    propose_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
