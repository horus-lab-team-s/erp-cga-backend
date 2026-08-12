"""Entités du contexte L · Vitrine publique.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Le site public du cabinet n'est pas un ensemble de pages figées : c'est du contenu
éditorial que le cabinet doit pouvoir corriger, dater et retirer lui-même. Les
entités décrites ici sont donc du **contenu**, pas de la présentation. On n'y
trouve ni couleur, ni gabarit, ni classe CSS : la vitrine décide seule de la façon
de les afficher, et changer la maquette ne touche pas à ce fichier.

CE QU'IL NE MODÉLISE PAS

Aucune donnée d'adhérent, aucun chiffre fiscal, aucune règle de calcul. C'est
pourquoi le contexte L ne dépend d'aucun autre : il ne lit ni le Référentiel, ni le
Portefeuille. Un contenu qui aurait besoin d'un paramètre légal ne serait pas du
contenu — ce serait un calcul, et il appartiendrait au contexte qui le porte.

POURQUOI TOUT EST GELÉ (`frozen=True`)

Le contenu est chargé une fois puis partagé par toutes les requêtes. Une entité
modifiable laisserait un adaptateur altérer, pour tout le monde, un article qu'il
n'a fait que lire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "Annonce",
    "Article",
    "Bloc",
    "BlocEncadre",
    "BlocIntertitre",
    "BlocListe",
    "BlocParagraphe",
    "Institution",
    "Rubrique",
]


class Rubrique(StrEnum):
    """Les rendez-vous éditoriaux du cabinet.

    Ce ne sont pas des catégories inventées : ce sont les rubriques que le cabinet
    publie déjà chaque semaine sur sa page Facebook, avec leurs personnages —
    Aïcha le lundi, Owona le mercredi, Kamdem sur l'impôt. Les reprendre telles
    quelles fait que l'audience reconnaît ce qu'elle retrouve sur le site.

    L'énumération est **fermée volontairement** : un article dont la rubrique est
    inconnue fait échouer le chargement du contenu. Mieux vaut un démarrage refusé
    qu'un article invisible parce que sa rubrique ne correspond à aucun filtre.
    """

    SAVIEZ_VOUS = "saviezVous"
    VRAI_OU_FAUX = "vraiOuFaux"
    LUNDI_COMPTABLE = "lundiComptable"
    MERCREDI_JURIDIQUE = "mercrediJuridique"
    COIN_FISCALISTE = "coinFiscaliste"
    ANNONCES = "annonces"


# ── Les blocs qui composent le corps d'un article ─────────────────────────────
#
# Un corps d'article est une suite de blocs typés, et non du Markdown ou du HTML.
# Trois raisons, dans cet ordre d'importance :
#
#   1. Sécurité — un contenu qui arrive du backend et qui serait du HTML devrait
#      être assaini avant affichage. Des blocs typés ne peuvent porter que du
#      texte : il n'y a rien à assainir, et rien à injecter.
#   2. Rendu — le site rend chaque type de bloc à sa façon, dans son propre style.
#      Du Markdown obligerait à styler des balises génériques.
#   3. Édition — une personne qui corrige le YAML voit la structure de l'article.


class BlocParagraphe(BaseModel):
    """Du texte courant. Le bloc par défaut."""

    model_config = ConfigDict(frozen=True)

    type: Literal["paragraphe"]
    texte: str = Field(min_length=1)


class BlocIntertitre(BaseModel):
    """Un titre de section à l'intérieur de l'article."""

    model_config = ConfigDict(frozen=True)

    type: Literal["intertitre"]
    texte: str = Field(min_length=1)


class BlocListe(BaseModel):
    """Une énumération de points."""

    model_config = ConfigDict(frozen=True)

    type: Literal["liste"]
    points: list[str] = Field(min_length=1)


class BlocEncadre(BaseModel):
    """Ce qu'il ne faut pas manquer : un piège, un seuil, un montant."""

    model_config = ConfigDict(frozen=True)

    type: Literal["encadre"]
    titre: str = Field(min_length=1)
    texte: str = Field(min_length=1)


#: Union discriminée sur `type` : Pydantic choisit la bonne classe sans essayer
#: les quatre à la suite, et le message d'erreur désigne le bloc fautif.
Bloc = Annotated[
    BlocParagraphe | BlocIntertitre | BlocListe | BlocEncadre,
    Field(discriminator="type"),
]


class AppelAction(BaseModel):
    """L'action proposée au bas d'un article. Un libellé, une destination."""

    model_config = ConfigDict(frozen=True)

    texte: str = Field(min_length=1)
    href: str = Field(min_length=1)


class Article(BaseModel):
    """Un article du blog.

    Les trois textes courts ne font pas double emploi, et les confondre abîme le
    partage :

    * `titre` — dans la page, dans l'onglet, et en tête de la vignette partagée ;
    * `accroche` — une phrase, écrite pour être lue **seule** dans un fil Facebook
      ou une conversation WhatsApp, là où personne ne verra le reste ;
    * `resume` — deux ou trois lignes, pour la vignette du sommaire, où le lecteur
      compare plusieurs articles entre eux.
    """

    model_config = ConfigDict(frozen=True)

    #: Identifiant d'adresse. Il entre dans l'URL, donc dans les liens déjà
    #: partagés : le changer casse tout lien existant vers l'article.
    slug: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    rubrique: Rubrique
    titre: str = Field(min_length=1)
    accroche: str = Field(min_length=1)
    resume: str = Field(min_length=1)

    #: Date de la publication d'origine, jamais celle de la saisie. C'est elle qui
    #: permet au lecteur de juger de la fraîcheur d'une règle fiscale, et au
    #: cabinet de repérer ce qu'il faut réviser.
    date: date

    #: Chemin de l'illustration, servi par le site. Paysage et 1200 px de large au
    #: minimum : c'est la vignette de partage, et en dessous de 600 px Facebook
    #: renonce à la grande carte pour une imagette carrée sans effet.
    image: str = Field(min_length=1)

    minutes: int = Field(gt=0, le=60)
    mots_cles: list[str] = Field(default_factory=list)
    corps: list[Bloc] = Field(min_length=1)

    #: Capture d'origine dans Docs/publications-facebook-blog/, pour pouvoir
    #: toujours remonter à la publication qui a servi de matière.
    source: str | None = None
    appel: AppelAction | None = None


class Annonce(BaseModel):
    """Le message du bandeau qui court sur toutes les pages du site."""

    model_config = ConfigDict(frozen=True)

    #: Identifiant de mémorisation de la fermeture, côté navigateur. ⚠️ Changer le
    #: texte SANS changer la clé laisse l'ancienne annonce fermée chez ceux qui
    #: l'avaient fermée : ils ne verront jamais la nouvelle.
    cle: str = Field(min_length=1)
    etiquette: str = Field(min_length=1, max_length=24)
    texte: str = Field(min_length=1)
    lien: str = Field(min_length=1)
    libelle_lien: str = Field(min_length=1)

    #: Premier et dernier jour d'affichage, inclus. Une promotion de fin d'année
    #: encore visible en mars fait plus de tort que pas d'annonce du tout : elle
    #: apprend au visiteur que le site n'est pas tenu.
    du: date
    au: date

    @model_validator(mode="after")
    def _fenetre_coherente(self) -> Annonce:
        if self.au < self.du:
            raise ValueError(f"fenêtre d'annonce incohérente : {self.au} < {self.du}")
        return self

    def est_active(self, a_la_date: date) -> bool:
        """Bornes incluses des deux côtés : une annonce du 1er au 31 s'affiche le 31."""
        return self.du <= a_la_date <= self.au


class Institution(BaseModel):
    """Une institution dans le cadre de laquelle le cabinet exerce.

    ⚠️ Ce ne sont **pas des partenaires commerciaux**, et le libellé de la section
    du site le dit. Afficher le logo d'une entreprise privée sous le mot
    « partenaire » affirmerait une relation contractuelle. La DGI reçoit les
    déclarations, la CNPS les déclarations sociales, l'ONECCA est l'ordre dont
    relèvent les experts-comptables, l'OHADA fixe le droit comptable applicable :
    le dire est un fait vérifiable, pas une caution empruntée.
    """

    model_config = ConfigDict(frozen=True)

    cle: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    logo: str = Field(min_length=1)
    site: str = Field(min_length=1)

    #: Retire une bande parasite au bas du fichier, en pourcentage de sa hauteur.
    #: Le logo de la CNPS est distribué avec un bandeau de certifications qui n'a
    #: rien à faire dans un ruban.
    rognage_bas: int | None = Field(default=None, ge=0, le=90)
