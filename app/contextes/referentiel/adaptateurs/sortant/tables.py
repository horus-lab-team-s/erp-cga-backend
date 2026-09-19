"""Les tables du référentiel (pas 95).

Le référentiel commun reste un fichier versionné. Ce qui se conserve en base, ce sont
**les décisions d'un cabinet** sur ce référentiel : validations et nouvelles versions.
Voir `domaine/surcouche.py`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableDecisionReferentiel"]


class TableDecisionReferentiel(Cloisonne, Document, Base):
    """Une décision d'un cabinet sur un paramètre, avec toute son histoire dans le document.

    ⚠️ La clé porte le locataire : c'est ce qui fait qu'une décision d'un cabinet ne vaut
    que pour lui. Une clé sur le seul identifiant ferait qu'un cabinet pourrait voir, par
    collision, la décision d'un autre sur le même paramètre.

    `code` et `statut` sont promus : l'écran lit « les propositions en attente » et
    « l'histoire de ce paramètre ».
    """

    __tablename__ = "decision_referentiel"
    __table_args__ = (
        Index("ix_decision_referentiel_code", "locataire", "code", "propose_le"),
        Index("ix_decision_referentiel_statut", "locataire", "statut"),
    )

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(120), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)
    propose_le: Mapped[datetime] = mapped_column(DateTime, nullable=False)
