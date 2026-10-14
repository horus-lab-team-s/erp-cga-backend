"""L'intergiciel d'unité de travail ne doit jamais arrêter la boucle d'événements.

─────────────────────────────────────────────────────────────────────────────────
⚠️ CE QUE CE FICHIER PROTÈGE, ET CE QUE COÛTAIT SON ABSENCE

Mesuré le 30 septembre 2026 sur la pile de démonstration, avec **un seul
navigateur en séquentiel** : dix connexions libres, puis trente transactions
ouvertes en trois secondes, et plus aucune réponse pendant dix minutes.

La cause, photographiée dans les piles du processus : `dispatch` est asynchrone,
donc il s'exécute sur la boucle d'événements, et il y ouvrait la session de la
requête. Quand le bassin est vide, `QueuePool.get` attend — et il attendait **sur
la boucle**. Le verrouillage se referme alors sur lui-même : la boucle attend une
connexion, et seules les réponses qu'elle ne peut plus écrire en rendraient une.

⚠️ Aucune taille de bassin ne corrige cela. Le nombre de requêtes en vol sur la
boucle n'est pas borné, donc il y en aura toujours une de trop. Ce n'est pas un
problème de dimensionnement, c'est un problème d'endroit.

CE QUI EST MESURÉ, ET POURQUOI PAS UNE DURÉE

Une première version chronométrait quatre requêtes et les comparait à un seuil.
Elle échouait à tort : `/sante` coûte à lui seul plus de cent millisecondes, et ce
coût, étranger à la propriété, noyait la mesure.

Ce qui est compté ici est le nombre d'ouvertures **simultanées**. Sur la boucle,
il vaut un par construction, quelles que soient les vitesses. Dans un fil, il vaut
le nombre de requêtes en vol. La mesure ne dépend donc d'aucun seuil de temps, et
une machine chargée ne peut pas la faire rougir.

⚠️ ÉPROUVÉ PAR MUTATION. En rendant à `dispatch` sa forme d'origine —
`with etabli(locataire), unite_de_travail():` — le premier cas échoue : le maximum
observé retombe à un. Le second cas, lui, continue de passer, ce qui est voulu :
c'est lui qui atteste que l'injection est bien traversée.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import threading
import time

import httpx
import pytest

from app.contextes.transverse.adaptateurs.entrant import dependances
from app.main import creer_application

#: La largeur de la fenêtre pendant laquelle deux ouvertures peuvent se croiser.
#: Assez longue pour qu'un ordonnancement de fils ordinaire les fasse se
#: recouvrir, assez courte pour que le fichier tourne en une seconde.
ATTENTE = 0.25

#: Combien de requêtes partent ensemble.
REQUETES = 4


class _Compteur:
    """Compte les ouvertures simultanées de l'unité de travail.

    ⚠️ Sous verrou : les ouvertures ont lieu dans des fils différents dès que la
    correction est en place, et c'est précisément ce qu'on mesure.
    """

    def __init__(self) -> None:
        self.verrou = threading.Lock()
        self.en_cours = 0
        self.maximum = 0
        self.appels = 0


@pytest.fixture
def ouvertures(monkeypatch):
    """Rend l'ouverture de l'atelier lente et **bloquante**, comme une vraie.

    ⚠️ Le point d'injection est `_atelier_du_processus` et non `ouvrir_l_atelier` :
    c'est le seul que les deux formes de l'intergiciel traversent, l'ancienne comme
    la nouvelle. Injecter plus haut laisserait passer le mutant sans rien mesurer.

    `time.sleep` et non `asyncio.sleep` : c'est une attente de fil, exactement ce
    qu'est l'emprunt d'une connexion au bassin. Une attente asynchrone rendrait la
    main à la boucle et ne prouverait rien.
    """
    compteur = _Compteur()
    veritable = dependances._atelier_du_processus

    def lent():
        with compteur.verrou:
            compteur.appels += 1
            compteur.en_cours += 1
            compteur.maximum = max(compteur.maximum, compteur.en_cours)
        try:
            time.sleep(ATTENTE)
        finally:
            with compteur.verrou:
                compteur.en_cours -= 1
        return veritable()

    monkeypatch.setattr(dependances, "_atelier_du_processus", lent)
    return compteur


def _lancer(coroutine):
    """`asyncio.run`, comme `test_boucle_de_fond.py`. Voir sa note : le projet n'a
    aucun greffon de test asynchrone, et en introduire un pour deux cas ferait
    dépendre toute la suite d'une dépendance de plus."""
    return asyncio.run(coroutine)


async def _tirer(application, combien: int) -> list[int]:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reponses = await asyncio.gather(*(client.get("/sante") for _ in range(combien)))
    return [r.status_code for r in reponses]


class TestLIntergicielNArretePasLeServeur:
    def test_les_ouvertures_se_chevauchent_au_lieu_de_se_suivre(self, ouvertures):
        # ⚠️ L'application est bâtie ici, en mode mémoire, et non prise à la
        # fixture `plateforme` : celle-ci monte PostgreSQL, et la propriété mesurée
        # n'a rien à voir avec la persistance. Elle porte sur l'endroit où
        # l'intergiciel attend, pas sur ce qu'il attend.
        application = creer_application()

        codes = _lancer(_tirer(application, REQUETES))

        # Elles ont toutes abouti : le parallélisme n'a pas été gagné en perdant
        # des requêtes en route.
        assert codes == [200] * REQUETES
        assert ouvertures.maximum > 1, (
            f"{REQUETES} requêtes parties ensemble n'ont jamais ouvert leur unité de "
            "travail à plus d'une à la fois. L'intergiciel l'ouvre donc sur la boucle "
            "d'événements, où l'attente d'une connexion arrête le serveur entier."
        )

    def test_chaque_requete_traverse_bien_l_ouverture(self, ouvertures):
        """La contre-épreuve : l'injection mord, et elle mord une fois par requête.

        Sans ce cas, un premier qui passerait parce que la mesure ne porte sur rien
        ne prouverait rien du tout.
        """
        application = creer_application()

        codes = _lancer(_tirer(application, REQUETES))

        assert codes == [200] * REQUETES
        # ⚠️ « au moins une fois par requête » et non « exactement » : l'ancienne
        # forme de l'intergiciel prenait l'atelier deux fois par requête — une pour
        # la variable de contexte, une pour la valeur rendue. C'est sans effet,
        # l'atelier du processus étant unique, mais un décompte exact ferait de ce
        # cas un contrôle de la forme du code plutôt que de la propriété.
        assert ouvertures.appels >= REQUETES, (
            f"l'ouverture n'a été traversée que {ouvertures.appels} fois pour "
            f"{REQUETES} requêtes : l'injection ne mesure pas ce qu'on croit."
        )
        # Et tout a été refermé : aucune unité de travail ne reste ouverte.
        assert ouvertures.en_cours == 0


# ═════════════════════════════════════════════════════════════════════════════
# LA RÈGLE, APPLIQUÉE À TOUT CE QUI VIT SUR LA BOUCLE
# ═════════════════════════════════════════════════════════════════════════════

#: Les routes déclarées asynchrones, et **la raison** de chacune.
#:
#: ⚠️ CETTE TABLE EST FERMÉE, ET C'EST SON OBJET. Une route asynchrone de plus
#: fait rougir le premier cas ci-dessous, ce qui oblige à la décision : ou bien
#: elle n'attend que du réseau et elle rejoint la table, ou bien elle fait du
#: travail bloquant et elle doit le confier à un fil.
#:
#: Toutes celles qui figurent ici le sont pour la même raison : lire le corps de
#: la requête est réellement asynchrone. Tout ce qui suit cette lecture, lui, ne
#: l'est pas, et part dans un fil de la réserve.
ROUTES_ASYNCHRONES = {
    "deposer_fichier": "await fichier.read()",
    "deposer_fichier_scelle": "await fichier.read()",
    "deposer_fichier_de_formalite": "await fichier.read()",
    "deposer_fichier_de_creation": "await fichier.read()",
    "reprendre": "await fichier.read()",
    "notification_tara": "await requete.json()",
}

#: Ce qui n'est pas une route mais traverse la boucle à chaque requête.
#:
#: `acces_optionnel` est asynchrone pour une raison qui n'a rien à voir avec
#: l'attente : une dépendance synchrone s'exécute avec une **copie** du contexte,
#: et le mandat qu'elle pose n'atteindrait jamais le journal. Sa vérification de
#: session, elle, lit la base, et doit donc partir dans un fil.
DEPENDANCES_ASYNCHRONES = {"acces_optionnel"}


def _routes_asynchrones() -> dict[str, tuple[str, ast.AsyncFunctionDef, ast.AST]]:
    """Toutes les fonctions asynchrones décorées par un routeur, dans `app/`.

    L'arbre du module est rendu avec la route : il faut y chercher les aides
    qu'elle attend. Voir `_confie_a_un_fil`.
    """
    trouvees: dict[str, tuple[str, ast.AsyncFunctionDef, ast.AST]] = {}
    for fichier in sorted(pathlib.Path("app").rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), str(fichier))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.AsyncFunctionDef):
                continue
            verbes = {"get", "post", "put", "patch", "delete"}
            if any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") in verbes
                for d in noeud.decorator_list
            ):
                trouvees[noeud.name] = (str(fichier), noeud, arbre)
    return trouvees


def _appelle_le_bac_a_fils(noeud: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and (
            getattr(n.func, "id", "") == "run_in_threadpool"
            or getattr(n.func, "attr", "") == "run_in_threadpool"
        )
        for n in ast.walk(noeud)
    )


def _confie_a_un_fil(noeud: ast.AST, arbre: ast.AST) -> bool:
    """Vrai si la fonction confie son travail bloquant à un fil de la réserve.

    ⚠️ UN NIVEAU D'INDIRECTION EST SUIVI, ET UN SEUL.

    Les trois portes de dépôt de fichier ne délèguent pas elles-mêmes : elles
    partagent un corps, `_ranger_le_fichier`, et c'est lui qui confie. Une analyse
    qui ne regarderait que le corps de la route les déclarerait fautives alors
    qu'elles sont correctes — c'est ce qu'a fait la première version de ce cas.

    Au-delà d'un niveau, l'analyse deviendrait un interpréteur. Une route qui
    s'enfoncerait plus profond doit rendre sa délégation visible chez elle.
    """
    if _appelle_le_bac_a_fils(noeud):
        return True
    aides = {
        n.name: n
        for n in ast.walk(arbre)
        if isinstance(n, ast.AsyncFunctionDef)
    }
    for interne in ast.walk(noeud):
        if not isinstance(interne, ast.Await):
            continue
        appel = interne.value
        if not isinstance(appel, ast.Call):
            continue
        nom = getattr(appel.func, "id", None)
        if nom in aides and _appelle_le_bac_a_fils(aides[nom]):
            return True
    return False


class TestRienDeBloquantNeResteSurLaBoucle:
    """⚠️ Des cas de **forme**, et c'est délibéré.

    Le cas de comportement plus haut prouve que le mécanisme fonctionne. Ceux-ci
    empêchent qu'on l'oublie ailleurs : une route asynchrone ajoutée demain ne
    figerait le serveur qu'en production, et sous charge seulement, c'est-à-dire
    le jour où personne ne cherche une régression.
    """

    def test_les_routes_asynchrones_sont_celles_qu_on_connait(self):
        trouvees = set(_routes_asynchrones())
        assert trouvees == set(ROUTES_ASYNCHRONES), (
            "la liste des routes asynchrones a changé.\n"
            f"  en trop : {sorted(trouvees - set(ROUTES_ASYNCHRONES))}\n"
            f"  disparues : {sorted(set(ROUTES_ASYNCHRONES) - trouvees)}\n"
            "Une route asynchrone s'exécute sur la boucle d'événements : tout ce "
            "qu'elle y fait de bloquant arrête le serveur entier. Ou bien elle "
            "n'attend que du réseau, et elle rejoint ROUTES_ASYNCHRONES avec sa "
            "raison ; ou bien elle est synchrone, et le cadre la placera dans un fil."
        )

    def test_chaque_route_asynchrone_confie_son_travail_a_un_fil(self):
        manquantes = [
            f"{chemin}::{nom}"
            for nom, (chemin, noeud, arbre) in sorted(_routes_asynchrones().items())
            if not _confie_a_un_fil(noeud, arbre)
        ]
        assert not manquantes, (
            "ces routes asynchrones ne confient rien à un fil : "
            f"{manquantes}. Elles lisent le corps de la requête, ce qui est "
            "légitimement asynchrone, mais tout ce qui suit — hacher, écrire sur "
            "disque, écrire en base — bloque la boucle d'événements."
        )

    def test_la_dependance_d_acces_confie_sa_verification_a_un_fil(self):
        depuis = pathlib.Path(
            "app/contextes/transverse/adaptateurs/entrant/dependances.py"
        )
        arbre = ast.parse(depuis.read_text(encoding="utf-8"), str(depuis))
        vues = {
            n.name: n
            for n in ast.walk(arbre)
            if isinstance(n, ast.AsyncFunctionDef) and n.name in DEPENDANCES_ASYNCHRONES
        }
        absentes = sorted(DEPENDANCES_ASYNCHRONES - set(vues))
        assert not absentes, f"dépendances asynchrones attendues introuvables : {absentes}"
        for nom, noeud in sorted(vues.items()):
            assert _confie_a_un_fil(noeud, arbre), (
                f"`{nom}` est asynchrone et lit la base sur la boucle d'événements. "
                "Chaque requête authentifiée la traverse : le serveur entier y "
                "attend la base pour authentifier son appelant."
            )
