"""Les tables du contexte L : les offres de poste et les candidatures (16 octobre 2026).

Le reste du contenu public (articles, annonces, institutions) vit en YAML, en lecture
seule ; les offres, elles, s'écrivent depuis la console.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableCandidature", "TableOffreDePoste"]


class TableOffreDePoste(Cloisonne, Document, Base):
    """Une offre de poste du cabinet. Voir `domaine/recrutement.py`."""

    __tablename__ = "offre_de_poste"
    __table_args__ = (Index("ix_offre_de_poste_etat", "locataire", "etat", "creee_le"),)

    identifiant: Mapped[str] = mapped_column(String(32), primary_key=True)
    etat: Mapped[str] = mapped_column(String(16), nullable=False)
    creee_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TableCandidature(Cloisonne, Document, Base):
    """Une candidature reçue sur une offre, le CV au magasin de fichiers."""

    __tablename__ = "candidature"
    __table_args__ = (Index("ix_candidature_offre", "locataire", "offre", "recue_le"),)

    identifiant: Mapped[str] = mapped_column(String(32), primary_key=True)
    offre: Mapped[str] = mapped_column(String(32), nullable=False)
    recue_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
