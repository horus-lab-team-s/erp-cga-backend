"""Les tables du contexte H · Clôture et DSF.

La première depuis le 14 octobre 2026 : jusque-là, la clôture ne persistait rien à
elle (l'exercice clos est au portefeuille, l'à-nouveau en comptabilité). Les
versions de la DSF, elles, n'appartiennent qu'à elle.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableDeclarationDSF"]


class TableDeclarationDSF(Cloisonne, Document, Base):
    """Une version de la DSF d'un exercice. Voir `domaine/declaration_dsf.py`."""

    __tablename__ = "declaration_dsf"
    __table_args__ = (Index("ix_declaration_dsf_dossier", "locataire", "entreprise", "exercice"),)

    identifiant: Mapped[str] = mapped_column(String(96), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(64), nullable=False)
    exercice: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    etat: Mapped[str] = mapped_column(String(20), nullable=False)
    preparee_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
