"""Les dépôts PostgreSQL du contexte C.

⚠️ Ces classes doivent réaliser **toute** la surface de leur port. Trois méthodes
y manquaient — `par_identifiant`, `recues_entre`, `par_empreinte` — et rien ne le
signalait : les tests tournaient en mémoire, où la réalisation était complète. Le
défaut n'est apparu qu'en interrogeant l'application réelle sur PostgreSQL, où la
route de téléchargement rendait un `500`.

`test_conformite_des_ports.py` compare désormais chaque réalisation à son port.
Un `Protocol` ne vérifie rien à l'exécution : c'est un contrat pour l'analyse
statique, et une méthode oubliée ne se voit qu'au premier appel.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import PieceIntrouvable
from app.contextes.collecte.adaptateurs.sortant.tables import (
    TableDemandePiece,
    TablePieceJustificative,
)
from app.contextes.collecte.domaine.demandes import DemandePiece, StatutDemande
from app.contextes.collecte.domaine.pieces import PieceJustificative
from app.infrastructure.depot_document import DepotDocument

__all__ = ["DepotDemandesSql", "DepotPiecesSql"]


class DepotPiecesSql(DepotDocument[PieceJustificative]):
    """Réalisation de `DepotPieces`.

    ⚠️ Aucune méthode `supprimer`, ici comme en mémoire. Une pièce reçue ne
    disparaît pas : elle s'archive, avec son motif. Le port ne l'offre pas, et
    aucun `DELETE` ne doit apparaître sur cette table.
    """

    _table = TablePieceJustificative
    _entite = PieceJustificative

    def _cle(self, entite: PieceJustificative) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: PieceJustificative) -> dict[str, Any]:
        return {
            "entreprise": entite.entreprise,
            "canal": entite.canal.value,
            "type": entite.type.value,
            "etat": entite.etat.value,
            "depose_le": entite.depose_le,
            "recue_le": entite.recue_le,
            "empreinte": entite.empreinte,
            "reference_document": entite.reference_document,
            "date_document": entite.date_document,
            "montant_ttc": entite.montant_ttc,
            "emetteur": entite.emetteur,
            "reference_ecriture": entite.reference_ecriture,
        }

    def lire(self, identifiant: str) -> PieceJustificative:
        """La pièce, ou `PieceIntrouvable`.

        Distincte de `par_identifiant` à dessein : ici l'absence est une
        anomalie — l'appelant tient la référence d'ailleurs et s'attend à la
        trouver. Là-bas, l'absence est une réponse.
        """
        trouvee = self.par_identifiant(identifiant)
        if trouvee is None:
            raise PieceIntrouvable(f"aucune pièce {identifiant}")
        return trouvee

    def par_identifiant(self, identifiant: str) -> PieceJustificative | None:
        return self._premier(
            self._requete().where(TablePieceJustificative.identifiant == identifiant)
        )

    def recues_entre(
        self, entreprise: str, debut: date, fin: date
    ) -> list[PieceJustificative]:
        """Les pièces d'un dossier reçues dans un intervalle, bornes comprises.

        ⚠️ La comparaison porte sur la **date** de `recue_le`, qui est un
        instant. `<= fin` sur l'instant exclurait tout ce qui a été reçu après
        minuit le dernier jour — c'est-à-dire presque tout ce jour-là, et
        personne ne s'en apercevrait avant une déclaration incomplète.
        """
        return self._tous(
            self._requete()
            .where(TablePieceJustificative.entreprise == entreprise)
            .where(func.date(TablePieceJustificative.recue_le) >= debut)
            .where(func.date(TablePieceJustificative.recue_le) <= fin)
            .order_by(TablePieceJustificative.recue_le.desc())
        )

    def par_empreinte(self, entreprise: str, empreinte: str) -> list[PieceJustificative]:
        """La confrontation exacte, servie par l'index sur la colonne promue.

        C'est pour cela que l'empreinte est une colonne et pas seulement une
        clé du document : le domaine devrait sinon charger tout le dossier en
        mémoire pour répondre.
        """
        return self._tous(
            self._requete()
            .where(TablePieceJustificative.entreprise == entreprise)
            .where(TablePieceJustificative.empreinte == empreinte)
            .order_by(TablePieceJustificative.recue_le.desc())
        )

    def du_dossier(self, entreprise: str) -> list[PieceJustificative]:
        return self._tous(
            self._requete()
            .where(TablePieceJustificative.entreprise == entreprise)
            .order_by(TablePieceJustificative.recue_le.desc())
        )

    def toutes(self) -> list[PieceJustificative]:
        """Les plus récentes d'abord — c'est l'ordre de la boîte de réception,
        et celui dans lequel un collaborateur travaille."""
        return self._tous(
            self._requete().order_by(TablePieceJustificative.recue_le.desc())
        )

    def enregistrer(self, piece: PieceJustificative) -> None:
        self._poser(piece)


class DepotDemandesSql(DepotDocument[DemandePiece]):
    """Réalisation de `DepotDemandes`."""

    _table = TableDemandePiece
    _entite = DemandePiece

    def _cle(self, entite: DemandePiece) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: DemandePiece) -> dict[str, Any]:
        return {
            "entreprise": entite.entreprise,
            "type_attendu": entite.type_attendu.value,
            "statut": entite.statut.value,
            "demandee_le": entite.demandee_le,
            "attendue_pour": entite.attendue_pour,
            "bloquante": entite.bloquante,
        }

    def lire(self, identifiant: str) -> DemandePiece | None:
        return self._premier(
            self._requete().where(TableDemandePiece.identifiant == identifiant)
        )

    #: Le port nomme cette lecture `par_identifiant`. `lire` lui préexistait ici
    #: avec la même sémantique ; les deux noms cohabitent plutôt que d'imposer
    #: une reprise des appelants pour un synonyme.
    par_identifiant = lire

    def toutes(self, entreprise: str | None = None) -> list[DemandePiece]:
        requete = self._requete().order_by(TableDemandePiece.demandee_le)
        if entreprise is not None:
            requete = requete.where(TableDemandePiece.entreprise == entreprise)
        return self._tous(requete)

    def ouvertes(self, entreprise: str | None = None) -> list[DemandePiece]:
        requete = (
            self._requete()
            .where(TableDemandePiece.statut == StatutDemande.OUVERTE.value)
            .order_by(TableDemandePiece.demandee_le)
        )
        if entreprise is not None:
            requete = requete.where(TableDemandePiece.entreprise == entreprise)
        return self._tous(requete)

    def enregistrer(self, demande: DemandePiece) -> None:
        self._poser(demande)


# ⚠️ `MagasinFichiersSql` a été retiré. C'était un jalon — une classe qui levait
# `NotImplementedError` pour que l'absence soit nommée plutôt que découverte. Le
# raisonnement qu'elle portait reste valable et vaut d'être conservé :
#
# **un magasin de fichiers ne se réalise pas par une table.** Des documents
# numérisés font plusieurs mégaoctets par pièce et se conservent dix ans ; en
# base, ils feraient grossir les sauvegardes d'un facteur cent pour des données
# qui ne se requêtent jamais, et rendraient chaque restauration impraticable. Ce
# qui va en base est l'**empreinte**, déjà portée par la pièce.
#
# La réalisation est `magasin_local.MagasinFichiersLocal`, adressée par contenu.
# La cible reste un stockage objet compatible S3 ; le port ne changera pas.
