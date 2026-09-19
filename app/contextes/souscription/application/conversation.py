"""L'étape 3 : le responsable prend contact, et le dossier avance.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE FAIT, ET POURQUOI IL EXISTE

Il relie deux objets qui s'ignorent volontairement.

Le `Fil` connaît la fenêtre de service, les modèles et les accusés de remise. Il
ne sait pas ce qu'est un dossier commercial. Le `DossierCommercial` connaît ses
huit états. Il ne sait pas ce qu'est un message.

Cette ignorance mutuelle est ce qui permet au fil de servir un jour la relation
avec un adhérent déjà client, qui n'a plus de dossier commercial du tout. Le
lien entre les deux est un geste, et un geste s'écrit ici.

QUAND LE DOSSIER AVANCE, ET QUAND IL N'AVANCE PAS

Le premier échange enregistré fait passer le dossier de `AFFECTÉE` à
`EN_CONVERSATION`. Trois choses le déclenchent : un message sortant, un message
entrant, ou un appel journalisé.

⚠️ **Un appel sans réponse compte aussi.** Ce n'est pas une négligence :
`premier_echange_le` mesure la **réactivité du cabinet**, pas la disponibilité du
client. Un responsable qui a appelé trois fois dans l'heure a fait son travail,
et l'indicateur doit le dire même si personne n'a décroché.

Le dossier n'avance pas s'il n'est pas à `AFFECTÉE`. Un message qui arriverait sur
un dossier encore `DÉPOSÉE`, ou déjà `QUALIFIÉE`, enrichit le fil et laisse l'état
tranquille : ce module ne force aucune transition que le domaine refuse, et ne
rattrape aucune exception pour faire semblant.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.conversation import (
    AppelJournalise,
    Fil,
    Message,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)

__all__ = [
    "ResultatEchange",
    "enregistrer_un_appel",
    "enregistrer_un_message",
]


class ResultatEchange(BaseModel):
    """Le fil et le dossier après l'échange, les deux ensemble.

    Rendus ensemble parce qu'ils se persistent ensemble : conserver le message
    sans avancer le dossier laisserait un dossier `AFFECTÉE` dont la veille des
    vingt-quatre heures réaffecterait un responsable qui vient d'écrire.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    dossier: DossierCommercial
    fil: Fil
    #: `True` si cet échange est celui qui a ouvert la conversation.
    a_ouvert_la_conversation: bool


def enregistrer_un_message(
    dossier: DossierCommercial, fil: Fil, message: Message, a_l_instant: datetime
) -> ResultatEchange:
    """Range le message au fil, et fait avancer le dossier si c'est le premier.

    L'instant est passé séparément de celui du message : ils coïncident pour un
    envoi, et diffèrent pour un entrant rejoué par un rappel tardif. La date du
    message est celle où le client a écrit ; celle du dossier est celle où le
    cabinet en a pris connaissance.
    """
    return _enregistrer(dossier, fil.avec(message), a_l_instant)


def enregistrer_un_appel(
    dossier: DossierCommercial, fil: Fil, appel: AppelJournalise, a_l_instant: datetime
) -> ResultatEchange:
    """Journalise l'appel, et fait avancer le dossier si c'est le premier.

    Y compris un appel sans réponse : voir l'en-tête.
    """
    return _enregistrer(dossier, fil.avec_appel(appel), a_l_instant)


def _enregistrer(
    dossier: DossierCommercial, fil: Fil, a_l_instant: datetime
) -> ResultatEchange:
    if dossier.etat is not EtatDossier.AFFECTEE:
        return ResultatEchange(
            dossier=dossier, fil=fil, a_ouvert_la_conversation=False
        )
    return ResultatEchange(
        dossier=dossier.premier_contact(a_l_instant),
        fil=fil,
        a_ouvert_la_conversation=True,
    )
