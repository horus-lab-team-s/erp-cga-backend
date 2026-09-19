"""L'événement qui relie l'encaissement à l'ouverture du tenant.

─────────────────────────────────────────────────────────────────────────────────
CE QUI MANQUAIT, ET QUI N'ÉTAIT PAS UNE PIÈCE

Le document de conception le disait déjà : « les sept étapes existent, la reprise
après incident aussi, la table et le répertoire aussi. **Ce qui manque est
l'événement qui les relie**, pas les pièces. »

Ce module est ce lien. Il s'abonne à `PaiementEncaissé`, démarre ou reprend la
saga d'ouverture, et persiste son avancement.

⚠️ IL EST IDEMPOTENT, ET C'EST NON NÉGOCIABLE

Le relais publie **au moins une fois**. Un rappel d'opérateur rejoué, une reprise
après incident, deux instances derrière un répartiteur : le même événement arrive
plusieurs fois, c'est le régime normal.

Sans cette garde, chaque arrivée ouvrirait un second préfixe de stockage et
enverrait un second lien d'activation. La garde n'est pas un test d'existence
suivi d'une création — entre les deux, l'autre a écrit — mais la **clé de
l'exécution**, contrainte en unique par la base : `saga` et `cle`.

CE QU'IL FAIT QUAND LA SAGA ÉCHOUE

Rien de plus que la consigner. Il **ne lève pas**, et c'est délibéré : lever
ferait échouer la publication, l'événement serait rejoué, la saga reprise, et
l'on tournerait ainsi jusqu'à la quarantaine. Or une saga en échec se reprend
déjà toute seule, avec son propre compteur et sa propre borne.

Deux mécanismes de reprise superposés sur le même incident produisent des
tentatives multipliées, pas une meilleure fiabilité.

L'exception est le cas où l'exécution demande un humain : là, l'appelant doit le
savoir, et `ResultatOuverture.demande_un_humain` le dit sans lever.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.tenants.application.ouverture import NOM_SAGA, saga_d_ouverture
from app.contextes.tenants.domaine.substitution import (
    ouverture_reellement_complete,
    substituees,
)
from app.orchestration.boite_d_envoi import EvenementSortant
from app.orchestration.saga import EtatSaga, Execution, avancer, demarrer

__all__ = [
    "CHAMPS_ATTENDUS",
    "ChargeIncomplete",
    "DepotDesExecutions",
    "ResultatOuverture",
    "abonner_l_ouverture",
    "ouvrir_sur_paiement",
]

#: Ce que la charge de l'événement doit porter pour qu'une ouverture soit
#: possible. Vérifié à l'entrée, parce qu'une charge incomplète découverte à la
#: quatrième étape laisserait un tenant à moitié fait.
CHAMPS_ATTENDUS = ("tenant", "slug")


class ChargeIncomplete(ValueError):
    """L'événement ne porte pas de quoi ouvrir un tenant."""


class DepotDesExecutions(Protocol):
    """Où l'avancement de la saga se persiste.

    ⚠️ `enregistrer` doit écrire **dans la transaction de l'appelant**, et
    l'unicité de `(saga, cle)` doit être tenue par la base. Une vérification
    préalable en code ne suffirait pas : entre la lecture et l'écriture, l'autre
    instance a démarré la même saga.
    """

    def trouver(self, saga: str, cle: str) -> Execution | None: ...

    def enregistrer(self, execution: Execution) -> None: ...


class ResultatOuverture(BaseModel):
    """Ce qu'une arrivée d'événement a produit."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    execution: Execution
    #: `False` quand l'événement était un rejeu sur une saga déjà terminée.
    a_progresse: bool

    @property
    def terminee(self) -> bool:
        return self.execution.etat is EtatSaga.TERMINEE

    @property
    def demande_un_humain(self) -> bool:
        return self.execution.demande_un_humain

    @property
    def tenant_utilisable(self) -> bool:
        """⚠️ **Pas la même question que « la saga est terminée ».**

        Une saga peut se terminer avec des étapes substituées faute
        d'infrastructure : le tenant existe, son sous-domaine répond, et il n'a
        ni schéma, ni stockage, ni compte administrateur. Annoncer au client un
        espace ouvert dans ce cas serait un mensonge que sa première connexion
        découvrirait.
        """
        return self.terminee and ouverture_reellement_complete(self.execution.contexte)

    @property
    def etapes_substituees(self) -> tuple[str, ...]:
        return substituees(self.execution.contexte)


def ouvrir_sur_paiement(
    evenement: EvenementSortant,
    *,
    provisionneur: Any,
    executions: DepotDesExecutions,
    a_l_instant: datetime,
) -> ResultatOuverture:
    """Démarre ou reprend la saga d'ouverture pour cet encaissement.

    ─────────────────────────────────────────────────────────────────────────
    LA CLÉ DE L'EXÉCUTION EST CELLE DE L'ÉVÉNEMENT

    C'est la référence du dossier commercial, pas l'identifiant de l'événement :
    deux événements distincts peuvent porter le même encaissement, un rappel
    d'opérateur et une reprise manuelle par exemple, et ils doivent tomber sur la
    **même** exécution.

    Prendre l'identifiant de l'événement produirait deux sagas pour un seul
    paiement, ce qui est exactement le double provisionnement que tout ce
    mécanisme existe pour empêcher.
    ─────────────────────────────────────────────────────────────────────────
    """
    manquants = [c for c in CHAMPS_ATTENDUS if not str(evenement.charge.get(c, "")).strip()]
    if manquants:
        raise ChargeIncomplete(
            f"l'événement {evenement.identifiant} ne porte pas {manquants}. "
            "Une charge incomplète découverte à la quatrième étape laisserait un "
            "tenant à moitié fait ; on refuse à l'entrée."
        )

    saga = saga_d_ouverture(provisionneur)
    existante = executions.trouver(NOM_SAGA, evenement.cle)
    if existante is not None and existante.achevee:
        return ResultatOuverture(execution=existante, a_progresse=False)

    depart = existante or demarrer(
        saga, evenement.cle, _contexte(evenement), a_l_instant
    )
    apres = avancer(saga, depart, a_l_instant)
    executions.enregistrer(apres)
    return ResultatOuverture(execution=apres, a_progresse=True)


def _contexte(evenement: EvenementSortant) -> Mapping[str, Any]:
    """La charge de l'événement, telle quelle.

    Recopiée sans filtrage : le provisionneur y lira ce dont il a besoin, et
    trier ici obligerait ce module à connaître les besoins de chaque étape.
    """
    return dict(evenement.charge)


def abonner_l_ouverture(
    abonnements: Any,
    *,
    provisionneur: Any,
    executions: DepotDesExecutions,
    horloge: Any,
    nom_evenement: str = "PaiementEncaissé",
) -> None:
    """Branche l'ouverture sur le relais.

    ⚠️ L'abonné **n'aboie pas** quand la saga échoue : voir l'en-tête. Deux
    mécanismes de reprise superposés sur le même incident produisent des
    tentatives multipliées, pas une meilleure fiabilité.

    Il lève en revanche sur une charge incomplète, parce que ce n'est pas un
    incident passager : rejouer n'ajoutera pas les champs manquants, et il faut
    que cela se voie.
    """

    def ouvrir_le_tenant(evenement: EvenementSortant) -> None:
        ouvrir_sur_paiement(
            evenement,
            provisionneur=provisionneur,
            executions=executions,
            a_l_instant=horloge(),
        )

    abonnements.abonner(nom_evenement, ouvrir_le_tenant)
