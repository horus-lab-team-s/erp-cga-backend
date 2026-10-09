"""Les en-têtes de sécurité que toute réponse porte.

─────────────────────────────────────────────────────────────────────────────────
CE QUI MANQUAIT, MESURÉ SUR LA PILE EN SERVICE

Une réponse de l'API ne portait AUCUN en-tête de sécurité : ni `nosniff`, ni
politique de référent, ni refus d'encadrement, ni HSTS. Les manifestes de
déploiement n'en ajoutaient pas davantage — on avait supposé que l'entrée s'en
chargerait, et personne ne l'avait écrit.

Ce sont quatre lignes, et elles ferment quatre chemins ordinaires :

  · **nosniff** empêche un navigateur de « deviner » qu'un JSON est du HTML et de
    l'exécuter. Un justificatif déposé par un adhérent est du contenu venu du
    dehors : il ne doit jamais devenir du script ;
  · **la politique de référent** empêche l'adresse complète — qui porte parfois
    le sceau d'une proforma — de partir dans l'en-tête `Referer` vers un site
    tiers ;
  · **le refus d'encadrement** interdit qu'un site étranger place la console dans
    un cadre invisible pour voler les gestes du collaborateur ;
  · **HSTS** interdit au navigateur de retomber en clair après une première
    visite chiffrée.

⚠️ HSTS SEULEMENT QUAND L'API EST SERVIE EN HTTPS

Posé en développement, il obligerait le navigateur à refuser `http://localhost`
pendant six mois — y compris pour les autres projets du poste, puisque la
directive porte sur l'hôte entier. Il suit donc la même règle que le témoin de
session : l'adresse publique décide.

⚠️ PAS DE POLITIQUE DE CONTENU ICI

Une `Content-Security-Policy` utile se règle là où les pages sont servies — la
console et la vitrine, qui savent quels scripts elles chargent. Une politique
écrite au niveau de l'API, qui ne rend que du JSON, donnerait l'illusion d'une
protection sans en offrir aucune.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.infrastructure.config import configuration

__all__ = ["ENTETES_DE_SECURITE", "IntergicielEntetesDeSecurite"]

#: Ce que toute réponse porte, quelle que soit la route.
ENTETES_DE_SECURITE = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}

#: Six mois, sous-domaines compris. ⚠️ Pas de `preload` : l'inscription sur la
#: liste des navigateurs est difficile à défaire, et ce n'est pas une décision
#: d'infrastructure mais du cabinet.
_HSTS = "max-age=15552000; includeSubDomains"


class IntergicielEntetesDeSecurite(BaseHTTPMiddleware):
    """Pose les en-têtes sur la réponse, sans jamais écraser ce qu'une route a mis."""

    async def dispatch(
        self, requete: Request, appeler_suivant: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        reponse = await appeler_suivant(requete)
        for nom, valeur in ENTETES_DE_SECURITE.items():
            # ⚠️ `setdefault` : une route qui a une raison de dire autre chose —
            # un document à afficher dans un cadre, par exemple — garde la main.
            reponse.headers.setdefault(nom, valeur)
        if configuration().temoin_securise:
            reponse.headers.setdefault("Strict-Transport-Security", _HSTS)
        return reponse
