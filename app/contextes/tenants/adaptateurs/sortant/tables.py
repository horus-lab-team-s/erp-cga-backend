"""La table du contexte N.

⚠️ **Elle n'est pas cloisonnée, et c'est délibéré.**

Toutes les autres tables portent la colonne `locataire` et sa politique de sécurité.
Celle-ci non, pour une raison qui tient en une phrase : **elle est lue avant qu'on sache
de quel locataire il s'agit.** C'est elle qui le dit.

La cloisonner créerait une dépendance circulaire à l'exécution — pour lire la table des
locataires il faudrait déjà connaître le locataire —, et la politique refuserait chaque
requête faute de variable posée. Le répertoire ne verrait plus rien, et tout sous-domaine
rendrait 404.

CE QUE CELA IMPLIQUE, ET QU'IL FAUT ASSUMER

Un rôle qui lit cette table voit **tous les tenants** : leurs slugs, leurs statuts, leurs
dates. C'est la liste des clients du cabinet. Elle n'a donc rien à faire dans une réponse
d'API, et aucune route ne l'expose : seule la passerelle la consulte, en mémoire, pour
résoudre un nom d'hôte.

POURQUOI UN DOCUMENT, ET QUELLES COLONNES SONT PROMUES

Un tenant est un agrégat toujours lu entier. Trois colonnes seulement sortent du document,
parce que trois questions se posent en SQL :

* `slug` — la résolution, à chaque requête, et son **unicité** ;
* `statut` — pour lister ce qui est en échec ou en cours d'ouverture, ce que fait
  l'ordonnanceur quand il reprend les provisionnements interrompus ;
* `etape_atteinte` — pour savoir **où** reprendre, sans charger tous les documents.
"""

from __future__ import annotations

from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.base_de_donnees import Base, Document

__all__ = ["TableTenant"]


class TableTenant(Document, Base):
    """Un tenant du plan de contrôle. Volontairement hors du cloisonnement."""

    __tablename__ = "tenant"
    __table_args__ = (
        # La reprise des ouvertures interrompues : « ce qui est en échec, et où ».
        Index("ix_tenant_reprise", "statut", "etape_atteinte"),
        # ⚠️ **Sur l'expression, pas sur la colonne**, et déclaré ici plutôt que
        # seulement dans la migration.
        #
        # Il ne l'était pas, et l'écart s'est vu en interrogeant `alembic check` :
        # la migration posait cet index d'expression, le modèle déclarait un
        # `unique=True` ordinaire sur la colonne. Les deux contraintes ne disent
        # pas la même chose, et les deux bases ne se comportaient donc pas de la
        # même façon : une base migrée refusait « Station » à côté de « station »,
        # une base montée par `create_all` — c'est-à-dire **celle de la suite de
        # tests** — les acceptait toutes les deux.
        #
        # Un nom d'hôte est insensible à la casse. Deux tenants dont les slugs ne
        # diffèrent que par elle répondraient à la même adresse, et le second
        # servirait les données du premier.
        Index("uq_tenant_slug_insensible_casse", text("lower(slug)"), unique=True),
    )

    identifiant: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: Promu, et **unique**. C'est la base qui arbitre deux souscriptions simultanées sur
    #: le même slug, jamais une lecture suivie d'une écriture : entre les deux, l'autre a
    #: eu le temps d'écrire.
    #:
    #: ⚠️ **Pas de `unique=True` ici.** L'unicité est portée par l'index d'expression
    #: déclaré dans `__table_args__`, et l'ajouter en double poserait une seconde
    #: contrainte, celle-là sensible à la casse, qui n'apporterait rien et ferait
    #: diverger à nouveau le modèle de la migration.
    slug: Mapped[str] = mapped_column(String(40), nullable=False)

    #: Promu : l'ordonnanceur cherche ce qui est en échec ou resté en ouverture.
    statut: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Promue : savoir où reprendre sans charger le document.
    etape_atteinte: Mapped[str] = mapped_column(String(30), nullable=False)
