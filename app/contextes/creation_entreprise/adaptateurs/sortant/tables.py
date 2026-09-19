"""La table du contexte I.

Un dossier de création est un agrégat toujours lu entier — checklist, jalons,
identifiants — et jamais interrogé par ses parties. Il est donc conservé comme
document, avec pour seules colonnes promues celles sur lesquelles le pipeline
filtre : l'étape, et la date du dernier mouvement.

⚠️ `immobile_depuis` est **promue en colonne** alors qu'elle se déduit des jalons.
C'est une dénormalisation assumée, et la seule du contexte : la requête de relance
— « les dossiers qui n'ont pas bougé depuis quinze jours » — est celle que le
chargé de formalités lance tous les matins. La calculer en Python obligerait à
charger tout le pipeline pour n'en garder qu'une poignée. La colonne est
recalculée à chaque écriture depuis les jalons, jamais saisie : elle ne peut donc
pas diverger de la vérité qu'elle résume.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableDossierCreation"]


class TableDossierCreation(Cloisonne, Document, Base):
    """Un dossier du tunnel de création."""

    __tablename__ = "dossier_creation"
    __table_args__ = (
        Index("ix_dossier_creation_pipeline", "locataire", "etape", "immobile_depuis"),
    )

    reference: Mapped[str] = mapped_column(String(40), primary_key=True)

    #: Promue : c'est la colonne du pipeline, celle par laquelle l'écran groupe.
    etape: Mapped[str] = mapped_column(String(30), nullable=False)

    #: Promue : voir l'en-tête. Recalculée à l'écriture, jamais saisie.
    immobile_depuis: Mapped[date] = mapped_column(Date, nullable=False)

    #: Promue pour la recherche par nom, qui est la façon dont le chargé de
    #: formalités retrouve un dossier quand le fondateur l'appelle.
    denomination_souhaitee: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Promu pour retrouver, depuis le portefeuille, le dossier qui a créé une
    #: entreprise. Nul tant que la conversion n'a pas eu lieu.
    converti_en: Mapped[str | None] = mapped_column(String(20))
