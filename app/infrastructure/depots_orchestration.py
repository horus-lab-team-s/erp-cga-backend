"""Les dépôts du socle d'orchestration, en mémoire et en base.

⚠️ **`deposer` ne valide jamais la transaction**, et c'est tout l'intérêt de la
boîte d'envoi. La validation appartient à l'unité de travail qui porte aussi le
fait métier ; valider ici rendrait l'atomicité illusoire tout en la laissant croire
acquise, ce qui est pire que de ne rien faire.

Le `flush` que `DepotDocument` émet n'est pas un `commit` : il pousse l'écriture
vers la base dans la transaction courante, sans la refermer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.infrastructure.depot_document import DepotDocument
from app.infrastructure.tables_orchestration import (
    TableEvenementSortant,
    TableExecutionSaga,
    TablePassageOrdonnance,
)
from app.orchestration.boite_d_envoi import EvenementSortant
from app.orchestration.ordonnanceur import Passage
from app.orchestration.saga import Execution
from app.partage.depot_memoire import EntrepotMemoire

__all__ = [
    "BoiteDEnvoiMemoire",
    "BoiteDEnvoiSql",
    "DepotExecutionsMemoire",
    "DepotExecutionsSql",
    "ExecutionIntrouvable",
]

LOCATAIRE_PAR_DEFAUT = "CGA-BRCG"


class ExecutionIntrouvable(LookupError):
    """Aucune exécution pour cette saga et cette clé."""


def _cle_saga(saga: str, cle: str) -> str:
    """L'identifiant d'une exécution, dérivé et non tiré au sort.

    Deux appels concurrents sur la même saga et la même clé doivent produire le
    **même** identifiant, sinon la contrainte d'unicité serait le seul rempart et
    l'un des deux échouerait bruyamment là où il devrait simplement retrouver
    l'exécution en cours.
    """
    return f"{saga}:{cle}"


# ── En mémoire ───────────────────────────────────────────────────────────────


class BoiteDEnvoiMemoire:
    """Réalisation de `BoiteDEnvoi`.

    ⚠️ Elle ne tient **aucune** des garanties qui font l'intérêt du modèle :
    l'atomicité avec le fait métier suppose une transaction, et il n'y en a pas
    ici. Elle sert à exercer le parcours en développement, jamais à valider le
    mécanisme.
    """

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[EvenementSortant] = EntrepotMemoire(
            locataire, cle=lambda e: e.identifiant
        )

    def deposer(self, evenement: EvenementSortant) -> None:
        self._entrepot.poser(evenement)

    def enregistrer(self, evenement: EvenementSortant) -> None:
        self._entrepot.poser(evenement)

    def a_publier(self, limite: int = 100) -> list[EvenementSortant]:
        attente = self._entrepot.filtrer(lambda e: e.en_attente)
        return sorted(attente, key=lambda e: (e.cree_le or datetime.min, e.identifiant))[
            :limite
        ]

    def en_quarantaine(self) -> list[EvenementSortant]:
        return sorted(
            self._entrepot.filtrer(lambda e: e.en_quarantaine),
            key=lambda e: e.cree_le or datetime.min,
        )

    def tous(self) -> list[EvenementSortant]:
        return sorted(self._entrepot.tout(), key=lambda e: e.cree_le or datetime.min)


class DepotExecutionsMemoire:
    """Réalisation du dépôt d'exécutions de saga."""

    def __init__(self, locataire: str = LOCATAIRE_PAR_DEFAUT) -> None:
        self.locataire = locataire
        self._entrepot: EntrepotMemoire[Execution] = EntrepotMemoire(
            locataire, cle=lambda e: _cle_saga(e.saga, e.cle)
        )

    def enregistrer(self, execution: Execution) -> None:
        self._entrepot.poser(execution)

    def lire(self, saga: str, cle: str) -> Execution:
        execution = self._entrepot.prendre(_cle_saga(saga, cle))
        if execution is None:
            raise ExecutionIntrouvable(f"aucune exécution « {saga} » pour « {cle} »")
        return execution

    def trouver(self, saga: str, cle: str) -> Execution | None:
        return self._entrepot.prendre(_cle_saga(saga, cle))

    def par_etat(self, *etats: str) -> list[Execution]:
        vises = {str(e) for e in etats}
        return sorted(
            self._entrepot.filtrer(lambda e: str(e.etat) in vises),
            key=lambda e: (e.demarree_le or datetime.min, e.cle),
        )


# ── En base ──────────────────────────────────────────────────────────────────


class BoiteDEnvoiSql(DepotDocument[EvenementSortant]):
    """Réalisation SQL. Voir l'en-tête : `deposer` ne valide pas la transaction."""

    _table = TableEvenementSortant
    _entite = EvenementSortant

    def _cle(self, entite: EvenementSortant) -> dict[str, Any]:
        return {"identifiant": entite.identifiant}

    def _colonnes(self, entite: EvenementSortant) -> dict[str, Any]:
        return {
            "nom": entite.nom,
            "cle": entite.cle,
            "cree_le": entite.cree_le,
            "publie_le": entite.publie_le,
            "tentatives": entite.tentatives,
            "en_quarantaine": entite.en_quarantaine,
            "dernier_echec": entite.dernier_echec,
        }

    def deposer(self, evenement: EvenementSortant) -> None:
        self._poser(evenement)

    def enregistrer(self, evenement: EvenementSortant) -> None:
        self._poser(evenement)

    def a_publier(self, limite: int = 100) -> list[EvenementSortant]:
        """Du plus ancien au plus récent, et la borne n'est pas cosmétique.

        Sans elle, un arriéré de cent mille événements serait chargé d'un coup à
        chaque passage. La publication traite un lot, valide, et repasse : c'est
        ce qui lui permet d'avancer même quand elle est très en retard.
        """
        requete = (
            self._requete()
            .where(TableEvenementSortant.publie_le.is_(None))
            .where(TableEvenementSortant.en_quarantaine.is_(False))
            .order_by(TableEvenementSortant.cree_le, TableEvenementSortant.identifiant)
            .limit(limite)
        )
        return self._tous(requete)

    def en_quarantaine(self) -> list[EvenementSortant]:
        return self._tous(
            self._requete()
            .where(TableEvenementSortant.en_quarantaine.is_(True))
            .order_by(TableEvenementSortant.cree_le)
        )

    def tous(self) -> list[EvenementSortant]:
        return self._tous(self._requete().order_by(TableEvenementSortant.cree_le))


class DepotExecutionsSql(DepotDocument[Execution]):
    """Réalisation SQL du dépôt d'exécutions.

    ⚠️ L'identifiant est **dérivé** de la saga et de la clé, jamais tiré au sort.
    Deux appels concurrents produisent ainsi la même ligne, et la contrainte
    d'unicité n'a plus qu'à confirmer ce que la clé primaire dit déjà.
    """

    _table = TableExecutionSaga
    _entite = Execution

    def _cle(self, entite: Execution) -> dict[str, Any]:
        return {"identifiant": _cle_saga(entite.saga, entite.cle)}

    def _colonnes(self, entite: Execution) -> dict[str, Any]:
        return {
            "saga": entite.saga,
            "cle": entite.cle,
            "etat": str(entite.etat),
            "tentatives": entite.tentatives,
            "demarree_le": entite.demarree_le,
            "terminee_le": entite.terminee_le,
            "dernier_echec": entite.dernier_echec,
        }

    def enregistrer(self, execution: Execution) -> None:
        self._poser(execution)

    def trouver(self, saga: str, cle: str) -> Execution | None:
        return self._premier(
            self._requete().where(
                TableExecutionSaga.identifiant == _cle_saga(saga, cle)
            )
        )

    def lire(self, saga: str, cle: str) -> Execution:
        execution = self.trouver(saga, cle)
        if execution is None:
            raise ExecutionIntrouvable(f"aucune exécution « {saga} » pour « {cle} »")
        return execution

    def par_etat(self, *etats: str) -> list[Execution]:
        """Les deux questions d'exploitation : « qu'est-ce qui est en cours ? » et
        « qu'est-ce qui attend un humain ? »."""
        return self._tous(
            self._requete()
            .where(TableExecutionSaga.etat.in_([str(e) for e in etats]))
            .order_by(TableExecutionSaga.demarree_le, TableExecutionSaga.cle)
        )


# ── Les passages de l'ordonnanceur ───────────────────────────────────────────
#
# ⚠️ Ces deux dépôts ne sont **pas** cloisonnés, contrairement à tous les autres de
# ce module. Un travail périodique n'appartient à aucun cabinet : le relais vide la
# boîte de tout le monde. Voir `TablePassageOrdonnance`.
#
# La conséquence pratique est qu'ils ne prennent pas de locataire en argument et ne
# passent pas par `EntrepotMemoire`, qui en réclame un. Écrire un dépôt plus court
# à la main coûte moins que tordre l'entrepôt pour qu'il accepte l'absence de
# cloison, et surtout : la différence se lit.


class DepotPassagesMemoire:
    """Les passages en mémoire. Perdus au redémarrage, ce qui est le défaut à corriger.

    ⚠️ Employé en persistance mémoire et dans les tests. Il rend exactement le
    service que la table existe pour ne **pas** rendre : oublier. C'est acceptable
    ici parce qu'une installation en mémoire n'a rien à relancer et personne à
    prévenir, et c'est écrit pour qu'on ne le prenne pas pour la version durable.
    """

    def __init__(self) -> None:
        self._passages: dict[str, Passage] = {}

    def enregistrer(self, passage: Passage) -> None:
        self._passages[passage.travail] = passage

    def tous(self) -> dict[str, Passage]:
        # Une copie : l'appelant reçoit un état, pas une vue vivante sur le dépôt.
        # Sans elle, un passage écrit pendant un tour changerait le dictionnaire
        # que le tour est en train de parcourir.
        return dict(self._passages)


class DepotPassagesSql:
    """Les passages en base. Une ligne par travail, jamais un historique.

    ⚠️ **L'écriture est un `merge`, pas un `add`.** Un travail écrit son passage à
    chaque tour, et la ligne existe déjà dès le second : un `add` lèverait sur la
    clé primaire, à chaque tour sauf le premier. C'est le genre de défaut qui passe
    la revue et tombe en exploitation deux minutes après le démarrage.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    def enregistrer(self, passage: Passage) -> None:
        self._session.merge(
            TablePassageOrdonnance(
                travail=passage.travail,
                debute_le=passage.debute_le,
                termine_le=passage.termine_le,
                echecs_consecutifs=passage.echecs_consecutifs,
                dernier_echec=passage.dernier_echec,
            )
        )

    def tous(self) -> dict[str, Passage]:
        from sqlalchemy import select

        return {
            ligne.travail: Passage(
                travail=ligne.travail,
                debute_le=ligne.debute_le,
                termine_le=ligne.termine_le,
                echecs_consecutifs=ligne.echecs_consecutifs,
                dernier_echec=ligne.dernier_echec,
            )
            for ligne in self._session.scalars(select(TablePassageOrdonnance))
        }
