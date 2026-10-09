"""Ce que la plateforme consomme, et ce qu'elle encaisse.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE MODULE MESURE, ET POURQUOI IL EXISTE

L'écran d'exploitation montrait ce qui **fonctionne** : les services inscrits,
la boîte d'envoi, la quarantaine, les travaux de fond. Il ne montrait rien de ce
que la machine **endure** : la mémoire prise, le processeur occupé, les
connexions de base employées, le temps qu'une requête met à répondre.

Or c'est précisément ce qu'on regarde en premier quand « c'est lent » — et la
seule chose dont on ne disposait pas.

⚠️ AUCUNE DÉPENDANCE NOUVELLE, ET CE N'EST PAS DE L'ASCÉTISME

`psutil` ferait ce travail en trois lignes. Il n'est pas déclaré dans les
dépendances du projet, donc pas dans l'image : l'ajouter pour un écran de
surveillance mettrait une bibliothèque de plus dans la chaîne d'approvisionnement
d'un produit qui manipule la comptabilité d'un tiers. `/proc` est toujours là sur
Linux, et c'est de là que `psutil` lit lui-même.

⚠️ LES MESURES SONT **EN MÉMOIRE, ET PAR INSTANCE**

Ce n'est pas un système de métriques : il n'y a ni agrégation, ni historique, ni
persistance. Deux répliques derrière un répartiteur rendent chacune ses propres
chiffres, et un redémarrage remet tout à zéro. Le dire est indispensable : un
exploitant qui croirait lire l'ensemble du parc conclurait de travers.

Ce que cela donne quand même, et qui n'existait pas : « à cet instant, sur cette
instance, la médiane est à 40 ms et 2 % des requêtes échouent ».

⚠️ L'ANNEAU EST BORNÉ, ET C'EST LA SEULE FAÇON DE NE PAS FUIR

Une liste qui grandirait à chaque requête est une fuite de mémoire déguisée en
mesure : sur un serveur qui tourne trois mois, elle finit par peser plus que
l'application. Mille entrées suffisent à une médiane et à un 95e centile, et
occupent quelques dizaines de kilo-octets.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = [
    "IntergicielMesureDeCharge",
    "MESURES",
    "MesureDesRequetes",
    "charge_du_processus",
    "charge_de_la_base",
]

#: Combien de requêtes l'anneau retient. Voir l'en-tête : borné, toujours.
TAILLE_DE_L_ANNEAU = 1000

#: Les chemins qu'on ne mesure PAS, parce que les mesurer fausserait la mesure.
#: La sonde de santé est appelée toutes les cinq secondes par Docker et par
#: Kubernetes : elle représenterait la moitié de l'anneau et écraserait les
#: latences réelles du travail du cabinet.
CHEMINS_IGNORES = frozenset({"/sante", "/transverse/charge"})


@dataclass(frozen=True)
class Passage:
    """Une requête servie. ⚠️ Le CHEMIN DÉCLARÉ, jamais l'adresse réelle.

    `/portefeuille/entreprises/M081234567890P` et le NIU du voisin sont deux
    adresses différentes pour une seule route. Retenir l'adresse réelle ferait
    deux choses, toutes deux mauvaises : l'anneau se remplirait d'une entrée par
    dossier, et les identifiants des clients se retrouveraient dans un écran de
    surveillance.
    """

    route: str
    methode: str
    statut: int
    millisecondes: float


class MesureDesRequetes:
    """L'anneau des passages, et ce qu'on en tire.

    ⚠️ **Protégé par un verrou.** Uvicorn sert plusieurs requêtes de front ;
    deux `append` simultanés sur une `deque` sont sûrs en CPython, mais la
    lecture qui calcule les centiles ne l'est pas si la collection bouge sous
    elle. Le verrou n'est pris que le temps d'une copie.
    """

    def __init__(self, taille: int = TAILLE_DE_L_ANNEAU) -> None:
        self._passages: deque[Passage] = deque(maxlen=taille)
        self._verrou = threading.Lock()
        self._depuis = time.monotonic()
        self._total = 0
        self._erreurs = 0
        self._refus = 0

    def noter(self, passage: Passage) -> None:
        # ─────────────────────────────────────────────────────────────────────
        # ⚠️ CE QUE LA PLATEFORME RATE N'EST PAS CE QU'ELLE REFUSE.
        #
        # Les deux étaient comptés ensemble, sous une raison qui se défendait :
        # « un 404 isolé est normal, un pic de 404 est un symptôme ; les séparer
        # perdrait le second pour épargner le premier ».
        #
        # Mesuré le 28 septembre sur la pile de démonstration, après une recette
        # complète :
        #
        #     requêtes 454 · 2xx 322 · 4xx 132 · 5xx 0
        #     taux d'erreur affiché : 29,1 %   (seuil d'alerte de la console : 2 %)
        #
        # Zéro panne, et un panneau rouge. Or ce produit REFUSE PAR MÉTIER : un
        # comptable écarté de l'acquisition rend 403, un dépôt aux pièces
        # incomplètes rend 409, un prix hors intervalle sans motif rend 422. Ce
        # sont des succès du produit, comptés comme ses échecs.
        #
        # ⚠️ Le raisonnement d'origine était optimiste sur un point : un taux
        # global ne détecte pas un PIC, il détecte un NIVEAU. Fusionner les deux
        # ne donnait donc pas le signal qu'on croyait acheter ; cela rendait
        # seulement le seuil inutilisable. Les deux taux sont désormais rendus,
        # et `par_statut` reste à côté.
        # ─────────────────────────────────────────────────────────────────────
        with self._verrou:
            self._passages.append(passage)
            self._total += 1
            if passage.statut >= 500:
                self._erreurs += 1
            elif passage.statut >= 400:
                self._refus += 1

    def instantane(self) -> dict[str, Any]:
        with self._verrou:
            passages = list(self._passages)
            total, erreurs, depuis = self._total, self._erreurs, self._depuis
            refus = self._refus

        latences = sorted(p.millisecondes for p in passages)
        par_statut: dict[str, int] = {}
        par_route: dict[str, list[float]] = {}
        for p in passages:
            famille = f"{p.statut // 100}xx"
            par_statut[famille] = par_statut.get(famille, 0) + 1
            par_route.setdefault(f"{p.methode} {p.route}", []).append(p.millisecondes)

        # Les cinq routes les plus lentes en médiane, et non en maximum : un
        # maximum est souvent un incident isolé, une médiane est un problème.
        plus_lentes = sorted(
            (
                {
                    "route": route,
                    "appels": len(mesures),
                    "mediane_ms": round(_centile(sorted(mesures), 50), 1),
                }
                for route, mesures in par_route.items()
            ),
            key=lambda ligne: float(ligne["mediane_ms"]),
            reverse=True,
        )[:5]

        secondes = max(time.monotonic() - depuis, 1e-9)
        return {
            "requetes_mesurees": len(passages),
            "requetes_depuis_le_demarrage": total,
            #: ⚠️ 5xx SEULEMENT. Ce que la plateforme a raté.
            "erreurs_depuis_le_demarrage": erreurs,
            "taux_d_erreur": round(erreurs / total, 4) if total else 0.0,
            #: ⚠️ 4xx seulement. Ce que la plateforme a refusé, et le refus est
            #: souvent la bonne réponse. Rendu sans seuil d'alerte : il n'existe
            #: pas de taux de refus « normal » — il dépend entièrement de ce que
            #: les appelants demandent, et 29 % sur une recette complète est sain.
            "refus_depuis_le_demarrage": refus,
            "taux_de_refus": round(refus / total, 4) if total else 0.0,
            "par_seconde": round(total / secondes, 2),
            "mediane_ms": round(_centile(latences, 50), 1),
            "p95_ms": round(_centile(latences, 95), 1),
            "max_ms": round(latences[-1], 1) if latences else 0.0,
            "par_statut": dict(sorted(par_statut.items())),
            "routes_les_plus_lentes": plus_lentes,
        }


def _centile(triees: list[float], rang: int) -> float:
    """Le centile d'une liste DÉJÀ TRIÉE. Zéro sur une liste vide.

    ⚠️ Pas d'interpolation : sur mille points, elle ne change rien de lisible et
    ajoute un cas limite à relire. On prend le point à la position.
    """
    if not triees:
        return 0.0
    position = min(int(len(triees) * rang / 100), len(triees) - 1)
    return triees[position]


#: L'instance du processus. Une seule : deux anneaux diraient deux vérités.
MESURES = MesureDesRequetes()


class IntergicielMesureDeCharge(BaseHTTPMiddleware):
    """Note la durée et l'issue de chaque requête servie.

    ⚠️ **La mesure est prise même quand la requête lève.** Une route qui échoue
    est précisément celle qu'on veut voir : ne compter que les succès ferait
    afficher « tout va bien » pendant une panne.
    """

    async def dispatch(
        self, requete: Request, appeler_suivant: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if requete.url.path in CHEMINS_IGNORES:
            return await appeler_suivant(requete)

        debut = time.perf_counter()
        statut = 500
        try:
            reponse = await appeler_suivant(requete)
            statut = reponse.status_code
            return reponse
        finally:
            MESURES.noter(
                Passage(
                    route=_route_declaree(requete),
                    methode=requete.method,
                    statut=statut,
                    millisecondes=(time.perf_counter() - debut) * 1000,
                )
            )


def _route_declaree(requete: Request) -> str:
    """Le chemin de la ROUTE, avec ses accolades — jamais l'adresse réelle.

    ⚠️ Starlette pose la route résolue dans le périmètre de la requête. Quand
    elle n'y est pas — un 404, une requête qui n'a atteint aucune route — on rend
    un libellé fixe plutôt que l'adresse : une adresse inconnue vient souvent
    d'un scanner, et l'anneau se remplirait de ses tentatives.
    """
    route = requete.scope.get("route")
    chemin = getattr(route, "path", None)
    return str(chemin) if chemin else "(hors route)"


def charge_du_processus() -> dict[str, Any]:
    """Ce que ce processus consomme, lu dans `/proc`.

    ⚠️ **Tout est optionnel.** `/proc` existe sur Linux ; sur un autre système,
    ou dans un bac à sable qui le masque, la lecture échoue. Un écran de
    surveillance qui tomberait faute de pouvoir se mesurer serait un comble : on
    rend `null` sur ce qu'on n'a pas pu lire, et l'écran le dit.
    """
    memoire_ko = _valeur_de_status("VmRSS")
    return {
        "memoire_resident_mo": round(memoire_ko / 1024, 1) if memoire_ko is not None else None,
        "fils_d_execution": _valeur_de_status("Threads"),
        "processeurs": os.cpu_count(),
        # ⚠️ La charge moyenne est celle de la MACHINE, pas du conteneur : dans
        # un conteneur, `/proc/loadavg` est celui de l'hôte. Sur un poste
        # partagé — celui du développement — elle compte donc les autres
        # projets. Le dire évite d'accuser la plateforme.
        "charge_moyenne": _charge_moyenne(),
        "temps_processeur_s": _temps_processeur(),
        "demarre_depuis_s": round(time.monotonic() - _DEMARRAGE, 1),
    }


_DEMARRAGE = time.monotonic()


def _valeur_de_status(cle: str) -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as fichier:
            for ligne in fichier:
                if ligne.startswith(f"{cle}:"):
                    return int(ligne.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _charge_moyenne() -> list[float] | None:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def _temps_processeur() -> float | None:
    try:
        temps = os.times()
        return round(temps.user + temps.system, 1)
    except (OSError, AttributeError):
        return None


def charge_de_la_base(moteur: Any) -> dict[str, Any]:
    """Ce que la base endure : connexions, taille, latence d'un aller-retour.

    ⚠️ **Le bassin de connexions d'abord.** C'est lui qui se sature en premier
    sur un produit multi-locataire : chaque requête prend une connexion pour la
    durée de sa transaction, et un bassin plein fait attendre les suivantes sans
    qu'aucune erreur ne soit rendue. « C'est lent » commence presque toujours là.

    ⚠️ Les lectures système de PostgreSQL sont tentées séparément : un rôle
    restreint peut se voir refuser `pg_stat_activity`, et cela ne doit pas
    empêcher de lire le reste.
    """
    if moteur is None:
        return {"joignable": False, "motif": "aucun moteur : persistance en mémoire"}

    resultat: dict[str, Any] = {"joignable": False}
    bassin = getattr(moteur, "pool", None)
    if bassin is not None:
        # ⚠️ `overflow()` rend un compteur NÉGATIF tant que le débordement n'est
        # pas employé : -8 signifie « huit places de débordement encore libres ».
        # Tel quel dans un écran d'exploitation, cela se lit comme une anomalie.
        # On rend donc ce qui est EMPLOYÉ, jamais en dessous de zéro.
        debordement = _appeler(bassin, "overflow")
        resultat["bassin"] = {
            "libres": _appeler(bassin, "checkedin"),
            "employees": _appeler(bassin, "checkedout"),
            "debordement_employe": max(0, debordement) if debordement is not None else None,
            "taille": _appeler(bassin, "size"),
        }

    from sqlalchemy import text

    debut = time.perf_counter()
    try:
        with moteur.connect() as connexion:
            connexion.execute(text("select 1"))
            resultat["joignable"] = True
            resultat["aller_retour_ms"] = round((time.perf_counter() - debut) * 1000, 2)
            resultat["connexions"] = _lecture(
                connexion,
                "select count(*) from pg_stat_activity where datname = current_database()",
            )
            resultat["connexions_maximum"] = _lecture(
                connexion, "select setting::int from pg_settings where name = 'max_connections'"
            )
            taille = _lecture(
                connexion, "select pg_database_size(current_database())"
            )
            resultat["taille_mo"] = round(taille / 1024 / 1024, 1) if taille else None
    except Exception as incident:  # noqa: BLE001 — l'écran doit dire pourquoi
        resultat["motif"] = type(incident).__name__
    return resultat


def _appeler(objet: Any, nom: str) -> int | None:
    methode = getattr(objet, nom, None)
    try:
        return int(methode()) if callable(methode) else None
    except Exception:  # noqa: BLE001
        return None


def _lecture(connexion: Any, requete: str) -> int | None:
    from sqlalchemy import text

    try:
        return int(connexion.execute(text(requete)).scalar_one())
    except Exception:  # noqa: BLE001 — un rôle restreint peut se voir refuser
        return None
