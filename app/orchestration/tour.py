"""Un tour d'ordonnanceur : faire tourner ce qui est dû, et rendre compte.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE NE SAIT PAS CE QU'IL FAIT TOURNER

Il reçoit des **exécutants**, c'est-à-dire des fonctions sans argument qui rendent
une ligne de compte rendu. Il ne connaît ni le relais, ni le balayage de relance,
ni la base. Cette ignorance est ce qui le rend testable sans rien monter : un
exécutant qui lève, un qui traîne, un qui réussit, et les trois cas se vérifient en
trois lignes.

⚠️ **AUCUN ÉCHEC NE PROPAGE.** C'est la propriété qui compte, et elle a la même
raison d'être que dans le relais : un travail qui lève ne doit pas empêcher les
suivants de tourner. Un balayage de relance cassé par un modèle de message absent
n'a aucune raison d'arrêter la publication des événements, dont dépend l'ouverture
des tenants payés.

L'ÉCHEC EST COMPTÉ, ET C'EST LE COMPTE QUI DÉCIDE

Un travail qui lève voit son compteur monter, donc son recul s'allonger, et
finalement son abandon au delà du seuil. Le module `ordonnanceur` porte cette
logique ; celui-ci se contente de l'alimenter avec ce qui s'est réellement passé.

LE DÉBUT EST ENREGISTRÉ, MAIS PAS AVANT : LA CORRECTION D'UNE ERREUR DE CONCEPTION

Ce module a d'abord rendu **deux** états par travail : celui du début, à écrire et
valider avant d'appeler l'exécutant, et celui de la fin. L'intention était de
détecter un processus tué en cours de travail, qui ne repasse jamais par la ligne
qui écrit la fin.

⚠️ **Cela ne pouvait pas fonctionner, et c'est le premier tour réel qui l'a montré.**
L'exclusion entre instances repose sur un verrou consultatif de **transaction** :
il tombe à la validation. Valider la marque de début en cours de tour aurait donc
rendu le verrou au milieu du travail, et une seconde instance serait entrée.

Le raisonnement était faux d'un cran plus haut. Avec un verrou de transaction, un
processus tué ne laisse **rien** : sa transaction est annulée, son verrou tombe
avec sa connexion, et le tour suivant repart d'un état propre. Il n'y a donc aucun
état « commencé sans finir » à détecter — la marque de début résolvait un problème
que cette conception n'a pas.

`debute_le` est conservé et écrit avec `termine_le`, en une seule fois : il mesure
la **durée** d'un tour, ce qui reste utile et n'a jamais demandé deux écritures.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.orchestration.ordonnanceur import Passage, Travail, travaux_dus

__all__ = ["Executant", "RapportDeTour", "ResultatDeTravail", "faire_un_tour"]

#: Un travail concret. Rend une ligne de compte rendu, ou lève.
#:
#: ⚠️ Il ne rend **pas** un booléen de succès. Un exécutant qui échoue lève : c'est
#: la convention de Python, et la seule qui ne puisse pas être ignorée par
#: inadvertance. Un booléen de retour se laisse oublier, et l'oublier ferait passer
#: un échec pour un succès sans qu'aucune ligne ne manque au journal.
Executant = Callable[[], str]


class ResultatDeTravail(BaseModel):
    """Ce qu'un travail a donné pendant ce tour."""

    model_config = ConfigDict(frozen=True)

    travail: str
    reussi: bool
    #: La ligne rendue par l'exécutant, ou le motif de l'échec.
    compte_rendu: str


class RapportDeTour(BaseModel):
    """Ce que le tour a fait, et l'état à écrire en base.

    Les deux listes d'états sont distinctes parce qu'elles s'écrivent à deux
    moments différents. Voir l'en-tête du module.
    """

    model_config = ConfigDict(frozen=True)

    resultats: tuple[ResultatDeTravail, ...] = ()
    #: L'état de chaque travail après son exécution, début et fin portés ensemble.
    #: Une seule écriture par travail : voir l'en-tête du module pour la raison,
    #: qui n'est pas une simplification mais une correction.
    fins: tuple[Passage, ...] = ()
    #: Les travaux dus dont aucun exécutant n'a été fourni. Voir `faire_un_tour`.
    sans_executant: tuple[str, ...] = ()

    @property
    def echecs(self) -> tuple[ResultatDeTravail, ...]:
        return tuple(r for r in self.resultats if not r.reussi)

    @property
    def rien_a_faire(self) -> bool:
        return not self.resultats and not self.sans_executant


def faire_un_tour(
    travaux: Sequence[Travail],
    passages: dict[str, Passage],
    executants: Mapping[str, Executant],
    a_l_instant: datetime,
) -> RapportDeTour:
    """Fait tourner les travaux dus, un par un, sans qu'aucun échec n'arrête les autres.

    ─────────────────────────────────────────────────────────────────────────────
    UN TRAVAIL DÉCLARÉ SANS EXÉCUTANT EST SIGNALÉ, JAMAIS IGNORÉ

    La tentation serait de sauter silencieusement : après tout, il n'y a rien à
    faire. Mais c'est exactement la forme que prend une faute de frappe dans un nom
    de travail, ou un exécutant oublié au branchement d'un nouveau travail. Le
    résultat serait un travail déclaré, visible dans la configuration, présent dans
    la sonde, et qui ne tourne jamais.

    ⚠️ Il n'est pas non plus compté comme un échec. Un échec allonge le recul et
    conduit à l'abandon, alors qu'ici il n'y a rien à réessayer : c'est un défaut
    de branchement, qui se corrige dans le code et non par une nouvelle tentative.

    L'INSTANT EST LE MÊME POUR TOUT LE TOUR

    Il est reçu en argument et non relu entre deux travaux. Un tour qui relirait
    l'horloge verrait un travail dû au début du tour et plus dû à la fin, ou
    l'inverse, selon la durée du précédent. Un même tour doit rendre le même
    verdict pour tous ses travaux, sans quoi l'ordre de déclaration cesserait
    d'être seulement un ordre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    resultats: list[ResultatDeTravail] = []
    fins: list[Passage] = []
    orphelins: list[str] = []

    for travail in travaux_dus(travaux, passages, a_l_instant):
        executant = executants.get(travail.nom)
        if executant is None:
            orphelins.append(travail.nom)
            continue

        avant = passages.get(travail.nom, Passage(travail=travail.nom)).model_copy(
            update={"debute_le": a_l_instant}
        )

        try:
            compte_rendu = executant()
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête : rien ne propage
            # Le type autant que le message : un motif qui dit seulement
            # « connection refused » ne dit pas quelle couche a refusé.
            motif = f"{type(echec).__name__} : {echec}"
            resultats.append(
                ResultatDeTravail(travail=travail.nom, reussi=False, compte_rendu=motif)
            )
            fins.append(avant.echoue(a_l_instant, motif))
            continue

        resultats.append(
            ResultatDeTravail(
                travail=travail.nom, reussi=True, compte_rendu=str(compte_rendu)
            )
        )
        fins.append(avant.reussi(a_l_instant))

    return RapportDeTour(
        resultats=tuple(resultats),
        fins=tuple(fins),
        sans_executant=tuple(orphelins),
    )
