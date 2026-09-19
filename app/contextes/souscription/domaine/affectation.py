"""À qui confier une demande, et pourquoi celui-là.

─────────────────────────────────────────────────────────────────────────────────
L'AFFECTATION N'EST PAS UN TIRAGE AU SORT, ET ELLE N'EST PAS DU CODE NON PLUS

Trois critères la déterminent : l'agence la plus proche du lieu déclaré, la
compétence requise par le service demandé, et la charge en cours du responsable.

**Ces trois critères changent tous les trimestres.** Le centre ouvrira une agence,
recrutera un spécialiste, décidera qu'un dossier de création vaut deux dossiers de
tenue. Une fonction Python les porterait très bien, et il faudrait une livraison
pour chacun de ces changements, décidés par un directeur qui n'attend pas.

Ils sont donc un **paquet de règles au référentiel**, évalué par `app/moteur/`.
C'est le troisième domaine à tourner sur ce noyau, après la conformité et la
charge, et comme les deux autres : **rien n'a été ajouté au noyau pour
l'accueillir.**

LE SUJET DE L'ÉVALUATION EST UNE CANDIDATURE, PAS UNE DEMANDE

C'est la décision de modélisation qui porte tout le reste, et elle n'est pas
évidente au premier abord.

Une règle d'affectation ne dit rien sur une demande seule. « L'agence est
éloignée » n'a de sens que pour un couple demande–responsable ; « la compétence
manque » aussi ; « la charge est excessive » aussi. Le sujet évalué est donc la
**rencontre** entre une demande et un responsable possible.

On évalue autant de candidatures qu'il y a de responsables, et l'on retient la
meilleure. Modéliser la demande comme sujet obligerait chaque règle à porter en
elle la façon de parcourir l'annuaire, ce qui n'est plus une règle mais un
programme.

DEUX NATURES DE RÈGLE, ET LA DIFFÉRENCE EST CONFIGURÉE

Certains critères **écartent** : un responsable qui n'a pas l'agrément pour la
création d'entreprise ne peut pas la traiter, quelle que soit sa disponibilité.
D'autres **pénalisent** : une agence plus loin, une charge plus lourde, c'est
moins bien sans être impossible.

`redhibitoire` est un champ de la règle, donc du référentiel. C'est délibéré :
ce qui bloque aujourd'hui sera une préférence demain, et l'inverse. Le jour où le
centre décidera qu'une charge de plus de quarante dossiers interdit toute nouvelle
affectation, ce sera un booléen dans un fichier, pas un `if` dans ce module.

⚠️ **Convention du projet : un prédicat exprime la normalité.** VRAI = rien à
signaler, ce responsable convient de ce point de vue. FAUX = le critère est
atteint, la pénalité s'ajoute ou la candidature est écartée.

La formulation surprend ici plus qu'ailleurs : on écrit « l'agence du responsable
est celle de la demande » pour pénaliser l'éloignement. C'est la même convention
que les deux autres domaines, et en changer pour celui-ci reviendrait à avoir deux
moteurs.

LE MOINS PÉNALISÉ GAGNE, ET LES ÉGALITÉS SE TRANCHENT

Deux responsables à égalité parfaite arrivent, et arriveront souvent au démarrage,
quand personne n'a encore de charge. Le départage se fait sur l'identifiant, par
ordre croissant.

Ce n'est pas un choix métier, c'est un choix de **reproductibilité** : sans lui,
le responsable désigné dépendrait de l'ordre dans lequel la base a rendu ses
lignes, et deux exécutions du même cas donneraient deux réponses. Un système dont
on ne peut pas rejouer une décision est un système dont on ne peut pas expliquer
une décision.

QUAND PERSONNE NE CONVIENT

`choisir` rend `None`. Ce n'est pas une erreur : c'est un fait, et la demande part
en file de pôle avec son alerte. Lever une exception obligerait chaque appelant à
la rattraper pour faire exactement cela, et l'un d'eux oublierait.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.referentiel.contrats import Fondement, StatutValidation
from app.moteur.consequence import Consequence, TypeConsequence, enjeu_declare
from app.moteur.evaluation import evaluer_regles
from app.moteur.faits import Fait, SchemaDeFaits, TypeFait

__all__ = [
    "CHIFFRER_LA_PENALITE",
    "SCHEMA_AFFECTATION",
    "Candidature",
    "Choix",
    "RegleDAffectation",
    "Verdict",
    "choisir",
    "choix_parmi",
    "classer",
    "evaluer_candidature",
]


# ── Ce que la candidature expose ─────────────────────────────────────────────────

SCHEMA_AFFECTATION = SchemaDeFaits(
    domaine="AFFECTATION_COMMERCIALE",
    faits=(
        Fait(
            code="service",
            type=TypeFait.TEXTE,
            libelle="Service demandé, clé du catalogue",
        ),
        Fait(
            code="region_demande",
            type=TypeFait.TEXTE,
            libelle="Région déclarée par le visiteur",
        ),
        Fait(
            code="agence_responsable",
            type=TypeFait.TEXTE,
            libelle="Agence de rattachement du responsable",
        ),
        Fait(
            code="meme_agence",
            type=TypeFait.BOOLEEN,
            libelle="Le responsable est dans l'agence de la région déclarée",
        ),
        Fait(
            code="competences",
            type=TypeFait.LISTE,
            libelle="Compétences détenues par le responsable",
        ),
        Fait(
            code="competence_requise",
            type=TypeFait.TEXTE,
            libelle="Compétence exigée par le service demandé",
        ),
        Fait(
            code="dossiers_ouverts",
            type=TypeFait.ENTIER,
            libelle="Dossiers commerciaux en cours chez ce responsable",
        ),
        Fait(
            code="charge_ponderee",
            type=TypeFait.DECIMAL,
            libelle="Dossiers ouverts pondérés par leur score de charge",
            unite="points",
        ),
        Fait(
            code="disponible",
            type=TypeFait.BOOLEEN,
            libelle="Le responsable est en mesure de prendre un dossier",
        ),
    ),
)


class Candidature(BaseModel):
    """La rencontre entre une demande et un responsable possible.

    Satisfait `app.moteur.faits.Sujet` par `faits()`, comme la facture et le
    dossier à évaluer. C'est tout ce que le moteur exige.

    ⚠️ **Ce modèle ne calcule rien qu'il ne puisse dériver de ce qu'on lui donne.**
    La proximité vient de la carte des agences, les compétences de l'annuaire, la
    charge du portefeuille. Trois sources, trois adaptateurs, et aucune n'est
    consultée ici : elles remplissent la candidature, qui se contente de la
    présenter au moteur.
    """

    model_config = ConfigDict(frozen=True)

    responsable: str = Field(min_length=1)

    service: str = Field(min_length=1)
    region_demande: str = ""
    agence_responsable: str = ""

    competences: tuple[str, ...] = ()
    #: Ce que le service demandé exige. Vide quand il n'exige rien de particulier,
    #: ce qui est le cas de la plupart des prestations courantes.
    competence_requise: str = ""

    dossiers_ouverts: int = Field(default=0, ge=0)
    charge_ponderee: Decimal = Field(default=Decimal(0), ge=0)
    disponible: bool = True

    @property
    def meme_agence(self) -> bool:
        """Dérivé, et non saisi.

        La comparaison est insensible à la casse et aux espaces : ces deux
        chaînes viennent de sources différentes, l'une d'un formulaire public et
        l'autre d'un annuaire interne, et exiger qu'elles coïncident au caractère
        près ferait échouer la proximité sur « Douala » contre « douala ».

        Une région déclarée vide ne vaut jamais la même agence : le visiteur n'a
        rien dit, et prétendre qu'il est du coin serait inventer.
        """
        region = self.region_demande.strip().casefold()
        agence = self.agence_responsable.strip().casefold()
        return bool(region) and region == agence

    def faits(self) -> Mapping[str, Any]:
        return {
            "service": self.service,
            "region_demande": self.region_demande,
            "agence_responsable": self.agence_responsable,
            "meme_agence": self.meme_agence,
            "competences": list(self.competences),
            "competence_requise": self.competence_requise,
            "dossiers_ouverts": self.dossiers_ouverts,
            "charge_ponderee": self.charge_ponderee,
            "disponible": self.disponible,
        }


# ── Les règles ───────────────────────────────────────────────────────────────────


class RegleDAffectation(BaseModel):
    """Un critère de routage.

    Satisfait `app.moteur.evaluation.RegleEvaluable`. Même discipline de
    justification que partout : un fondement, un statut, un signataire nommé quand
    la règle est validée.

    Le fondement n'est pas légal ici mais organisationnel, et le principe reste
    identique : un responsable de pôle à qui l'on refuse un dossier doit pouvoir
    lire pourquoi, et sur quelle décision du centre cela repose.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    applicable_du: date
    applicable_au: date | None = None

    #: JSONLogic. **VRAI = ce responsable convient de ce point de vue.**
    #: **FAUX = le critère est atteint, la pénalité s'ajoute.**
    predicat: dict[str, Any]

    #: Le poids du critère. Zéro est refusé : un critère qui ne pèse rien et
    #: n'écarte personne n'a aucun effet, et sa présence laisserait croire le
    #: contraire à qui lit la grille.
    penalite: int = Field(default=0, ge=0)

    #: Ce critère écarte-t-il la candidature, ou la pénalise-t-il seulement ?
    #: **Configuré, pas codé** : voir l'en-tête.
    redhibitoire: bool = False

    fondement: Fondement
    statut: StatutValidation = StatutValidation.A_VALIDER
    valide_par: str | None = None
    valide_le: date | None = None

    @model_validator(mode="after")
    def _coherence(self) -> RegleDAffectation:
        if self.applicable_au is not None and self.applicable_au <= self.applicable_du:
            raise ValueError(f"{self.code} : borne de validité incohérente")
        if not self.predicat:
            raise ValueError(f"{self.code} : prédicat vide")
        if not self.redhibitoire and self.penalite <= 0:
            raise ValueError(
                f"{self.code} : un critère qui ne pénalise rien et n'écarte personne "
                "n'a aucun effet. Sa présence dans la grille laisserait croire le "
                "contraire à qui la lit."
            )
        if self.statut is StatutValidation.VALIDE and not (
            self.valide_par and self.valide_le
        ):
            raise ValueError(
                f"{self.code} : un critère VALIDE doit porter valide_par et valide_le. "
                "Sans signataire nommé, la règle de routage n'est opposable à personne."
            )
        return self

    @property
    def consequence(self) -> Consequence:
        return Consequence(
            type=TypeConsequence.POINTS,
            libelle=self.libelle,
            unite="points",
            valeur_numerique=Decimal(self.penalite),
        )

    def en_vigueur(self, a_la_date: date) -> bool:
        if a_la_date < self.applicable_du:
            return False
        return self.applicable_au is None or a_la_date < self.applicable_au

    def concerne(self, faits: Mapping[str, Any]) -> bool:
        """Tout critère porte sur toute candidature.

        La méthode existe parce que la boucle l'exige. Qu'elle rende toujours vrai
        est un fait de ce domaine, pas un oubli : un critère qui ne vaudrait que
        pour certains services exprime cette restriction **dans son prédicat**,
        où elle se lit et se change sans code.
        """
        return True


#: Les points sont déclarés par la règle : aucun besoin de regarder la candidature.
CHIFFRER_LA_PENALITE = enjeu_declare


# ── Le verdict d'une candidature ─────────────────────────────────────────────────


class Verdict(BaseModel):
    """Ce que les règles disent d'un responsable pour cette demande."""

    model_config = ConfigDict(frozen=True)

    responsable: str
    #: Un critère rédhibitoire au moins s'est déclenché.
    ecartee: bool
    penalite: Decimal
    #: La charge du candidat, reportée telle quelle depuis la candidature.
    #:
    #: ─────────────────────────────────────────────────────────────────────────
    #: ⚠️ **ELLE SERT À DÉPARTAGER, ET C'EST INDISPENSABLE.**
    #:
    #: La grille du centre pénalise la charge **par seuils** : vingt points
    #: au-delà de soixante, quarante de plus au-delà de cent vingt. En dessous de
    #: soixante, tous les collaborateurs obtiennent donc exactement la même
    #: pénalité, et le classement les départageait sur l'identifiant seul.
    #:
    #: Mesuré : vingt dossiers déposés, quatre collaborateurs équivalents et
    #: vides, **vingt dossiers pour le premier dans l'ordre alphabétique** et
    #: aucun pour les trois autres. L'affectation « choisit le moins chargé »
    #: était fausse pour les soixante premiers points de chacun.
    #:
    #: Les seuils ne sont pas en cause : ils expriment « soutenue » et
    #: « excessive », ce qui est un jugement du centre, et le moteur ne sait pas
    #: valoriser une pénalité proportionnelle. Ce qui manquait est la règle
    #: ordinaire : *à préférence égale, on répartit.*
    #: ─────────────────────────────────────────────────────────────────────────
    charge: Decimal = Decimal(0)
    #: Les libellés des critères déclenchés, dans l'ordre des règles. Ce sont eux
    #: qui composent le motif conservé pour audit.
    motifs: tuple[str, ...] = ()
    #: Les critères rédhibitoires seuls. Sous-ensemble de `motifs`, isolé parce
    #: que c'est ce qu'on affiche en premier quand personne ne convient.
    empechements: tuple[str, ...] = ()
    #: Les règles qui n'ont pas pu être évaluées, avec leur motif. Signalées, jamais
    #: tues : un critère silencieusement absent est indiscernable d'un critère
    #: satisfait, et il ferait passer pour idéal un responsable qu'on n'a pas jugé.
    echecs: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return not self.ecartee


def evaluer_candidature(
    candidature: Candidature,
    regles: Sequence[RegleDAffectation],
    a_la_date: date,
) -> Verdict:
    """Applique la grille de routage à un couple demande–responsable.

    Aucun résolveur n'est passé : les seuils de cette grille appartiennent au
    centre et non à la loi, comme ceux de la grille de charge. Le jour où l'un
    d'eux deviendrait un paramètre daté du référentiel, c'est un résolveur qu'on
    ajouterait ici, sans toucher au noyau.
    """
    evaluation = evaluer_regles(
        regles,
        candidature.faits(),
        a_la_date,
        valorisation=CHIFFRER_LA_PENALITE,
    )

    motifs: list[str] = []
    empechements: list[str] = []
    penalite = Decimal(0)
    for declenchement in evaluation.declenchements:
        motifs.append(declenchement.regle.libelle)
        if declenchement.regle.redhibitoire:
            empechements.append(declenchement.regle.libelle)
        penalite += declenchement.enjeu or Decimal(0)

    return Verdict(
        responsable=candidature.responsable,
        ecartee=bool(empechements),
        penalite=penalite,
        charge=candidature.charge_ponderee,
        motifs=tuple(motifs),
        empechements=tuple(empechements),
        echecs=tuple(f"{e.code_regle} : {e.motif}" for e in evaluation.echecs),
    )


# ── Le choix ─────────────────────────────────────────────────────────────────────


class Choix(BaseModel):
    """Le responsable retenu, et de quoi le justifier.

    Le motif est conservé pour audit parce que la grille change : sans lui, on ne
    saura pas, dans six mois, selon quelle version un dossier a été routé, ni
    pourquoi le responsable évident ne l'avait pas eu.

    Il ne porte **pas** les verdicts des autres candidats. Ceux-ci existent aussi
    quand personne n'est retenu, c'est-à-dire précisément quand on en a le plus
    besoin ; les loger ici obligerait à fabriquer un `Choix` sans choix pour les
    transporter. Ils vivent donc à côté, rendus par `classer`.
    """

    model_config = ConfigDict(frozen=True)

    responsable: str
    penalite: Decimal
    motif: str


def classer(
    candidatures: Sequence[Candidature],
    regles: Sequence[RegleDAffectation],
    a_la_date: date,
) -> tuple[Verdict, ...]:
    """Tous les verdicts, du meilleur au moins bon.

    ─────────────────────────────────────────────────────────────────────────
    QUATRE CLÉS, ET CHACUNE RÉPOND À UNE QUESTION DIFFÉRENTE

    1. **Écartés en dernier.** Un empêchement n'est pas une pénalité très grande,
       c'est autre chose : il ne se rattrape par aucun avantage.
    2. **Pénalité croissante.** Ce que la grille du centre juge, et elle seule.
    3. **Charge croissante.** *À préférence égale, on répartit.*
    4. **Identifiant croissant.** La reproductibilité.

    ⚠️ **LA TROISIÈME CLÉ MANQUAIT, ET SON ABSENCE ANNULAIT LE CRITÈRE DE
    CHARGE.**

    La grille pénalise la charge par seuils — vingt points au-delà de soixante,
    quarante de plus au-delà de cent vingt — parce que le moteur valorise une
    pénalité fixe et non proportionnelle. En dessous de soixante, tous les
    candidats obtiennent donc la même pénalité, et le classement tombait
    directement sur l'identifiant.

    Mesuré : vingt dossiers, quatre collaborateurs équivalents et vides au
    départ, **vingt dossiers pour le premier dans l'ordre alphabétique**. Le
    document de conception écrit pourtant « l'affectation choisit le moins
    chargé », et c'était faux pour les soixante premiers points de chacun.

    Corriger dans la grille aurait demandé un seuil tous les cinq points, c'est-
    à-dire douze règles pour exprimer une proportionnalité. Le classement est le
    bon endroit : les seuils restent le jugement du centre, la répartition reste
    une propriété du tri.

    ⚠️ **LA QUATRIÈME CLÉ N'EST PAS COSMÉTIQUE NON PLUS.** Sans elle, deux
    responsables à charge et pénalité égales seraient départagés par l'ordre dans
    lequel la base a rendu ses lignes, qu'aucune base ne garantit. Deux
    exécutions du même cas donneraient deux réponses, et l'on ne pourrait ni
    rejouer ni expliquer une décision.

    Rendre le classement entier, et non le seul gagnant, est ce qui permet de
    répondre à « pourquoi pas untel ? » sans tout rejouer, et de dire ce qui
    manquait quand personne ne convenait.
    ─────────────────────────────────────────────────────────────────────────
    """
    return tuple(
        sorted(
            (evaluer_candidature(c, regles, a_la_date) for c in candidatures),
            key=lambda v: (v.ecartee, v.penalite, v.charge, v.responsable),
        )
    )


def choix_parmi(verdicts: Sequence[Verdict]) -> Choix | None:
    """Le premier éligible d'un classement déjà établi, ou `None`.

    Séparé de `classer` pour que l'appelant qui a besoin des deux ne paie pas
    deux fois l'évaluation. C'est le cas de l'affectation : elle veut le retenu
    **et** le détail des autres, y compris quand il n'y a pas de retenu.

    ⚠️ **`None` n'est pas une erreur.** C'est le cas où la demande part en file de
    pôle avec son alerte. Lever obligerait chaque appelant à rattraper pour faire
    exactement cela, et l'un d'eux oublierait.
    """
    for verdict in verdicts:
        if verdict.eligible:
            return Choix(
                responsable=verdict.responsable,
                penalite=verdict.penalite,
                motif=_motif(verdict, len(verdicts)),
            )
    return None


def choisir(
    candidatures: Sequence[Candidature],
    regles: Sequence[RegleDAffectation],
    a_la_date: date,
) -> Choix | None:
    """Classer puis retenir, en un geste. La commodité, pas le mécanisme."""
    return choix_parmi(classer(candidatures, regles, a_la_date))


def _motif(verdict: Verdict, candidats: int) -> str:
    """Une phrase lisible par un humain, pas un vidage de structure.

    C'est ce qui apparaîtra dans la fiche du dossier et dans l'audit. Un motif
    illisible est un motif que personne ne lit, et une traçabilité que personne
    ne lit ne trace rien.
    """
    parmi = f"retenu parmi {candidats} candidat{'s' if candidats > 1 else ''}"
    if not verdict.motifs:
        return f"{parmi}, aucun critère déclenché"
    reserves = ", ".join(verdict.motifs)
    return f"{parmi}, pénalité {verdict.penalite} — réserves : {reserves}"
