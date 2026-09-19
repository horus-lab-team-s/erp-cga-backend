"""Les tables du contexte J · Pilotage (pas 100).

Jusqu'au pas 100, le pilotage n'écrivait rien : le score se calcule, il ne se conserve pas
(voir `ScoreRisque`). Ce qui se conserve, c'est **ce que la direction a décidé** devant ce
score. Une décision ne se recalcule pas : elle engage la personne qui l'a prise.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableDecisionDeDirection", "TableRapportMensuel"]


class TableDecisionDeDirection(Cloisonne, Document, Base):
    """Une décision de direction sur un dossier, avec son histoire dans le document.

    ⚠️ **La clé porte le locataire** : le NIU d'un adhérent peut être suivi par deux
    cabinets successifs, et `M065544332211L:RENFORCER_SUIVI:1` exister chez les deux.

    Une décision close reste une ligne : c'est avec elle qu'on relit, en comité, ce qui a
    été tenté sur le dossier. `dossier` et `prise_le` sont promues pour la lecture de la
    fiche ; `statut` pour retrouver ce qui est en cours.
    """

    __tablename__ = "decision_de_direction"
    __table_args__ = (
        Index("ix_decision_direction_dossier", "locataire", "dossier", "prise_le"),
        Index("ix_decision_direction_statut", "locataire", "statut"),
    )

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(120), primary_key=True)
    dossier: Mapped[str] = mapped_column(String(64), nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)
    prise_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TableRapportMensuel(Cloisonne, Document, Base):
    """Un rapport mensuel archivé (pas 106), avec son contenu figé et son empreinte.

    `mois` et `version` sont promues pour l'ordre de la liste ; `empreinte` pour qu'un contrôle
    puisse la comparer à celle du journal d'audit sans relire le document.
    """

    __tablename__ = "rapport_mensuel"
    __table_args__ = (Index("ix_rapport_mensuel_mois", "locataire", "mois", "version"),)

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(32), primary_key=True)
    mois: Mapped[str] = mapped_column(String(7), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    empreinte: Mapped[str] = mapped_column(String(64), nullable=False)
