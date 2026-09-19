"""La saga : des étapes ordonnées, compensables, et reprises où elles se sont arrêtées.

Voir l'en-tête du paquet pour **quand** employer une saga, et surtout quand ne pas
en employer.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE MOTEUR GARANTIT

    reprise            une exécution repart après la dernière étape franchie
    idempotence        rejouer une exécution terminée ne refait rien
    ordre              les étapes se franchissent dans l'ordre, sans saut
    compensation       à rebours, et seulement sur ce qui a été franchi
    borne              au-delà de N tentatives, un humain est appelé

CE QU'IL N'ATTEND PAS DES ÉTAPES, ET CE QU'IL EXIGE

Il n'attend pas qu'elles réussissent. Il exige en revanche qu'elles soient
**idempotentes** : rejouée, une étape doit constater que son travail est fait et
rendre le même résultat.

C'est la condition de tout le reste. Le moteur enregistre l'avancement **après**
l'effet, et une panne entre les deux fait rejouer l'étape. Sans idempotence, ce
rejeu crée un second enregistrement DNS, envoie un second courriel, ouvre un
second compte.

L'ordre inverse ne serait pas meilleur : enregistrer avant d'agir ferait sauter
une étape jamais faite, ce qui est pire, parce que rien ne le dit.

⚠️ UNE ÉTAPE À EFFET EXTERNE DOIT DÉCLARER CE QU'ON EN FAIT

Soit elle porte une compensation, soit elle se déclare **irréversible** avec sa
raison. Ce qu'elle ne peut pas faire, c'est se taire.

C'est le défaut classique des sagas : on écrit six compensations, on oublie la
septième, et l'on découvre à l'abandon qu'un préfixe de stockage reste facturé
pour un client qui n'existe plus. L'oubli ne se voit jamais au moment où on le
commet, puisque le chemin nominal fonctionne parfaitement.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CLE_MOTIF",
    "TENTATIVES_MAXIMALES",
    "DefinitionInvalide",
    "Etape",
    "EtatSaga",
    "Execution",
    "Saga",
    "abandonner",
    "avancer",
    "demarrer",
]

#: La clé sous laquelle le motif d'abandon est passé aux compensations.
CLE_MOTIF = "motif"

#: Au-delà, l'exécution passe en échec et attend un humain. Ce n'est pas une
#: valeur métier : c'est la borne qui distingue « le service est lent » de « ce
#: provisionnement ne passera jamais ». Une boucle sans borne ne se voit pas, elle
#: se découvre sur la facture du prestataire ou dans un journal saturé.
TENTATIVES_MAXIMALES = 5

#: Le contexte transporté d'étape en étape. Opaque au moteur : il le passe et le
#: conserve, il ne le lit jamais.
Contexte = Mapping[str, Any]


class DefinitionInvalide(ValueError):
    """La saga est mal déclarée. Levée à la construction, jamais à l'exécution."""


class EtatSaga(StrEnum):
    EN_COURS = "EN_COURS"
    TERMINEE = "TERMINEE"
    #: Abandon demandé, compensations en cours à rebours.
    EN_COMPENSATION = "EN_COMPENSATION"
    COMPENSEE = "COMPENSEE"
    #: Une compensation au moins a échoué. **Il reste des effets externes
    #: pendants**, et c'est l'état le plus important du lot : il ne se résout pas
    #: tout seul et doit produire une alerte nominative.
    COMPENSATION_INCOMPLETE = "COMPENSATION_INCOMPLETE"
    #: Trop de tentatives. L'exécution attend un humain ; elle n'est pas perdue et
    #: se reprend par `avancer` une fois la cause levée.
    ECHOUEE = "ECHOUEE"


@dataclass(frozen=True)
class Etape:
    """Une étape, son effet, et ce qui le défait.

    `action` reçoit le contexte et rend le contexte enrichi. Elle doit être
    **idempotente** : voir l'en-tête.

    `compensation` défait l'effet. Elle doit l'être aussi, et pour la même raison.
    """

    nom: str
    action: Callable[[Contexte], Contexte]
    compensation: Callable[[Contexte], Contexte] | None = None

    #: L'étape produit-elle un effet **hors base** ? C'est ce qui justifie la
    #: saga. Une étape purement transactionnelle n'en a pas besoin : le
    #: `ROLLBACK` la défait.
    externe: bool = True

    #: Déclarer une étape externe irréversible est permis, à condition de dire
    #: pourquoi. Se taire ne l'est pas.
    irreversible: str = ""

    def __post_init__(self) -> None:
        if not self.nom.strip():
            raise DefinitionInvalide("une étape sans nom ne se retrouve pas au journal")
        if not self.externe:
            return
        if self.compensation is None and not self.irreversible.strip():
            raise DefinitionInvalide(
                f"étape « {self.nom} » : effet externe sans compensation ni raison "
                "d'irréversibilité. C'est le défaut classique des sagas — on écrit "
                "six compensations, on oublie la septième, et l'on découvre à "
                "l'abandon qu'un effet reste pendant. Déclarer "
                "`irreversible=\"…\"` est une réponse acceptable ; se taire non."
            )
        if self.compensation is not None and self.irreversible.strip():
            raise DefinitionInvalide(
                f"étape « {self.nom} » : à la fois compensable et déclarée "
                "irréversible. L'une des deux affirmations est fausse, et on ne "
                "sait pas laquelle."
            )


@dataclass(frozen=True)
class Saga:
    """La définition : un nom, des étapes, dans l'ordre où elles se franchissent."""

    nom: str
    etapes: tuple[Etape, ...]

    def __post_init__(self) -> None:
        if not self.etapes:
            raise DefinitionInvalide(f"saga « {self.nom} » sans étape")
        noms = [e.nom for e in self.etapes]
        doublons = sorted({n for n in noms if noms.count(n) > 1})
        if doublons:
            raise DefinitionInvalide(
                f"saga « {self.nom} » : étapes du même nom {doublons}. L'avancement "
                "est repéré par le nom ; deux étapes homonymes rendraient la "
                "reprise indécidable."
            )

    def etape(self, nom: str) -> Etape | None:
        return next((e for e in self.etapes if e.nom == nom), None)

    @property
    def noms(self) -> tuple[str, ...]:
        return tuple(e.nom for e in self.etapes)


class Execution(BaseModel):
    """L'état d'une saga en cours, tel qu'on le persiste.

    Un modèle Pydantic et non une classe de données, contrairement à `Etape` et
    `Saga` : celles-ci portent des fonctions et ne se sérialisent pas, celle-ci
    est de la donnée pure et se range en base comme toutes les entités du dépôt.
    Mélanger deux stratégies de sérialisation dans un même système coûte plus tard
    ce qu'il économise maintenant.

    ⚠️ **Il doit être écrit dans la même transaction que l'effet de l'étape**
    quand cet effet est en base, et juste après quand il ne l'est pas. C'est
    l'appelant qui en répond : le moteur est pur et ne connaît aucune base.
    """

    model_config = ConfigDict(frozen=True)

    saga: str
    #: Ce sur quoi porte l'exécution — un slug de tenant, une référence de
    #: dossier. Sert de clé d'unicité : deux exécutions concurrentes de la même
    #: saga sur la même clé produiraient l'effet deux fois.
    cle: str
    etat: EtatSaga = EtatSaga.EN_COURS
    #: Les étapes déjà réussies, dans l'ordre.
    franchies: tuple[str, ...] = ()
    #: Les compensations déjà passées, dans l'ordre où elles ont été jouées.
    compensees: tuple[str, ...] = ()
    #: Celles qui ont échoué à compenser. Non vide = `COMPENSATION_INCOMPLETE`.
    compensations_en_echec: tuple[str, ...] = ()
    tentatives: int = 0
    dernier_echec: str | None = None
    contexte: dict[str, Any] = Field(default_factory=dict)
    demarree_le: datetime | None = None
    terminee_le: datetime | None = None

    @property
    def achevee(self) -> bool:
        return self.etat in (
            EtatSaga.TERMINEE,
            EtatSaga.COMPENSEE,
            EtatSaga.COMPENSATION_INCOMPLETE,
        )

    @property
    def demande_un_humain(self) -> bool:
        """Les deux états qui ne se résolvent pas tout seuls.

        `ECHOUEE` attend qu'on lève la cause ; `COMPENSATION_INCOMPLETE` laisse
        des effets externes pendants. L'une et l'autre doivent produire une
        alerte nominative, pas une ligne dans un journal que personne ne lit.
        """
        return self.etat in (EtatSaga.ECHOUEE, EtatSaga.COMPENSATION_INCOMPLETE)

    def prochaine(self, saga: Saga) -> str | None:
        """Le nom de l'étape à franchir, ou `None` si tout l'est.

        Repérée par **ce qui reste**, jamais par un index : un index survivrait
        mal à l'insertion d'une étape dans une définition dont des exécutions
        sont en cours, et se décalerait silencieusement.
        """
        for nom in saga.noms:
            if nom not in self.franchies:
                return nom
        return None


def demarrer(saga: Saga, cle: str, contexte: Contexte, a_l_instant: datetime) -> Execution:
    """Une exécution neuve, rien de franchi."""
    return Execution(
        saga=saga.nom, cle=cle, contexte=dict(contexte), demarree_le=a_l_instant
    )


def avancer(
    saga: Saga,
    execution: Execution,
    a_l_instant: datetime,
    *,
    tentatives_maximales: int = TENTATIVES_MAXIMALES,
) -> Execution:
    """Franchit les étapes restantes, et s'arrête à la première qui échoue.

    ─────────────────────────────────────────────────────────────────────────
    REPRISE EN AVANT

    Un échec **ne compense rien**. Il enregistre le motif, incrémente le compteur
    et laisse l'exécution reprenable. C'est ce qu'il faut pour un
    provisionnement, où l'échec est presque toujours passager. Compenser sur un
    échec technique détruirait un tenant à moitié créé pour une coupure de trois
    secondes.

    L'abandon est un geste **demandé**, et il s'appelle `abandonner`.

    IDEMPOTENT SUR UNE EXÉCUTION ACHEVÉE

    Rejouer une exécution terminée la rend inchangée. Les tâches d'arrière-plan
    se rejouent, c'est leur mécanisme de reprise ; sans cette garde, chaque rejeu
    relancerait la dernière étape.

    ⚠️ UNE EXÉCUTION `ECHOUEE` SE REPREND

    `avancer` sur elle repart, avec le compteur remis à zéro : si un humain la
    relance, c'est qu'il a levé la cause. La laisser bloquée obligerait à la
    recréer, donc à perdre ce qu'elle avait déjà franchi.
    ─────────────────────────────────────────────────────────────────────────
    """
    if execution.achevee or execution.etat is EtatSaga.EN_COMPENSATION:
        return execution

    courante = (
        execution.model_copy(
            update={"etat": EtatSaga.EN_COURS, "tentatives": 0, "dernier_echec": None}
        )
        if execution.etat is EtatSaga.ECHOUEE
        else execution
    )

    while True:
        nom = courante.prochaine(saga)
        if nom is None:
            return courante.model_copy(
                update={"etat": EtatSaga.TERMINEE, "terminee_le": a_l_instant}
            )

        etape = saga.etape(nom)
        try:
            contexte = dict(etape.action(courante.contexte))
        except Exception as echec:  # noqa: BLE001 — on veut le motif, quel qu'il soit
            tentatives = courante.tentatives + 1
            depassee = tentatives >= tentatives_maximales
            return courante.model_copy(
                update={
                    "etat": EtatSaga.ECHOUEE if depassee else EtatSaga.EN_COURS,
                    "tentatives": tentatives,
                    "dernier_echec": f"{nom} : {echec}",
                }
            )

        courante = courante.model_copy(
            update={
                "franchies": (*courante.franchies, nom),
                "contexte": contexte,
                "tentatives": 0,
                "dernier_echec": None,
            }
        )


def abandonner(
    saga: Saga, execution: Execution, a_l_instant: datetime, *, motif: str
) -> Execution:
    """Défait ce qui a été fait, à rebours, et seulement cela.

    ─────────────────────────────────────────────────────────────────────────
    TROIS RÈGLES, ET CHACUNE ÉVITE UN DÉGÂT PRÉCIS

    **À rebours.** Défaire dans l'ordre de création laisserait des dépendances :
    supprimer un tenant avant son compte administrateur laisse le compte
    orphelin, rattaché à rien.

    **Seulement ce qui a été franchi.** Compenser une étape jamais jouée est le
    second défaut classique : la compensation d'un stockage jamais ouvert
    supprime, dans le meilleur des cas, un préfixe qui n'existe pas — dans le
    pire, celui d'un autre.

    **Une compensation qui échoue n'arrête pas les autres.** Défaire ce qu'on
    peut vaut mieux que s'arrêter au premier obstacle. Ce qui n'a pas pu l'être
    est nommé, et l'exécution passe en `COMPENSATION_INCOMPLETE` : un état qui ne
    se résout pas tout seul et doit produire une alerte nominative.

    LES ÉTAPES IRRÉVERSIBLES SONT COMPTÉES POUR CE QU'ELLES SONT

    Elles ne sont pas silencieusement passées : leur nom figure dans
    `compensations_en_echec`, avec sa raison. Un courriel envoyé ne se rappelle
    pas, et l'exploitation doit le savoir plutôt que de le supposer.
    ─────────────────────────────────────────────────────────────────────────
    """
    if execution.etat in (EtatSaga.COMPENSEE, EtatSaga.COMPENSATION_INCOMPLETE):
        return execution

    # Le motif entre dans le contexte avant les compensations, et il le faut :
    # une compensation a besoin de savoir **pourquoi** elle défait. Un tenant
    # résilié porte un motif que le client lira ; « annulé » ne lui apprendrait
    # rien, et l'exploitation ne saurait pas distinguer une rétractation d'un
    # paiement contesté.
    contexte = {**execution.contexte, CLE_MOTIF: motif}
    compensees: list[str] = list(execution.compensees)
    en_echec: list[str] = list(execution.compensations_en_echec)

    for nom in reversed(execution.franchies):
        if nom in compensees or any(e.startswith(f"{nom} :") for e in en_echec):
            continue
        etape = saga.etape(nom)
        if etape is None:
            # L'étape a disparu de la définition depuis le démarrage. On ne
            # devine pas comment la défaire, et l'on refuse de le taire.
            en_echec.append(f"{nom} : étape absente de la définition courante")
            continue
        if etape.compensation is None:
            en_echec.append(f"{nom} : irréversible — {etape.irreversible}")
            continue
        try:
            contexte = dict(etape.compensation(contexte))
            compensees.append(nom)
        except Exception as echec:  # noqa: BLE001
            en_echec.append(f"{nom} : {echec}")

    return execution.model_copy(
        update={
            "etat": (
                EtatSaga.COMPENSATION_INCOMPLETE if en_echec else EtatSaga.COMPENSEE
            ),
            "compensees": tuple(compensees),
            "compensations_en_echec": tuple(en_echec),
            "contexte": contexte,
            "dernier_echec": motif,
            "terminee_le": a_l_instant,
        }
    )
