"""Ce qui poste réellement une relance, et le canal qu'il choisit pour cela.

─────────────────────────────────────────────────────────────────────────────────
LA BOUCLE QUI SE REFERME

Le balayage dépose un `RelanceDue`, le relais le publie, et jusqu'ici personne ne
l'écoutait. Ce module est l'abonné : il reçoit l'événement et joint le client.

⚠️ **LE CANAL EST CHOISI ICI, ET NON AU DÉPÔT.**

C'est la décision structurante de ce module, et elle a une raison précise. Entre le
dépôt et la remise, il peut s'écouler un tour, une reprise après panne, ou une
journée d'arriéré. Pendant ce temps, un client peut avoir **révoqué son
consentement**, le centre peut avoir désactivé un canal au référentiel, ou la
messagerie peut avoir cessé d'être prête.

Un canal figé au dépôt ferait partir un message sur un consentement révoqué. Le
choisir à la remise le rend impossible : le repli est recalculé sur l'état du moment.

AUCUNE PLATEFORME EXTÉRIEURE N'EST BLOQUANTE

C'est la règle posée au pas 5 bis, et ce module est le premier à la subir vraiment.
La messagerie n'est employée que si elle est **prête** — un compte ouvert et au moins
un modèle approuvé — et à défaut le repli descend vers le courriel puis vers l'appel.

⚠️ L'appel n'envoie rien : il **inscrit une tâche pour un humain**. C'est ce qui rend
le plancher réel. Un système dont le dernier recours serait encore un envoi
automatique n'aurait aucun plancher du tout.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.canaux import (
    Canal,
    PlanDeContact,
    Repli,
    canal_a_employer,
)
from app.contextes.souscription.domaine.dossier_commercial import DossierCommercial
from app.orchestration.boite_d_envoi import EvenementSortant

__all__ = [
    "CODE_COURRIEL_RELANCE",
    "IssueDeRemise",
    "RemiseImpossible",
    "Remise",
    "remettre_une_relance",
]

#: Le code du gabarit de courriel. Le service de notification le résout dans son
#: propre catalogue : le domaine ne connaît ni SMTP, ni HTML, ni gabarit.
CODE_COURRIEL_RELANCE = "relance.proforma"


class RemiseImpossible(RuntimeError):
    """La relance n'a pas pu être remise, et elle doit être retentée.

    ⚠️ **Elle lève, et c'est ce qu'il faut.** Le relais compte les échecs, retient
    les événements suivants de la même clé, et met en quarantaine au delà du seuil.
    Rendre un booléen ferait passer un échec pour un succès dès qu'un appelant
    oublierait de le lire, et le client ne serait jamais relancé.
    """


class IssueDeRemise(BaseModel):
    """Ce qui a été fait, et par quel canal. Sans aucun contenu de message."""

    model_config = ConfigDict(frozen=True)

    proforma: str
    canal: Canal
    #: `True` quand ce n'est pas le canal que le client avait demandé.
    replie: bool
    #: Ce qui a écarté les canaux précédents. ⚠️ Journalisé : sans cela, on ne
    #: saura pas pourquoi un client qui avait coché la messagerie a reçu un appel.
    ecartes: tuple[str, ...] = ()
    #: `True` quand la remise a produit une tâche humaine plutôt qu'un envoi.
    confiee_a_un_humain: bool = False


class ServiceDeCourriel(Protocol):
    def envoyer(
        self, code: str, *, destinataire: str, contexte: dict[str, Any]
    ) -> bool: ...


class TachesHumaines(Protocol):
    """Ce que ce module exige du carnet des rappels à passer, et rien de plus."""

    def inscrire(
        self, dossier: str, motif: str, *, a_l_instant: datetime
    ) -> None: ...


@dataclass(frozen=True)
class Remise:
    """Les dépendances d'une remise, rassemblées pour ne pas les passer une à une.

    ⚠️ **Une dataclass et non un modèle Pydantic**, contrairement au reste du
    contexte. Ce n'est pas une entité : c'est un faisceau de collaborateurs, dont
    trois sont des protocoles. Pydantic refuse de valider un `Protocol` nu, et le
    rendre vérifiable à l'exécution n'apporterait rien — il n'y a ici aucune donnée
    venue du dehors à contrôler.
    """

    plan: PlanDeContact
    courriels: ServiceDeCourriel
    taches: TachesHumaines
    horloge: Callable[[], datetime]
    #: ⚠️ **Faux tant que la plateforme de messagerie n'est pas ouverte.** Ce n'est
    #: pas un réglage de confort : c'est ce qui empêche le système de dépendre d'une
    #: validation extérieure qu'il ne contrôle pas. Voir le pas 5 bis.
    #:
    #: Déclaré en dernier parce qu'il porte un défaut, et qu'une dataclass refuse
    #: un champ sans défaut après un champ qui en a un.
    messagerie_prete: bool = False


def remettre_une_relance(
    evenement: EvenementSortant, dossier: DossierCommercial, remise: Remise
) -> IssueDeRemise:
    """Joint le client par le canal réellement employable maintenant.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LE REPLI EST RECALCULÉ, JAMAIS RELU.**

    Voir l'en-tête : entre le dépôt de l'événement et cette remise, un client peut
    avoir révoqué son consentement. Le canal du moment est le seul qui vaille.

    L'ORDRE DES TROIS BRANCHES SUIT L'ORDRE DU REPLI

    Et il n'y a **pas de branche par défaut**. `canal_a_employer` lève plutôt que de
    rendre un canal inconnu, et le domaine n'en déclare que trois : une branche
    « sinon » ne pourrait être atteinte qu'en ajoutant un canal sans passer ici, et
    elle masquerait précisément cet oubli.
    ─────────────────────────────────────────────────────────────────────────────
    """
    demande = dossier.derniere_demande
    repli = canal_a_employer(
        demande.canal_prefere,
        remise.plan,
        consentement_vaut=demande.consentement.vaut_maintenant,
        a_un_courriel=bool(demande.courriel),
        messagerie_prete=remise.messagerie_prete,
    )
    numero = evenement.charge.get("proforma", "")

    if repli.canal is Canal.COURRIEL:
        return _par_courriel(evenement, dossier, repli, remise, numero)
    if repli.canal is Canal.WHATSAPP:
        return _par_messagerie(repli, remise, numero)
    return _par_appel(evenement, dossier, repli, remise, numero)


def _par_courriel(
    evenement: EvenementSortant,
    dossier: DossierCommercial,
    repli: Repli,
    remise: Remise,
    numero: str,
) -> IssueDeRemise:
    """Le courriel part par le service de notification, qui connaît les gabarits.

    ⚠️ **Un refus d'envoi lève.** Le relais retentera, et retiendra les relances
    suivantes de la même proforma en attendant : un rang 2 posté alors que le rang 1
    n'est jamais parti ferait recevoir au client une relance de deuxième niveau pour
    un message qu'il n'a jamais eu.
    """
    parti = remise.courriels.envoyer(
        CODE_COURRIEL_RELANCE,
        destinataire=dossier.derniere_demande.courriel or "",
        # ⚠️ Ni le montant ni l'identité ne transitent : le gabarit les lira où
        # elles vivent. Ce contexte porte de quoi choisir le ton et rien de plus.
        contexte={
            "proforma": numero,
            "rang": evenement.charge.get("rang"),
            "ton": evenement.charge.get("ton"),
            "depuis_jours": evenement.charge.get("depuis_jours"),
            "derniere": evenement.charge.get("derniere", False),
        },
    )
    if not parti:
        raise RemiseImpossible(
            f"relance {numero} : le service de courriel a refusé l'envoi vers "
            "l'adresse du dossier. L'événement sera retenté au passage suivant."
        )
    return IssueDeRemise(
        proforma=numero, canal=repli.canal, replie=repli.replie, ecartes=repli.ecartes
    )


def _par_messagerie(repli: Repli, remise: Remise, numero: str) -> IssueDeRemise:
    """La messagerie n'est pas branchée, et le dire vaut mieux que le taire.

    ⚠️ `canal_a_employer` ne rend ce canal que si `messagerie_prete`. Arriver ici
    avec une passerelle absente est donc une **incohérence de câblage**, pas un état
    normal : la remise lève plutôt que de faire silencieusement rien, ce qui
    laisserait croire que le client a été relancé.
    """
    raise RemiseImpossible(
        f"relance {numero} : le canal messagerie a été retenu alors qu'aucune "
        "passerelle n'est branchée. Vérifier que `messagerie_prete` reflète l'état "
        "réel du compte, ou désactiver ce canal au référentiel."
    )


def _par_appel(
    evenement: EvenementSortant,
    dossier: DossierCommercial,
    repli: Repli,
    remise: Remise,
    numero: str,
) -> IssueDeRemise:
    """Le plancher : une tâche pour un humain, jamais un envoi.

    ⚠️ **C'est ce qui rend le plancher réel.** Un système dont le dernier recours
    serait encore un envoi automatique n'aurait aucun plancher : il dépendrait
    toujours d'une passerelle, et une panne de celle-ci arrêterait toute relance.

    Un appel ne coûte rien à la note du numéro, ne demande aucune approbation
    extérieure, et c'est le seul canal dont le centre dispose sans condition.
    """
    rang = evenement.charge.get("rang")
    remise.taches.inscrire(
        dossier.reference,
        f"rappeler au sujet de la proforma {numero} (relance de rang {rang})",
        a_l_instant=remise.horloge(),
    )
    return IssueDeRemise(
        proforma=numero,
        canal=repli.canal,
        replie=repli.replie,
        ecartes=repli.ecartes,
        confiee_a_un_humain=True,
    )
