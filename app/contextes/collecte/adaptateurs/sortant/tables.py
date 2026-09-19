"""Les tables du contexte C.

Les pièces portent beaucoup de colonnes promues, et c'est justifié : la boîte de
réception filtre sur le dossier, le canal, l'état et l'ancienneté, et c'est
l'écran le plus consulté du cabinet. Une requête qui devrait ouvrir le document
de chaque ligne pour filtrer ne tiendrait pas la charge.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableDemandePiece", "TablePieceJustificative"]


class TablePieceJustificative(Cloisonne, Document, Base):
    """Une pièce reçue."""

    __tablename__ = "piece_justificative"
    __table_args__ = (
        Index("ix_piece_dossier_etat", "locataire", "entreprise", "etat"),
        Index("ix_piece_recue_le", "locataire", "recue_le"),
        # L'empreinte n'est **pas** unique : le contexte C refuse un doublon
        # certain à la réception, mais deux dossiers différents peuvent
        # légitimement recevoir le même fichier — une facture d'un fournisseur
        # commun, transmise par les deux adhérents.
        Index("ix_piece_empreinte", "locataire", "empreinte"),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), nullable=False)
    canal: Mapped[str] = mapped_column(String(32), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    etat: Mapped[str] = mapped_column(String(32), nullable=False)

    depose_le: Mapped[date] = mapped_column(Date, nullable=False)
    recue_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    empreinte: Mapped[str | None] = mapped_column(String(64))
    reference_document: Mapped[str | None] = mapped_column(String(120))
    date_document: Mapped[date | None] = mapped_column(Date)
    montant_ttc: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    emetteur: Mapped[str | None] = mapped_column(String(255))
    reference_ecriture: Mapped[str | None] = mapped_column(String(64))


class TableDemandePiece(Cloisonne, Document, Base):
    """Une pièce que le cabinet attend."""

    __tablename__ = "demande_piece"
    __table_args__ = (
        Index("ix_demande_dossier_statut", "locataire", "entreprise", "statut"),
        Index("ix_demande_attendue_pour", "locataire", "attendue_pour"),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), nullable=False)
    type_attendu: Mapped[str] = mapped_column(String(40), nullable=False)
    statut: Mapped[str] = mapped_column(String(32), nullable=False)
    demandee_le: Mapped[date] = mapped_column(Date, nullable=False)
    attendue_pour: Mapped[date | None] = mapped_column(Date)
    #: Promue : c'est elle qui décide si un dossier peut être déposé, et la
    #: requête « que reste-t-il de bloquant » se pose à chaque échéance.
    bloquante: Mapped[bool] = mapped_column(Boolean, nullable=False)
