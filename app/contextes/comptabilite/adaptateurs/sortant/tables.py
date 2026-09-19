"""Les tables du contexte E.

─────────────────────────────────────────────────────────────────────────────────
LA CLÉ D'ÉCRITURE PORTE ENFIN LE DOSSIER

`2026/AC/000042` n'est unique qu'à l'intérieur d'un dossier — défaut trouvé au
premier branchement des adaptateurs, et contourné jusqu'ici par un dépôt en
mémoire ouvert *pour un dossier*. En base, la clé primaire est composite :
locataire, entreprise, exercice, journal, numéro. La question posée à l'époque —
« faudra-t-il une colonne `entreprise` ? » — se répond ici par oui.

**Aucune ligne d'écriture n'est supprimée.** Une écriture validée est immuable ;
ce qui l'annule est une contre-passation, qui est une écriture de plus. Il
n'existe donc pas de `DELETE` sur cette table dans le code, et il ne doit pas en
apparaître.

⚠️ LA NUMÉROTATION CONTINUE N'EST PAS ENCORE GARANTIE PAR UNE SÉQUENCE

Le port `DepotEcritures` exige une numérotation continue **même en accès
concurrent**. La contrainte d'unicité empêche deux écritures de porter le même
numéro : la seconde échoue et recommence. C'est correct, et ce n'est pas une
séquence — deux insertions concurrentes se battent au lieu de se répartir. Sur le
volume d'un cabinet, la différence est théorique ; elle cesserait de l'être sur
un import de reprise massif.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = [
    "TableEcriture",
    "TablePlanImputation",
    "TableRapprochementBancaire",
    "TableLettrage",
    "TableRevueDeDossier",
]


class TableEcriture(Cloisonne, Document, Base):
    """Une écriture, avec ses lignes dans le document."""

    __tablename__ = "ecriture"
    __table_args__ = (
        Index("ix_ecriture_dossier_exercice", "locataire", "entreprise", "exercice"),
        Index("ix_ecriture_date", "locataire", "entreprise", "date_operation"),
        Index("ix_ecriture_piece", "locataire", "piece_justificative"),
    )

    #: Clé composite — voir l'en-tête. L'ordre suit celui du tri naturel :
    #: un dossier, un exercice, un journal, un numéro.
    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), primary_key=True)
    exercice: Mapped[str] = mapped_column(String(10), primary_key=True)
    journal: Mapped[str] = mapped_column(String(10), primary_key=True)
    numero: Mapped[int] = mapped_column(Integer, primary_key=True)

    date_operation: Mapped[date] = mapped_column(Date, nullable=False)
    libelle: Mapped[str] = mapped_column(String(255), nullable=False)
    etat: Mapped[str] = mapped_column(String(20), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    #: Promue : c'est elle qui relie l'écriture à la pièce dont elle procède, et
    #: la question « cette pièce est-elle comptabilisée » se pose constamment.
    piece_justificative: Mapped[str | None] = mapped_column(String(64))


class TablePlanImputation(Cloisonne, Document, Base):
    """Le plan d'imputation d'un dossier.

    Propre à chaque adhérent : une entreprise de BTP et une clinique n'imputent
    pas les mêmes achats sur les mêmes comptes. C'est donc une donnée du dossier,
    pas un référentiel — d'où sa table, là où le plan SYSCOHADA et les journaux
    restent en code, identiques pour tout le monde.

    Un plan est **un document entier** : il porte ses comptes par défaut et ses
    règles ordonnées, et l'ordre des règles *est* la règle — la première qui
    correspond l'emporte. Le découper en lignes obligerait à reconstituer cet
    ordre à chaque lecture, pour un objet qu'on ne lit jamais autrement qu'en
    entier.
    """

    __tablename__ = "plan_imputation"

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), primary_key=True)


class TableRapprochementBancaire(Cloisonne, Document, Base):
    """Un relevé importé et son rapprochement, en un document (pas 101).

    ⚠️ **Rien n'est écrit dans la table des écritures.** Le rapprochement désigne les lignes
    d'écriture par leur clé ; une écriture validée ne se réécrit pas pour porter une marque.

    Un relevé abandonné reste une ligne : on doit pouvoir dire pourquoi un relevé importé
    n'a jamais été rapproché. `journal`, `du` et `au` sont promues pour refuser deux relevés
    qui se chevauchent ; `statut` pour retrouver ce qui reste à arrêter.
    """

    __tablename__ = "rapprochement_bancaire"
    __table_args__ = (
        Index("ix_rapprochement_periode", "locataire", "entreprise", "journal", "au"),
    )

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)
    journal: Mapped[str] = mapped_column(String(10), nullable=False)
    du: Mapped[date] = mapped_column(Date, nullable=False)
    au: Mapped[date] = mapped_column(Date, nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)


class TableRevueDeDossier(Cloisonne, Document, Base):
    """La revue d'un mois transmis, avec ses remarques et son histoire (pas 102).

    Une revue validée reste une ligne : c'est la trace du second regard. `statut` est promu
    pour la file du réviseur (les mois transmis) et celle du comptable (les mois renvoyés).
    """

    __tablename__ = "revue_de_dossier"
    __table_args__ = (Index("ix_revue_statut", "locataire", "statut", "du"),)

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(40), primary_key=True)
    du: Mapped[date] = mapped_column(Date, nullable=False)
    au: Mapped[date] = mapped_column(Date, nullable=False)
    statut: Mapped[str] = mapped_column(String(16), nullable=False)


class TableLettrage(Cloisonne, Document, Base):
    """Un lettrage de lignes sur un compte de tiers (pas 108).

    À part des écritures, qui sont intangibles : lettrer n'est pas réécrire. Un lettrage défait
    reste une ligne, avec son statut ; sa lettre n'est jamais réattribuée.
    """

    __tablename__ = "lettrage"
    __table_args__ = (
        Index("ix_lettrage_compte", "locataire", "entreprise", "exercice", "compte"),
    )

    locataire: Mapped[str] = mapped_column(String(64), primary_key=True)
    entreprise: Mapped[str] = mapped_column(String(20), primary_key=True)
    identifiant: Mapped[str] = mapped_column(String(40), primary_key=True)
    exercice: Mapped[str] = mapped_column(String(16), nullable=False)
    compte: Mapped[str] = mapped_column(String(8), nullable=False)
    statut: Mapped[str] = mapped_column(String(8), nullable=False)
