"""La conformité du Centre et la liste de ses adhérents (CGI art. 118 et 119, 10 novembre 2026).

Lues par la direction (`LIRE_PILOTAGE`) : ce sont des obligations du PROMOTEUR, pas d'un
dossier. Voir `domaine/conformite_du_centre.py`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Response

from app.contextes.pilotage.adaptateurs.entrant.sources import depot_dossiers
from app.contextes.pilotage.adaptateurs.sortant.conformite_du_centre_yaml import (
    charger_les_regles_du_centre,
    raison_sociale_du_cabinet,
)
from app.contextes.pilotage.adaptateurs.sortant.liste_des_adherents_excel import (
    liste_des_adherents_en_excel,
    nom_du_fichier,
)
from app.contextes.pilotage.domaine.conformite_du_centre import (
    ConformiteDuCentre,
    adherents_actifs,
    evaluer_la_conformite,
)
from app.contextes.transverse.api import AccesRequis, Permission, exiger
from app.infrastructure.config import configuration
from app.partage.horloge import aujourd_hui

__all__ = ["routeur"]

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · conformité du centre"])


@routeur.get(
    "/conformite-du-centre",
    summary="Ce que la loi impose au Centre : adhérents actifs, professionnels, fourchettes",
)
def lire_la_conformite_du_centre(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> ConformiteDuCentre:
    exiger(acces, Permission.LIRE_PILOTAGE)
    jour = a_la_date or aujourd_hui()
    regles = charger_les_regles_du_centre(configuration().dossier_referentiel)
    return evaluer_la_conformite(depot_dossiers().lister(), regles, jour)


@routeur.get(
    "/liste-des-adherents.xlsx",
    summary="La liste à jour des adhérents, à annexer aux déclarations mensuelles",
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}}}
    },
)
def exporter_la_liste_des_adherents(
    acces: AccesRequis,
    au: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> Response:
    exiger(acces, Permission.LIRE_PILOTAGE)
    jour = au or aujourd_hui()
    referentiel = configuration().dossier_referentiel
    cabinet, agrement = raison_sociale_du_cabinet(referentiel)
    octets = liste_des_adherents_en_excel(
        adherents_actifs(depot_dossiers().lister(), jour),
        jour,
        cabinet=cabinet,
        agrement=agrement,
    )
    return Response(
        content=octets,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nom_du_fichier(jour)}"'},
    )
