"""Point d'entrée du backend.

Monolithe modulaire : un package par contexte borné, des frontières nettes, aucun
microservice. Voir Docs/architecture/01-contextes-bornes.md.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.contextes.conformite.adaptateurs.entrant.routes_http import (
    routeur as routeur_conformite,
)
from app.contextes.referentiel.adaptateurs.entrant.routes_http import (
    routeur as routeur_referentiel,
)
from app.contextes.vitrine.adaptateurs.entrant.routes_http import (
    routeur as routeur_vitrine,
)
from app.infrastructure.config import configuration

DESCRIPTION = """
Plateforme de suivi fiscal et comptable du Centre de Gestion Agréé
**Broad Range Consulting Group** — agrément MINFI/DGI n° 00000048.

⚠️ **Aucune valeur légale de ce système n'a été validée sur le Code Général des Impôts.**
Les paramètres proviennent de sources secondaires et portent le statut `A_VALIDER`.
Consulter `/referentiel/validation` pour l'état exact. Aucun chiffre produit n'est
opposable tant qu'un fiscaliste nommé n'a pas confirmé chaque valeur sur le texte.
"""


def creer_application() -> FastAPI:
    config = configuration()
    application = FastAPI(
        title=config.nom_application,
        description=DESCRIPTION,
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.origines_cors,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(routeur_referentiel)
    application.include_router(routeur_conformite)
    # La vitrine publique : contenu éditorial du site, en lecture seule et sans
    # authentification — tout ce qu'elle rend est déjà destiné à être affiché.
    application.include_router(routeur_vitrine)

    @application.get("/sante", tags=["Technique"], summary="Vérification de disponibilité")
    def sante() -> dict[str, str]:
        return {"etat": "operationnel", "environnement": config.environnement}

    return application


app = creer_application()
