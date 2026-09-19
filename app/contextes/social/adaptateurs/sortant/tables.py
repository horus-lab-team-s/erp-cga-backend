"""Les tables du contexte G.

Deux tables, comme deux ports. Le salarié et ses contrats sont lus séparément :
l'écran du personnel liste des salariés, le calcul de paie charge des contrats.
Les fondre en un document unique obligerait à lire toute l'histoire d'un salarié
pour afficher son nom.

⚠️ **Aucun bulletin en base**, et c'est la décision qui commande ce schéma. Un
bulletin se calcule à partir du contrat et du référentiel à la date de la période.
Le stocker créerait deux vérités : le bulletin figé et le bulletin recalculé, et
personne ne saurait dire laquelle fait foi le jour où un taux est corrigé
rétroactivement — ce qui arrive à chaque loi de finances.

Ce qui devra être stocké, quand la paie sera réellement versée, c'est le **paiement**
— pas le calcul. Il aura sa propre table et son propre invariant d'immuabilité,
comme les écritures comptables.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Cloisonne, Document

__all__ = ["TableContrat", "TableSalarie"]


class TableSalarie(Cloisonne, Document, Base):
    """Une personne employée par un dossier du portefeuille."""

    __tablename__ = "salarie"
    __table_args__ = (Index("ix_salarie_dossier", "locataire", "entreprise", "nom"),)

    matricule: Mapped[str] = mapped_column(String(40), primary_key=True)

    #: Promu : toutes les lectures partent du dossier employeur.
    entreprise: Mapped[str] = mapped_column(String(20), nullable=False)
    nom: Mapped[str] = mapped_column(String(120), nullable=False)
    prenom: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Promu pour le rapprochement avec les états CNPS, qui désignent par ce numéro.
    matricule_cnps: Mapped[str | None] = mapped_column(String(40))


class TableContrat(Cloisonne, Document, Base):
    """Un engagement, sur son intervalle."""

    __tablename__ = "contrat_travail"
    __table_args__ = (
        Index("ix_contrat_dossier_periode", "locataire", "entreprise", "debut", "fin"),
        Index("ix_contrat_salarie", "locataire", "salarie", "debut"),
    )

    #: Un salarié peut avoir plusieurs contrats successifs : la clé porte donc la
    #: date de début. Un CDD suivi d'un CDI chez le même employeur est le cas
    #: courant, et il doit rester deux lignes distinctes.
    identifiant: Mapped[str] = mapped_column(String(60), primary_key=True)

    salarie: Mapped[str] = mapped_column(String(40), nullable=False)
    entreprise: Mapped[str] = mapped_column(String(20), nullable=False)
    debut: Mapped[date] = mapped_column(Date, nullable=False)
    #: Nul = toujours en cours. Promue pour filtrer les contrats en vigueur à une
    #: date sans charger tout le fichier du personnel.
    fin: Mapped[date | None] = mapped_column(Date)
