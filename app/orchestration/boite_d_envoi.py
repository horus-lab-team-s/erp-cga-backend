"""La boîte d'envoi : publier un événement sans jamais mentir.

─────────────────────────────────────────────────────────────────────────────────
LE PROBLÈME QU'ELLE RÉSOUT, ET QUI N'EST PAS ÉVIDENT

Une étape écrit en base **et** publie un événement. Ce sont deux systèmes, et il
n'y a pas de transaction commune. Deux ordres possibles, deux défauts :

    écrire puis publier   la panne entre les deux perd l'événement.
                          Le paiement est encaissé, le tenant ne s'ouvre jamais,
                          et rien ne le signale.

    publier puis écrire   la panne entre les deux publie un événement pour un
                          fait qui n'a pas eu lieu. Un tenant s'ouvre pour un
                          paiement annulé.

**Aucun des deux n'est acceptable**, et aucune quantité de soin ne les répare :
c'est un problème de structure, pas d'attention.

LA SOLUTION

L'événement est écrit **en base, dans la même transaction que le fait**. Il ne
part pas encore : il attend dans une table. Un passage séparé le publie ensuite,
et le marque publié.

Le fait et l'intention de publier sont donc atomiques. La publication, elle, est
« au moins une fois » : elle peut être rejouée, et c'est pourquoi tout
consommateur doit être idempotent.

⚠️ **AU MOINS UNE FOIS, JAMAIS EXACTEMENT UNE FOIS**

« Exactement une fois » n'existe pas entre deux systèmes qui peuvent tomber. Ce
qui existe, c'est « au moins une fois » du côté de l'émetteur, et l'idempotence du
côté du consommateur. Prétendre l'inverse conduit à écrire des consommateurs qui
double-comptent le jour d'une reprise.

C'est la raison pour laquelle les sept étapes d'ouverture d'un tenant sont
idempotentes, et pourquoi leur docstring le dit.

CE QUE CE MODULE EST, ET N'EST PAS

Un modèle et un port. Pas une table, pas un client de bus, pas une tâche
d'arrière-plan : ce paquet est un mécanisme pur, et l'infrastructure est ailleurs.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TENTATIVES_AVANT_QUARANTAINE",
    "BoiteDEnvoi",
    "EvenementSortant",
    "RemiseRefusee",
    "deposer",
]

#: Au-delà, l'événement est mis de côté plutôt que rejoué indéfiniment. Un
#: événement qu'aucun consommateur n'accepte sature la file et retarde tous les
#: autres — dont ceux qui, eux, seraient traités.
TENTATIVES_AVANT_QUARANTAINE = 10


class RemiseRefusee(ValueError):
    """On ne remet en circulation qu'un événement en quarantaine (pas 84)."""


class EvenementSortant(BaseModel):
    """Un fait à publier, en attente dans la même base que le fait lui-même.

    Un modèle Pydantic pour la même raison que `Execution` : c'est de la donnée
    pure, et elle se range en base comme toutes les entités du dépôt.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str
    #: Le nom métier, tel que le document de conception l'écrit : `PaiementEncaissé`,
    #: `TenantOuvert`. Pas un nom technique : c'est lui qui figurera dans les
    #: journaux qu'un exploitant lit à trois heures du matin.
    nom: str
    #: Ce sur quoi porte l'événement. Sert au partitionnement et à l'ordre : deux
    #: événements de la même clé doivent être traités dans l'ordre d'émission.
    cle: str
    charge: dict[str, Any] = Field(default_factory=dict)
    cree_le: datetime | None = None
    publie_le: datetime | None = None
    tentatives: int = 0
    dernier_echec: str | None = None
    #: Mis de côté après trop d'échecs. Voir `TENTATIVES_AVANT_QUARANTAINE`.
    en_quarantaine: bool = False

    @property
    def en_attente(self) -> bool:
        return self.publie_le is None and not self.en_quarantaine

    def publie(self, a_l_instant: datetime) -> EvenementSortant:
        """Marque publié. **Rejouable** : une seconde confirmation ne repousse pas
        la date, qui sert à mesurer le délai de bout en bout."""
        if self.publie_le is not None:
            return self
        return self.model_copy(
            update={"publie_le": a_l_instant, "dernier_echec": None}
        )

    def remettre_en_circulation(self) -> EvenementSortant:
        """Rend un événement en quarantaine au relais, après correction de sa cause (pas 84).

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ ANNONCÉ, JAMAIS ÉCRIT

        `echoue` dit de la quarantaine qu'elle laisse l'événement « lisible et
        rejouable à la main ». Rien ne le rejouait : un tenant payé jamais ouvert
        restait en quarantaine jusqu'à une modification de la base.

        POURQUOI LE REJEU EST SANS DANGER

        Le relais remet un événement en entier, et tout abonné doit être idempotent
        (voir `_remettre` dans `relais.py`) : un abonné déjà servi le recevra une
        seconde fois sans effet.

        Les tentatives reviennent à zéro, sans quoi le premier nouvel échec le
        renverrait aussitôt en quarantaine. Le dernier échec est gardé : c'est ce que
        l'exploitant a corrigé, et la publication réussie l'effacera.

        ⚠️ Refusée sur un événement qui n'est pas en quarantaine : un événement publié
        ne se republie pas, et un événement en attente est déjà dans la file.
        ─────────────────────────────────────────────────────────────────────────
        """
        if not self.en_quarantaine:
            raison = (
                "il a été publié." if self.publie_le is not None else "il est déjà dans la file."
            )
            raise RemiseRefusee(
                f"l'événement {self.identifiant} n'est pas en quarantaine : {raison}"
            )
        return self.model_copy(update={"en_quarantaine": False, "tentatives": 0})

    def echoue(
        self, motif: str, *, seuil: int = TENTATIVES_AVANT_QUARANTAINE
    ) -> EvenementSortant:
        """Compte l'échec, et met de côté au-delà du seuil.

        La quarantaine n'est pas une suppression : l'événement reste lisible et
        rejouable à la main. Le supprimer effacerait la trace d'un fait qui a bien
        eu lieu, et l'on chercherait longtemps pourquoi un tenant payé n'a jamais
        été ouvert.
        """
        tentatives = self.tentatives + 1
        return self.model_copy(
            update={
                "tentatives": tentatives,
                "dernier_echec": motif,
                "en_quarantaine": tentatives >= seuil,
            }
        )


class BoiteDEnvoi(Protocol):
    """Le port. La réalisation SQL écrit dans la transaction de l'appelant.

    ⚠️ `deposer` ne valide **jamais** la transaction. C'est tout l'intérêt : la
    validation appartient à l'unité de travail qui porte aussi le fait métier. Une
    réalisation qui validerait ici rendrait l'atomicité illusoire tout en la
    laissant croire acquise.
    """

    def deposer(self, evenement: EvenementSortant) -> None: ...

    def a_publier(self, limite: int = 100) -> list[EvenementSortant]:
        """Les événements en attente, **du plus ancien au plus récent**.

        L'ordre n'est pas un confort : deux événements de la même clé doivent
        être traités dans l'ordre d'émission. Un `TenantOuvert` traité avant le
        `PaiementEncaissé` qui l'a causé produirait un tenant sans justification.
        """
        ...

    def enregistrer(self, evenement: EvenementSortant) -> None:
        """Réécrit un événement après publication ou échec."""
        ...

    def en_quarantaine(self) -> list[EvenementSortant]:
        """Ce qu'aucun consommateur n'a accepté. À regarder tous les matins."""
        ...


def deposer(
    boite: BoiteDEnvoi,
    identifiant: str,
    nom: str,
    cle: str,
    charge: Mapping[str, Any],
    a_l_instant: datetime,
) -> EvenementSortant:
    """Range un événement dans la boîte. À appeler **dans** la transaction du fait."""
    evenement = EvenementSortant(
        identifiant=identifiant,
        nom=nom,
        cle=cle,
        charge=dict(charge),
        cree_le=a_l_instant,
    )
    boite.deposer(evenement)
    return evenement
