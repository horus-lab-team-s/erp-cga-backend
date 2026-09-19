"""La table du contexte B.

Un dossier est un agrégat riche — régimes, rattachements, adhésions, exercices,
mandats, dirigeants, associés, tiers — toujours lu entier. Il est donc conservé
comme document, avec les colonnes sur lesquelles on filtre promues. Voir
l'en-tête du mixin `Document`.

⚠️ `regime` et `centre` **ne sont pas** des colonnes, et c'est délibéré : ils se
résolvent à une date. Une colonne « régime » figerait le régime courant et
inviterait à des requêtes qui liraient le régime de 2026 sur une facture de 2022 —
le troisième des huit pièges du dossier de vision.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableEntreprise"]


class TableEntreprise(Cloisonne, Document, Base):
    """Un dossier du portefeuille."""

    __tablename__ = "entreprise"
    __table_args__ = (Index("ix_entreprise_denomination", "locataire", "denomination"),)

    niu: Mapped[str] = mapped_column(String(20), primary_key=True)

    #: Promues pour la liste et la recherche du sélecteur de dossier, qui
    #: interroge sur la dénomination et le NIU.
    denomination: Mapped[str] = mapped_column(String(255), nullable=False)
    forme_juridique: Mapped[str] = mapped_column(String(20), nullable=False)
    date_creation: Mapped[date] = mapped_column(Date, nullable=False)
    activite: Mapped[str | None] = mapped_column(String(255))
    siege: Mapped[str | None] = mapped_column(String(255))
