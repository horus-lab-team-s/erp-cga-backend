"""La souscription : du devis engagé à l'accès ouvert.

─────────────────────────────────────────────────────────────────────────────────
QUATRE ÉTATS, ET LA DISTINCTION ENTRE LES DEUX DU MILIEU EST TOUT LE SUJET

    EN_ATTENTE_PAIEMENT ──> PAYEE ──> ACTIVEE
             │                │
             └──> ABANDONNEE  └──> (l'activation a échoué : on reste PAYÉE)

**PAYEE** signifie que l'argent est arrivé. **ACTIVEE** signifie que le compte
existe et que le lien est parti. Les fondre en un seul état — « terminée » —
rendrait invisible le seul cas qui compte vraiment : **encaissé mais pas
activé**.

Ce cas se produit. Le service de courriel tombe, la création de compte échoue sur
une adresse déjà prise, le processus redémarre entre les deux écritures. Avec un
état unique, le client a payé et n'a rien ; personne ne le sait ; il rappelle
trois jours plus tard. Avec deux états, une requête suffit à lister les
souscriptions payées non activées, et c'est cette requête qu'on met sous
surveillance.

L'échec d'activation ne fait donc **jamais** retomber la souscription en arrière
et ne rejette **jamais** le paiement. Elle reste `PAYEE`, et le rattrapage est un
nouvel essai d'activation.

`activee_le` EST LA GARDE D'IDEMPOTENCE

Le prestataire renvoie plusieurs fois la même notification — son mécanisme de
reprise l'y pousse. Chaque rejeu retraverse le traitement. Ce qui empêche
d'ouvrir deux comptes et d'envoyer deux liens est cette date : elle est posée une
fois, et `activer()` refuse de la reposer.

Deux gardes valent mieux qu'une : le paiement se déclare déjà sans effet quand il
est rejoué. Celle-ci protège du cas où l'activation aurait échoué après la
validation du paiement, et où le rejeu la reprendrait — auquel cas il **faut**
qu'elle reprenne.

CE QUE LA SOUSCRIPTION NE FAIT PAS

Elle n'ouvre pas le compte elle-même. Elle dit qu'il est temps de le faire, et
enregistre que ce fut fait. La création d'identité appartient au contexte K, et
une entité de M qui saurait dériver un mot de passe serait une entité de trop.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.souscription.domaine.devis import Prospect
from app.contextes.souscription.domaine.offre import NatureService, Periodicite

__all__ = [
    "EtatSouscription",
    "Souscription",
    "TransitionRefusee",
]


class TransitionRefusee(ValueError):
    """Le passage demandé n'existe pas depuis l'état courant."""


class EtatSouscription(StrEnum):
    EN_ATTENTE_PAIEMENT = "EN_ATTENTE_PAIEMENT"
    #: L'argent est arrivé. Le compte n'existe pas encore.
    PAYEE = "PAYEE"
    #: Le compte existe et le lien est parti.
    ACTIVEE = "ACTIVEE"
    #: Le paiement a été refusé ou s'est perdu.
    ABANDONNEE = "ABANDONNEE"
    #: Fin de la relation, à l'initiative de l'une ou l'autre partie.
    RESILIEE = "RESILIEE"


class Souscription(BaseModel):
    """Un engagement pris, et ce qu'il en est advenu."""

    model_config = ConfigDict(frozen=True)

    reference: str = Field(min_length=1)
    devis: str = Field(min_length=1)
    prospect: Prospect

    service: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    nature: NatureService
    periodicite: Periodicite
    formule: str | None = None

    #: Ce qui est encaissé à la souscription. L'abonnement mensuel est annoncé à
    #: part — voir `Devis.montant_a_regler`.
    montant: Decimal = Field(gt=0)
    abonnement_mensuel: Decimal = Field(default=Decimal(0), ge=0)

    etat: EtatSouscription = EtatSouscription.EN_ATTENTE_PAIEMENT
    engagee_le: datetime
    payee_le: datetime | None = None
    activee_le: datetime | None = None
    close_le: datetime | None = None
    motif: str | None = None

    #: Le dossier auquel l'accès sera ouvert. `None` pour une prestation qui
    #: n'ouvre aucun dossier — une formation, par exemple.
    niu: str | None = None

    #: Renseigné à l'activation, par le contexte K.
    compte: str | None = None

    #: Date d'effet de la prestation. Pour un abonnement, le point de départ des
    #: échéances ; pour un service annuel, la date anniversaire.
    prend_effet_le: date | None = None

    # ── Ce que la souscription sait dire d'elle-même ────────────────────────

    @computed_field
    @property
    def encaissee(self) -> bool:
        return self.etat in (EtatSouscription.PAYEE, EtatSouscription.ACTIVEE)

    @computed_field
    @property
    def a_activer(self) -> bool:
        """Payée et non activée : **le seul cas qui compte vraiment**.

        Sérialisé, et c'est délibéré : c'est la ligne d'un tableau de bord, pas
        un détail interne. Un client qui a payé et n'a rien reçu doit apparaître
        quelque part sans qu'on ait à le chercher.
        """
        return self.etat == EtatSouscription.PAYEE

    @computed_field
    @property
    def ouvre_un_acces(self) -> bool:
        return self.niu is not None

    # ── Transitions ─────────────────────────────────────────────────────────

    def encaisser(self, a_l_instant: datetime) -> Souscription:
        """Le paiement est confirmé. **Rejouable** : une souscription déjà payée
        ou activée se rend inchangée."""
        if self.etat in (EtatSouscription.PAYEE, EtatSouscription.ACTIVEE):
            return self
        if self.etat != EtatSouscription.EN_ATTENTE_PAIEMENT:
            raise TransitionRefusee(
                f"souscription {self.reference} à l'état {self.etat} : un encaissement "
                "ne la rouvre pas. Un règlement arrivé après un abandon doit être traité "
                "à la main."
            )
        return self.model_copy(
            update={"etat": EtatSouscription.PAYEE, "payee_le": a_l_instant}
        )

    def activer(
        self, a_l_instant: datetime, *, compte: str, prend_effet_le: date
    ) -> Souscription:
        """Le compte existe et le lien est parti.

        Refuse d'activer deux fois — c'est la garde d'idempotence, voir l'en-tête.
        Refuse aussi d'activer une souscription non payée : le lien ne part que
        sur un encaissement réellement confirmé.
        """
        if self.activee_le is not None:
            raise TransitionRefusee(
                f"souscription {self.reference} déjà activée le {self.activee_le} "
                f"(compte {self.compte}). L'activer de nouveau ouvrirait un second "
                "compte et enverrait un second lien."
            )
        if self.etat != EtatSouscription.PAYEE:
            raise TransitionRefusee(
                f"souscription {self.reference} à l'état {self.etat} : le lien "
                "d'activation ne part que sur un paiement confirmé."
            )
        return self.model_copy(
            update={
                "etat": EtatSouscription.ACTIVEE,
                "activee_le": a_l_instant,
                "compte": compte,
                "prend_effet_le": prend_effet_le,
            }
        )

    def abandonner(self, a_l_instant: datetime, motif: str) -> Souscription:
        """Le paiement a été refusé ou s'est perdu. Rejouable."""
        if self.etat == EtatSouscription.ABANDONNEE:
            return self
        if self.etat != EtatSouscription.EN_ATTENTE_PAIEMENT:
            raise TransitionRefusee(
                f"souscription {self.reference} à l'état {self.etat} : on n'abandonne "
                "pas ce qui a été encaissé. Un remboursement est un autre geste, et il "
                "laisse sa propre trace."
            )
        return self.model_copy(
            update={
                "etat": EtatSouscription.ABANDONNEE,
                "close_le": a_l_instant,
                "motif": motif,
            }
        )

    def resilier(self, a_l_instant: datetime, motif: str) -> Souscription:
        if self.etat != EtatSouscription.ACTIVEE:
            raise TransitionRefusee(
                f"souscription {self.reference} à l'état {self.etat} : seule une "
                "souscription active se résilie."
            )
        return self.model_copy(
            update={
                "etat": EtatSouscription.RESILIEE,
                "close_le": a_l_instant,
                "motif": motif,
            }
        )
