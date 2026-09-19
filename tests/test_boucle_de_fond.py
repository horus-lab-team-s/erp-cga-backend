"""La boucle de fond : elle rappelle, elle s'arrête vite, et elle ne meurt jamais.

⚠️ **Ces cas mesurent des durées, ce qui est inhabituel ici et assumé.** Trois des
propriétés de ce module ne se lisent pas dans son résultat mais dans son
comportement temporel : s'arrêter avant l'échéance, ne pas figer la boucle
d'événements, reculer après une panne. Les vérifier autrement reviendrait à
vérifier que le code est écrit comme il est écrit.

Les seuils sont larges — de l'ordre de la seconde contre des mesures attendues au
centième — parce qu'une machine chargée ne doit pas faire rougir la suite. Un cas
qui échoue au hasard finit ignoré, et un cas ignoré ne protège rien.
"""

from __future__ import annotations

import asyncio
import time

from app.infrastructure.boucle_de_fond import boucler, demarrer

#: Un intervalle qu'aucun cas de ce fichier n'atteint jamais. S'il est atteint,
#: c'est que la boucle attend son échéance au lieu d'écouter l'arrêt.
JAMAIS = 30.0

#: ⚠️ **Toute attente sur une tâche de boucle est bornée par ce délai.**
#:
#: Sans cette borne, une boucle qui dort au lieu d'écouter l'arrêt fait *pendre* la
#: suite trente secondes au lieu de la faire échouer. C'est arrivé en éprouvant ces
#: cas par mutation : le mutant était bien détecté, mais par un blocage.
#:
#: Un test qui pend est pire qu'un test qui échoue. Il ne dit pas ce qui ne va pas,
#: il immobilise la chaîne d'intégration, et il pousse à lancer la suite en
#: arrière-plan, c'est-à-dire à ne plus la regarder.
PATIENCE = 2.0


def _lancer(coroutine):
    """`asyncio.run` plutôt qu'un greffon de test asynchrone.

    Le projet n'en a aucun, et en introduire un pour cinq cas ferait dépendre toute
    la suite d'une dépendance de plus, avec son mode de configuration et ses
    incompatibilités de version.
    """
    return asyncio.run(coroutine)


class TestElleRappelle:
    def test_elle_appelle_le_tour_au_moins_une_fois(self):
        tours = []

        async def scenario():
            arret = asyncio.Event()
            tache = asyncio.create_task(
                boucler(lambda: tours.append(1), lambda: JAMAIS, arret=arret)
            )
            await asyncio.sleep(0.05)
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)

        _lancer(scenario())
        assert tours == [1]

    def test_elle_relit_l_intervalle_a_chaque_tour(self):
        """La cadence se change **sans redémarrer**.

        L'intervalle est une fonction, pas une valeur : un `intervalle: float` figé
        au démarrage obligerait à redéployer pour passer le relais de cinq secondes
        à une minute, ce qui est exactement le genre de réglage qu'on veut pouvoir
        toucher pendant un incident.
        """
        lectures = []

        def intervalle() -> float:
            lectures.append(len(lectures))
            return 0.01

        async def scenario():
            arret = asyncio.Event()
            tache = asyncio.create_task(boucler(lambda: None, intervalle, arret=arret))
            await asyncio.sleep(0.06)
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)

        _lancer(scenario())
        assert len(lectures) >= 2, "l'intervalle n'est lu qu'une fois : il est figé"


class TestElleSArreteVite:
    def test_un_arret_demande_n_attend_pas_l_echeance(self):
        """⚠️ La propriété qui décide de la durée d'un redéploiement.

        `asyncio.sleep` rendrait la main au bout du délai quoi qu'il arrive : un
        arrêt demandé au début d'une attente de trente secondes ferait attendre
        l'orchestrateur trente secondes, et il finirait par tuer le processus.
        """

        async def scenario():
            arret = asyncio.Event()
            tache = asyncio.create_task(boucler(lambda: None, lambda: JAMAIS, arret=arret))
            await asyncio.sleep(0.02)
            debut = time.monotonic()
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)
            return time.monotonic() - debut

        assert _lancer(scenario()) < 1.0

    def test_l_arreteur_laisse_le_tour_en_cours_aller_a_son_terme(self):
        """⚠️ Annuler la tâche l'interromprait **où qu'elle en soit**.

        Un tour interrompu au milieu laisserait sa transaction annulée, ce qui est
        propre, mais aussi son travail à moitié fait sans que le passage le dise.
        Poser l'événement laisse le tour finir.
        """
        acheve = []

        def tour_lent() -> None:
            time.sleep(0.2)
            acheve.append(1)

        async def scenario():
            _, arreter = await demarrer(tour_lent, lambda: JAMAIS)
            await asyncio.sleep(0.02)  # le tour a commencé
            await asyncio.wait_for(arreter(), timeout=PATIENCE)

        _lancer(scenario())
        assert acheve == [1], "le tour en cours a été interrompu au lieu d'être attendu"


class TestElleNeMeurtJamais:
    def test_une_exception_n_arrete_pas_la_boucle(self):
        """⚠️ La panne la plus coûteuse possible, parce qu'elle est invisible.

        Une exception non rattrapée tuerait la tâche sans bruit : l'application
        continuerait de servir, la boucle serait morte, et plus rien ne
        publierait. Aucune sonde ne le dirait, aucune requête n'échouerait.
        """
        appels = []

        def casse() -> None:
            appels.append(1)
            raise RuntimeError("base injoignable")

        async def scenario(monkey_intervalle):
            arret = asyncio.Event()
            tache = asyncio.create_task(
                boucler(casse, monkey_intervalle, arret=arret)
            )
            await asyncio.sleep(0.1)
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)

        import app.infrastructure.boucle_de_fond as module

        ancien = module.INTERVALLE_APRES_PANNE
        module.INTERVALLE_APRES_PANNE = 0.01
        try:
            _lancer(scenario(lambda: 0.01))
        finally:
            module.INTERVALLE_APRES_PANNE = ancien

        assert len(appels) >= 2, "la boucle est morte au premier échec"

    def test_apres_une_panne_elle_recule(self):
        """Reboucler tout de suite martèlerait une base déjà en difficulté.

        C'est un recul distinct de celui des travaux : celui-ci répond à « le tour
        lui-même n'a pas pu s'exécuter », c'est-à-dire session impossible à ouvrir
        ou base muette, et non « un travail a échoué ».
        """
        appels = []

        async def scenario():
            arret = asyncio.Event()

            def casse() -> None:
                appels.append(1)
                raise RuntimeError("base injoignable")

            tache = asyncio.create_task(boucler(casse, lambda: 0.001, arret=arret))
            await asyncio.sleep(0.1)
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)

        _lancer(scenario())
        # L'intervalle demandé vaut une milliseconde ; cent millisecondes en
        # donneraient des dizaines si le recul ne s'appliquait pas.
        assert len(appels) <= 3, "le recul après panne ne s'applique pas"


class TestElleNeFigePasLeService:
    def test_le_tour_bloquant_ne_bloque_pas_la_boucle_d_evenements(self):
        """⚠️ Le défaut qui ne se verrait que sous charge.

        ─────────────────────────────────────────────────────────────────────────
        Le tour ouvre une session, interroge la base et publie : c'est du code
        bloquant. L'appeler directement figerait la boucle d'événements pendant
        toute sa durée, donc **toutes les requêtes HTTP en cours**. Une publication
        d'une seconde bloquerait une seconde le service entier.

        ⚠️ **CE CAS A D'ABORD ÉTÉ ÉCRIT DE TRAVERS, ET LA MUTATION L'A DIT.**

        Sa première version faisait battre un compteur trente fois et vérifiait
        qu'il atteignait vingt. Remplacer `to_thread(un_tour)` par `un_tour()` ne le
        faisait pas échouer : **un compteur borné finit par atteindre son total même
        après un gel**, il met simplement plus longtemps. Le cas mesurait la
        complétion, pas la fluidité, et le mutant a survécu.

        Il mesure maintenant le nombre de battements dans une **durée de mur fixée**.
        Sans gel, une attente de cinq millisecondes en donne quelques dizaines ; avec
        un gel de cent cinquante millisecondes sur deux cents, il en reste une
        poignée.

        *Une mutation qui survit ne dit pas toujours que le garde est inutile. Elle
        dit d'abord que le test ne mesure pas ce qu'il croit mesurer.*
        ─────────────────────────────────────────────────────────────────────────
        """
        FENETRE = 0.2
        GEL = 0.15
        battements = []

        def tour_bloquant() -> None:
            time.sleep(GEL)

        async def battre():
            # ⚠️ Une **durée de mur**, pas un nombre de tours. Voir la docstring :
            # un compteur borné atteint son total même après un gel.
            fin = time.monotonic() + FENETRE
            while time.monotonic() < fin:
                battements.append(1)
                await asyncio.sleep(0.005)

        async def scenario():
            arret = asyncio.Event()
            tache = asyncio.create_task(
                boucler(tour_bloquant, lambda: JAMAIS, arret=arret)
            )
            await battre()
            arret.set()
            await asyncio.wait_for(tache, timeout=PATIENCE)

        _lancer(scenario())
        # Sans gel, la fenêtre en autorise une quarantaine ; le seuil est bas pour
        # ne pas rougir sur une machine chargée, et un gel de 150 ms sur 200 en
        # laisserait moins de dix.
        assert len(battements) >= 20, (
            "la boucle d'événements a été figée pendant le tour : le service ne "
            "répondait plus"
        )
