"""API du contexte L · Vitrine publique.

─────────────────────────────────────────────────────────────────────────────────
QUI APPELLE CES ROUTES

Le site public, au moment où il construit ses pages. Elles sont donc **en lecture
seule et sans authentification** : tout ce qu'elles rendent est déjà destiné à
être affiché publiquement. Il n'y a rien à protéger ici, et une authentification
n'ajouterait qu'un point de panne entre le site et son propre contenu.

L'écriture viendra plus tard, par un écran d'administration réservé au cabinet.
Elle n'a pas sa place sur ce routeur : une route publique et une route
d'administration n'ont ni le même public, ni les mêmes garanties.

POURQUOI LE SERVICE EST EN CACHE

`@lru_cache` construit le service une fois, à la première requête, et le réutilise.
Le contenu se compte en dizaines d'entrées et ne change qu'à la main : le relire à
chaque visiteur serait du gaspillage pur.

⚠️ **Conséquence à connaître : une correction du YAML n'est visible qu'après
redémarrage du backend.** C'est assumé pour l'instant — le cabinet corrige par
lots, pas en continu. Le jour où l'édition deviendra quotidienne, ce cache devra
gagner une invalidation ; c'est ici, et nulle part ailleurs, qu'elle se posera.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.contextes.vitrine.api import (
    Annonce,
    Article,
    ArticleInconnu,
    DepotContenuVitrineYaml,
    Institution,
    Rubrique,
    ServiceContenu,
)
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/vitrine", tags=["Vitrine publique"])


@lru_cache
def service() -> ServiceContenu:
    """Assemble le service, une fois. C'est ici que la source est choisie.

    Le passage éventuel du YAML à une base se fait sur cette seule ligne : le cas
    d'usage reçoit un dépôt, il ne sait pas lequel.
    """
    depot = DepotContenuVitrineYaml(configuration().dossier_contenu_vitrine)
    return ServiceContenu.depuis_depot(depot)


class SommaireBlog(BaseModel):
    """Ce dont le sommaire du blog a besoin, en une seule requête.

    Les articles et les comptes par rubrique partent ensemble : le site les affiche
    sur le même écran, et deux allers-retours pour une page seraient deux fois plus
    de latence sur une connexion mobile camerounaise.
    """

    articles: list[Article]
    comptes_par_rubrique: dict[Rubrique, int]


class ArticleEtVoisins(BaseModel):
    """L'article, et ce qu'on propose de lire ensuite.

    Même raison qu'au-dessus : la page d'article affiche les deux, elle ne doit les
    demander qu'une fois.
    """

    article: Article
    voisins: list[Article]


@routeur.get(
    "/articles",
    summary="Sommaire du blog",
    description=(
        "Les articles du plus récent au plus ancien, avec le compte par rubrique. "
        "Une rubrique inconnue est ignorée plutôt que refusée : un lien mal recopié "
        "doit montrer le blog entier, pas une page d'erreur."
    ),
)
def lister_articles(
    rubrique: str | None = Query(
        default=None,
        description="Filtre facultatif. Valeur inconnue : ignorée.",
    ),
) -> SommaireBlog:
    contenu = service()
    # Une chaîne fantaisiste ne lève pas : elle vaut « aucun filtre ». Le sommaire
    # complet est toujours une réponse acceptable, une erreur ne l'est pas.
    filtre = Rubrique(rubrique) if rubrique in set(Rubrique) else None
    return SommaireBlog(
        articles=contenu.articles(filtre),
        comptes_par_rubrique=contenu.comptes_par_rubrique(),
    )


@routeur.get(
    "/articles/{slug}",
    summary="Un article et ses voisins de lecture",
)
def lire_article(slug: str) -> ArticleEtVoisins:
    contenu = service()
    try:
        article = contenu.article(slug)
    except ArticleInconnu as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArticleEtVoisins(article=article, voisins=contenu.voisins(slug))


@routeur.get(
    "/annonce",
    summary="L'annonce en cours à une date",
    description=(
        "Rend `null` s'il n'y a rien à afficher ce jour-là. La date est obligatoire : "
        "il n'existe volontairement aucune façon de demander « l'annonce courante » "
        "sans dire de quel jour on parle — le serveur et le visiteur ne sont pas "
        "toujours dans le même fuseau."
    ),
)
def annonce_du_jour(
    a_la_date: date = Query(..., description="Jour pour lequel on demande l'annonce"),
) -> Annonce | None:
    return service().annonce_du_jour(a_la_date)


@routeur.get(
    "/institutions",
    summary="Les institutions dans le cadre desquelles le cabinet exerce",
    description=(
        "Ce ne sont pas des partenaires commerciaux : DGI, CNPS, ONECCA et OHADA sont "
        "les institutions avec lesquelles un centre de gestion agréé traite par nature."
    ),
)
def lister_institutions() -> list[Institution]:
    return service().institutions()
