"""Une requête qui modifie, portant la session, vient d'un site du cabinet.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI (audit de sécurité du 19 octobre 2026)

Le témoin de session est `SameSite=Lax` : un formulaire posté depuis un autre SITE ne
l'emporte pas. Mais « site » veut dire domaine enregistré, pas origine : une page
servie sur un autre sous-domaine du même domaine, ou un site de prévisualisation mal
rangé, est du même site, et le navigateur y joint le témoin. C'est la falsification
de requête (CSRF) qui reste ouverte malgré `Lax`.

La parade : une requête qui MODIFIE (POST, PUT, PATCH, DELETE), qui PORTE le témoin, et
dont l'en-tête `Origin` désigne une origine que le cabinet ne sert pas, est refusée.

⚠️ CE QUI PASSE SANS CONTRÔLE, ET POURQUOI CE N'EST PAS UNE BRÈCHE

  · une requête SANS `Origin` : les appels de serveur à serveur (la console et la
    vitrine parlent à l'API depuis leur serveur), l'application mobile, la
    notification de Tara. Un navigateur, lui, pose toujours `Origin` sur un POST
    venu d'ailleurs : l'attaque visée ne peut pas l'omettre ;
  · une requête SANS témoin : elle n'a rien à usurper.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.infrastructure.config import configuration

__all__ = ["IntergicielOrigine", "origines_du_cabinet"]

_METHODES_QUI_MODIFIENT = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: ⚠️ Le nom du témoin de session, recopié de `transverse/.../dependances.NOM_TEMOIN` :
#: l'infrastructure ne dépend d'aucun contexte. Un test vérifie que les deux concordent.
_TEMOIN = "cga_session"


def _origine(adresse: str) -> str:
    """`https://console.cga.cm/fr/x` → `https://console.cga.cm`."""
    morceaux = adresse.split("/")
    return "/".join(morceaux[:3]).lower() if len(morceaux) >= 3 else adresse.lower()


def origines_du_cabinet() -> frozenset[str]:
    """Les origines que le cabinet sert : l'API, la console, la vitrine, et CORS."""
    config = configuration()
    adresses = [
        config.adresse_publique,
        config.adresse_publique_site,
        config.adresse_publique_vitrine,
        *config.origines_cors,
    ]
    return frozenset(_origine(a) for a in adresses if a and a != "*")


class IntergicielOrigine(BaseHTTPMiddleware):
    """Refuse une modification portant la session et venue d'une origine étrangère."""

    async def dispatch(
        self, requete: Request, appeler_suivant: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origine = requete.headers.get("origin")
        if (
            requete.method in _METHODES_QUI_MODIFIENT
            and origine
            and _TEMOIN in requete.cookies
            and _origine(origine) not in origines_du_cabinet()
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Requête refusée : elle ne vient pas d'un site du cabinet."},
            )
        return await appeler_suivant(requete)
