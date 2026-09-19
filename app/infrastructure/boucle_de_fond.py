"""La boucle qui appelle l'ordonnanceur, quand on veut qu'elle tourne dans le processus.

─────────────────────────────────────────────────────────────────────────────────
DEUX FAÇONS DE FAIRE TOURNER LES TRAVAUX, ET AUCUNE N'EST « LA BONNE »

Un ordonnanceur **extérieur** — `cron`, un déclencheur d'orchestrateur, une tâche
programmée — appelle `POST /orchestration/ordonnancement` à son rythme. Il survit
au redémarrage de l'application, se règle sans la toucher, et son état se lit
ailleurs. C'est le choix par défaut, et c'est pourquoi le réglage
`CGA_ORDONNANCEUR_EN_PROCESSUS` vaut `false`.

Une boucle **dans le processus** ne demande rien à installer. Sur un serveur unique,
c'est ce qui fait la différence entre un système qui marche après un `docker
compose up` et un système qui attend qu'un exploitant lise une documentation.

⚠️ **Les deux ensemble sont sûrs**, et c'est ce qui permet de ne pas trancher. Le
verrou consultatif arbitre : la boucle et l'appel extérieur demandent le même
verrou, un seul l'obtient, l'autre passe son tour sans lever. Ce module n'a donc
aucun besoin de savoir si quelqu'un d'autre appelle.

CE QU'ELLE NE FAIT PAS, ET POURQUOI

Elle ne décide pas ce qui est dû : c'est `app/orchestration/ordonnanceur.py`, et
l'instant y est un argument. Elle ne fait pas le travail : c'est
`routes_orchestration._un_tour`, la même fonction que la route appelle. **Elle ne
fait que dormir et rappeler**, et c'est délibérément tout ce qu'elle fait : ce qui
reste ici est exactement ce qu'un test ne peut pas vérifier sans attendre.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

__all__ = ["INTERVALLE_APRES_PANNE", "boucler"]

_journal = logging.getLogger("cga.ordonnanceur")

#: Le temps d'attente après une panne du tour lui-même, par opposition à une panne
#: d'un travail — celle-là est comptée par le passage et gérée par son recul.
#:
#: ⚠️ Une panne ici veut dire que la base n'a pas répondu, ou que la session n'a pas
#: pu s'ouvrir. Reboucler tout de suite martèlerait une base déjà en difficulté,
#: précisément au moment où elle a besoin qu'on la laisse respirer.
INTERVALLE_APRES_PANNE = 30.0


async def boucler(
    un_tour: Callable[[], object],
    intervalle: Callable[[], float],
    *,
    arret: asyncio.Event,
) -> None:
    """Appelle `un_tour` à intervalles réguliers jusqu'à ce qu'`arret` soit posé.

    ─────────────────────────────────────────────────────────────────────────────
    ELLE ATTEND SUR L'ÉVÉNEMENT D'ARRÊT, ET NON SUR UNE HORLOGE

    `asyncio.sleep` rendrait la main au bout du délai, quoi qu'il arrive. Un arrêt
    demandé une seconde après le début d'une attente de cinq minutes ferait attendre
    l'orchestrateur cinq minutes, et il finirait par tuer le processus.

    `wait_for` sur l'événement se réveille **au premier des deux** : l'échéance ou
    l'arrêt. Un conteneur qui s'arrête le fait alors en quelques millisecondes.

    ⚠️ **LE TOUR EST APPELÉ DANS UN FIL, PAS DANS LA BOUCLE.** `un_tour` ouvre une
    session, interroge la base et publie : c'est du code bloquant. L'appeler
    directement figerait la boucle d'événements pendant toute sa durée, donc
    **toutes les requêtes HTTP en cours**. Une publication d'une seconde bloquerait
    une seconde le service entier, et cela ne se verrait que sous charge.

    RIEN NE PROPAGE, ET LA BOUCLE NE MEURT PAS

    Une exception non rattrapée ici tuerait la tâche sans bruit : l'application
    continuerait de servir, la boucle serait morte, et plus rien ne publierait.
    C'est la panne la plus coûteuse possible, puisqu'elle est invisible. Tout est
    donc rattrapé, journalisé, et la boucle reprend après un délai plus long.
    ─────────────────────────────────────────────────────────────────────────────
    """
    _journal.info("ordonnanceur en processus : démarré")
    while not arret.is_set():
        attente = intervalle()
        try:
            await asyncio.to_thread(un_tour)
        except Exception as panne:  # noqa: BLE001 — voir l'en-tête : la boucle ne meurt pas
            _journal.exception("tour d'ordonnanceur en échec (%s)", type(panne).__name__)
            attente = INTERVALLE_APRES_PANNE
        await _attendre(arret, attente)
    _journal.info("ordonnanceur en processus : arrêté")


async def _attendre(arret: asyncio.Event, secondes: float) -> None:
    """Dort, ou s'interrompt aussitôt si l'arrêt est demandé."""
    try:
        await asyncio.wait_for(arret.wait(), timeout=secondes)
    except TimeoutError:
        # L'échéance est arrivée avant l'arrêt : c'est le cas normal, pas une panne.
        # ⚠️ `TimeoutError` et non `asyncio.TimeoutError` : les deux sont le même
        # objet depuis Python 3.11, et l'alias du module est en voie de retrait.
        return


async def demarrer(
    un_tour: Callable[[], object], intervalle: Callable[[], float]
) -> tuple[asyncio.Task[None], Callable[[], Awaitable[None]]]:
    """Lance la boucle et rend de quoi l'arrêter proprement.

    ⚠️ L'arrêteur est rendu plutôt que la tâche seule : annuler une tâche
    l'interrompt **où qu'elle en soit**, y compris au milieu d'un tour, ce qui
    laisserait un passage marqué commencé et jamais terminé. Poser l'événement
    laisse le tour en cours aller à son terme.
    """
    arret = asyncio.Event()
    tache = asyncio.create_task(boucler(un_tour, intervalle, arret=arret))

    async def arreter() -> None:
        arret.set()
        # Le tour en cours va à son terme. Sans borne, un tour bloqué sur une base
        # muette retiendrait l'arrêt indéfiniment et l'orchestrateur tuerait le
        # processus, ce qui est justement ce qu'on cherche à éviter.
        try:
            await asyncio.wait_for(tache, timeout=INTERVALLE_APRES_PANNE)
        except TimeoutError:
            _journal.warning("ordonnanceur : arrêt forcé, un tour ne rendait pas la main")
            tache.cancel()

    return tache, arreter
