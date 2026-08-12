"""Cas d'usage du contexte L · Vitrine publique.

─────────────────────────────────────────────────────────────────────────────────
CE QUE FAIT CE SERVICE

Il répond aux questions que le site public pose, et rien d'autre : quels articles
montrer, dans quel ordre, lequel s'appelle ainsi, que lire ensuite, quelle annonce
est en cours aujourd'hui. Ce sont des décisions de **lecture** — elles ne
dépendent ni du transport HTTP, ni du format de stockage.

CE QU'IL NE FAIT PAS

Il ne lit aucun fichier. Le contenu lui est remis par un dépôt qui réalise le port
`DepotContenuVitrine`, et il ignore si ce dépôt lit du YAML, une base ou un cache.
C'est ce qui permet de le tester avec un dépôt en mémoire, sans disque.

POURQUOI LE CONTENU EST CHARGÉ UNE FOIS

Le service prend ses listes au moment de sa construction. Un site public sert la
même page à des milliers de visiteurs : relire le disque à chaque requête serait
absurde. En contrepartie, une correction du fichier n'est visible qu'après
reconstruction du service — c'est l'adaptateur entrant qui décide quand, et il le
documente.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from app.contextes.vitrine.domaine.entites import Annonce, Article, Institution, Rubrique
from app.contextes.vitrine.domaine.ports import DepotContenuVitrine

__all__ = ["ArticleInconnu", "ServiceContenu", "SlugsEnDoublon"]


class ArticleInconnu(LookupError):
    """Aucun article ne porte ce slug.

    Erreur explicite plutôt que `None` : un article manquant doit produire un 404
    lisible, jamais une page à moitié vide dont personne ne comprend l'origine.
    """


class SlugsEnDoublon(ValueError):
    """Deux articles portent le même slug.

    Détecté au chargement et non à la lecture : deux articles à la même adresse,
    c'est l'un des deux définitivement inatteignable, et le hasard de l'ordre du
    fichier qui décide lequel.
    """


class ServiceContenu:
    """Lecture du contenu éditorial de la vitrine."""

    def __init__(
        self,
        articles: list[Article],
        annonces: list[Annonce],
        institutions: list[Institution],
    ) -> None:
        doublons = _slugs_en_doublon(articles)
        if doublons:
            raise SlugsEnDoublon(f"slugs d'articles en double : {', '.join(sorted(doublons))}")

        # Triés une fois pour toutes, du plus récent au plus ancien : c'est l'ordre
        # de lecture du sommaire, et le seul que le site demande.
        self._articles = sorted(articles, key=lambda article: article.date, reverse=True)
        self._par_slug = {article.slug: article for article in self._articles}
        self._annonces = list(annonces)
        self._institutions = list(institutions)

    @classmethod
    def depuis_depot(cls, depot: DepotContenuVitrine) -> ServiceContenu:
        """Assemble le service à partir d'une source quelconque.

        Le cas d'usage ne construit pas son dépôt : il le reçoit. C'est
        l'adaptateur entrant qui choisit lequel, et c'est là que le passage du
        YAML à une base se décidera.
        """
        return cls(
            articles=depot.charger_articles(),
            annonces=depot.charger_annonces(),
            institutions=depot.charger_institutions(),
        )

    # ── Articles ──────────────────────────────────────────────────────────────

    def articles(self, rubrique: Rubrique | None = None) -> list[Article]:
        """Les articles, du plus récent au plus ancien, filtrés au besoin.

        Une rubrique inconnue n'a pas à être gérée ici : le type l'interdit. C'est
        l'adaptateur entrant qui traduit une chaîne d'URL fantaisiste en absence de
        filtre plutôt qu'en erreur — un lien mal recopié doit montrer le blog, pas
        une page d'erreur.
        """
        if rubrique is None:
            return list(self._articles)
        return [article for article in self._articles if article.rubrique is rubrique]

    def article(self, slug: str) -> Article:
        """L'article portant ce slug, ou `ArticleInconnu`."""
        try:
            return self._par_slug[slug]
        except KeyError as exc:
            raise ArticleInconnu(f"aucun article ne porte le slug « {slug} »") from exc

    def voisins(self, slug: str, combien: int = 3) -> list[Article]:
        """Ce qu'on propose de lire ensuite, au bas d'un article.

        Même rubrique d'abord — un lecteur venu pour du juridique en veut d'autre —
        puis complété par les plus récents si la rubrique est trop maigre. Une
        rubrique qui ne compterait qu'un article ne doit pas produire un bas de page
        vide.
        """
        courant = self.article(slug)
        autres = [article for article in self._articles if article.slug != slug]
        meme_rubrique = [a for a in autres if a.rubrique is courant.rubrique]
        reste = [a for a in autres if a.rubrique is not courant.rubrique]
        return [*meme_rubrique, *reste][:combien]

    def comptes_par_rubrique(self) -> dict[Rubrique, int]:
        """Le nombre d'articles par rubrique, pour les pastilles du filtre.

        Toutes les rubriques sont présentes, y compris à zéro : une rubrique vide
        doit se voir avant d'être ouverte, pour qu'on ne clique pas dans le vide.
        """
        comptes = dict.fromkeys(Rubrique, 0)
        for article in self._articles:
            comptes[article.rubrique] += 1
        return comptes

    # ── Annonces ──────────────────────────────────────────────────────────────

    def annonce_du_jour(self, a_la_date: date) -> Annonce | None:
        """L'annonce à afficher à cette date, ou aucune.

        La **première** encore valide dans l'ordre du fichier, et une seule : deux
        bandeaux superposés font une page qu'on ne lit plus. L'ordre du fichier est
        donc l'ordre de priorité, et il est de la main du cabinet.

        La date est un argument et non `date.today()` : c'est ce qui rend le cas
        d'usage testable, et ce qui évite qu'un serveur et un visiteur dans deux
        fuseaux ne voient pas la même chose sans qu'on l'ait décidé.
        """
        for annonce in self._annonces:
            if annonce.est_active(a_la_date):
                return annonce
        return None

    # ── Institutions ──────────────────────────────────────────────────────────

    def institutions(self) -> list[Institution]:
        return list(self._institutions)


def _slugs_en_doublon(articles: list[Article]) -> set[str]:
    vus: set[str] = set()
    doublons: set[str] = set()
    for article in articles:
        if article.slug in vus:
            doublons.add(article.slug)
        vus.add(article.slug)
    return doublons
