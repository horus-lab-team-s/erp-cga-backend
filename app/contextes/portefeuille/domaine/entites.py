"""Entités du contexte B · Portefeuille adhérents.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CE CONTEXTE MODÉLISE

Qui sont les adhérents du Centre, depuis quand, sous quel régime, rattachés à
quel centre des impôts, et sur quels exercices. C'est peu de choses, et pourtant
c'est de là que dépendent presque tous les autres contextes :

* **D · Conformité** demande le régime pour savoir si une règle de déductibilité
  s'applique ;
* **E · Comptabilité** le demande pour savoir si l'écriture porte une ligne de
  TVA ;
* **F · Obligations** demande le rattachement pour calculer les échéances, et
  l'exercice pour la DSF ;
* **H · Clôture** demande l'adhésion pour savoir si l'abattement CGA est ouvert.

Aucun de ces contextes ne peut être écrit correctement sans B. C'est pourquoi il
ne dépend de rien, sinon du socle : c'est une feuille que tout le monde lit.

CE QU'IL NE MODÉLISE PAS

Ni l'identité des utilisateurs, ni leurs droits — cela relève de K · Transverse.
Ni le cloisonnement multi-tenant : c'est un filtre appliqué dans la couche de
persistance, transverse à tous les contextes, et non une dépendance métier. Voir
`Docs/architecture/10-flux-fonctionnels.md` § 5.

Aucun taux, aucun seuil : les seuils de régime vivent au référentiel et se lisent
à une date. Ce module reçoit la valeur, il ne la connaît pas.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.portefeuille.domaine.temporel import (
    MotifChangement,
    Periode,
    resoudre,
    verifier_succession,
)

__all__ = [
    "Adhesion",
    "Associe",
    "CentreRattachement",
    "Dirigeant",
    "Entreprise",
    "Exercice",
    "FormeJuridique",
    "MandatDeclaratif",
    "RegimeFiscal",
    "StatutIntrouvable",
    "StatutRattachement",
    "StatutRegime",
    "Tiers",
    "TypeTiers",
]

#: Durée maximale d'un exercice. Le premier peut excéder douze mois — c'est même
#: le cas le plus fréquent — mais au-delà de deux ans, c'est une erreur de saisie.
DUREE_MAXIMALE_EXERCICE = timedelta(days=730)


class StatutIntrouvable(LookupError):
    """Aucun statut ne couvre la date demandée.

    Volontairement une erreur, jamais une valeur par défaut : supposer le régime
    du réel parce qu'on ne sait pas produirait un contrôle faux, et il serait faux
    en silence. C'est la même discipline que `AucuneVersionApplicable` au
    référentiel normatif.
    """


class FormeJuridique(StrEnum):
    """Les formes proposées par le cabinet, plus celles rencontrées au portefeuille."""

    ETS = "ETS"
    SARLU = "SARLU"
    SARL = "SARL"
    SAS = "SAS"
    SA = "SA"
    SCI = "SCI"
    GIE = "GIE"
    ASSOCIATION = "ASSOCIATION"
    PERSONNE_PHYSIQUE = "PERSONNE_PHYSIQUE"


class RegimeFiscal(StrEnum):
    """Le régime d'imposition.

    ⚠️ NOMENCLATURE À CONFIRMER. Le document de cadrage emploie « Impôt Général
    Synthétique » pour le régime des petites entreprises, et c'est ce vocabulaire
    que le référentiel et le code ont repris. Or les régimes camerounais ont été
    remaniés par des lois de finances successives, et les appellations en usage —
    impôt libératoire, régime simplifié, régime du réel — ne se recouvrent pas
    exactement.

    Obtenir du fiscaliste la liste officielle et ses seuils est la question n° 12
    du dossier de questions ouvertes. Tant qu'elle n'est pas tranchée, cette
    énumération est provisoire — mais le **patron** du statut daté, lui, ne
    changera pas.

    ⚠️ Le contexte D · Conformité porte sa propre énumération `RegimeEmetteur`,
    avec les mêmes valeurs. La duplication est **délibérée** : chaque contexte
    borné garde son vocabulaire, et la correspondance se fait à la composition.
    Les fusionner créerait une dépendance de D vers B qui n'a aucune raison
    d'être — le moteur de conformité évalue une règle sur une facture, il n'a pas
    besoin de connaître le portefeuille.
    """

    IGS = "IGS"
    REEL = "REEL"


class CentreRattachement(StrEnum):
    """Le centre des impôts dont relève l'entreprise, du plus petit au plus grand.

    Ce n'est pas un détail administratif : il détermine l'interlocuteur, les
    modalités de dépôt, et surtout les **dates limites**, qui sont échelonnées par
    catégorie. C'est pourquoi il est historisé plutôt que porté en attribut.
    """

    CDI = "CDI"
    CIME = "CIME"
    DGE = "DGE"


class TypeTiers(StrEnum):
    CLIENT = "CLIENT"
    FOURNISSEUR = "FOURNISSEUR"
    LES_DEUX = "LES_DEUX"


# ── Les statuts historisés ───────────────────────────────────────────────────────


class StatutRegime(Periode):
    """Le régime, sur un intervalle. Jamais un attribut."""

    model_config = ConfigDict(frozen=True)
    regime: RegimeFiscal

    @property
    def assujettie_tva(self) -> bool:
        """Nommer le sens métier plutôt que comparer l'énumération partout.

        Le jour où la nomenclature des régimes changera — et elle changera —,
        c'est cette propriété qu'on corrigera, pas les dix contextes appelants.
        """
        return self.regime is RegimeFiscal.REEL


class StatutRattachement(Periode):
    model_config = ConfigDict(frozen=True)
    centre: CentreRattachement


class Adhesion(Periode):
    """L'appartenance au Centre, sur un intervalle.

    ⚠️ Sa date d'effet est **fiscalement porteuse** : c'est elle qui ouvre
    l'abattement sur le bénéfice et l'exonération temporaire de patente. Une
    adhésion antidatée, ou un trou bouché par commodité, accorderait
    rétroactivement des avantages que l'entreprise n'avait pas — et exposerait le
    Centre autant que l'adhérent.
    """

    model_config = ConfigDict(frozen=True)
    numero: str | None = None


class MandatDeclaratif(Periode):
    """Ce que le Centre fait au nom de l'adhérent.

    Objet **juridique**, pas administratif : c'est lui qui autorise le cabinet à
    déposer une déclaration à la place de l'entreprise, et donc lui qui engage sa
    responsabilité. Déposer hors mandat n'est pas une négligence de procédure,
    c'est agir sans qualité.
    """

    model_config = ConfigDict(frozen=True)

    #: Codes des obligations couvertes. Un mandat peut être partiel : la TVA sans
    #: la paie, par exemple.
    obligations: list[str] = Field(min_length=1)
    signe_le: date
    signe_par: str = Field(min_length=1)

    def couvre_obligation(self, code: str, a_la_date: date) -> bool:
        return self.couvre(a_la_date) and code in self.obligations


# ── L'exercice ───────────────────────────────────────────────────────────────────


class Exercice(BaseModel):
    """La période couverte par les comptes annuels.

    ⚠️ NI ANNÉE CIVILE, NI DOUZE MOIS. Deux cas cassent toute présomption, et le
    dossier de vision les cite parmi les huit pièges :

    * l'**exercice décalé** — clos au 30 juin, par exemple. La DSF n'est alors pas
      due le 15 mars mais soixante-quinze jours après *sa* clôture ;
    * le **premier exercice long** — une entreprise créée en octobre clôture
      généralement au 31 décembre de l'année suivante, soit quinze mois.

    Toute proratisation — amortissements, acomptes, appréciation des seuils —
    doit employer la durée réelle, jamais douze mois par défaut.

    La clôture est **incluse** dans l'exercice, à la différence des périodes de
    statut : c'est l'usage comptable, un exercice va du 1er janvier au 31 décembre.
    """

    model_config = ConfigDict(frozen=True)

    libelle: str = Field(min_length=1)
    ouverture: date
    cloture: date
    clos: bool = False

    @model_validator(mode="after")
    def _duree_plausible(self) -> Exercice:
        if self.cloture <= self.ouverture:
            raise ValueError(
                f"exercice {self.libelle} : clôture {self.cloture} antérieure ou égale à "
                f"l'ouverture {self.ouverture}."
            )
        if self.duree > DUREE_MAXIMALE_EXERCICE:
            raise ValueError(
                f"exercice {self.libelle} : {self.duree.days} jours. Un premier exercice "
                "peut dépasser douze mois, mais au-delà de deux ans c'est une erreur de "
                "saisie."
            )
        return self

    @property
    def duree(self) -> timedelta:
        """Durée réelle, clôture incluse."""
        return self.cloture - self.ouverture + timedelta(days=1)

    @property
    def duree_mois(self) -> Decimal:
        """Durée en mois, pour les proratisations. Approchée à partir des jours."""
        return (Decimal(self.duree.days) / Decimal("30.4375")).quantize(Decimal("0.01"))

    @property
    def est_long(self) -> bool:
        return self.duree > timedelta(days=366)

    @property
    def est_annee_civile(self) -> bool:
        return (
            self.ouverture.month == 1
            and self.ouverture.day == 1
            and self.cloture.month == 12
            and self.cloture.day == 31
        )

    def contient(self, jour: date) -> bool:
        return self.ouverture <= jour <= self.cloture

    def prorata(self, jours: int) -> Decimal:
        """La fraction d'exercice que représentent ces jours.

        Sert aux amortissements et aux seuils rapportés à l'année. Rapportée à la
        durée **réelle**, pas à 365 : sur un premier exercice de quinze mois, la
        différence est de 25 %.
        """
        return (Decimal(jours) / Decimal(self.duree.days)).quantize(Decimal("0.0001"))


# ── Les personnes ────────────────────────────────────────────────────────────────


class Dirigeant(BaseModel):
    model_config = ConfigDict(frozen=True)

    nom: str = Field(min_length=1)
    qualite: str = Field(min_length=1)
    depuis: date
    jusqu_a: date | None = None
    niu: str | None = None

    @property
    def en_fonction(self) -> bool:
        return self.jusqu_a is None


class Associe(BaseModel):
    model_config = ConfigDict(frozen=True)

    nom: str = Field(min_length=1)
    parts: Decimal = Field(gt=0)
    depuis: date
    niu: str | None = None


class Tiers(BaseModel):
    """Un client ou un fournisseur de l'adhérent.

    Modélisé comme une entité à part entière, et non comme un simple libellé sur
    une facture : ce sont **eux** qui émettent les factures contrôlées par le
    contexte D. Connaître leur NIU et leur régime, c'est pouvoir vérifier qu'un
    fournisseur est bien assujetti avant d'accepter la TVA qu'il facture.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    denomination: str = Field(min_length=1)
    type: TypeTiers
    niu: str | None = None
    rccm: str | None = None
    regime: RegimeFiscal | None = None
    etranger: bool = False


# ── L'entreprise ─────────────────────────────────────────────────────────────────


class Entreprise(BaseModel):
    """Un adhérent, ou un dossier suivi par le Centre.

    Le NIU sert de clé naturelle : c'est l'identité fiscale, et c'est sur elle que
    l'administration raisonne.
    """

    model_config = ConfigDict(frozen=True)

    niu: str = Field(min_length=1)
    denomination: str = Field(min_length=1)
    forme_juridique: FormeJuridique
    date_creation: date

    rccm: str | None = None
    capital: Decimal | None = Field(default=None, ge=0)
    activite: str | None = None
    siege: str | None = None
    etablissements: list[str] = Field(default_factory=list)

    #: Statuts historisés et **continus** : une entreprise relève d'un régime et
    #: d'un centre à chaque instant de son existence.
    regimes: list[StatutRegime] = Field(min_length=1)
    rattachements: list[StatutRattachement] = Field(min_length=1)

    #: Historisés mais **discontinus** : on peut ne jamais avoir adhéré, ou avoir
    #: adhéré, résilié, puis ré-adhéré.
    adhesions: list[Adhesion] = Field(default_factory=list)
    mandats: list[MandatDeclaratif] = Field(default_factory=list)

    exercices: list[Exercice] = Field(default_factory=list)
    dirigeants: list[Dirigeant] = Field(default_factory=list)
    associes: list[Associe] = Field(default_factory=list)
    tiers: list[Tiers] = Field(default_factory=list)

    # ── Invariants ──────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _statuts_coherents(self) -> Entreprise:
        verifier_succession(self.regimes, quoi=f"{self.niu} · régimes", continue_=True)
        verifier_succession(
            self.rattachements, quoi=f"{self.niu} · rattachements", continue_=True
        )
        verifier_succession(self.adhesions, quoi=f"{self.niu} · adhésions", continue_=False)
        verifier_succession(self.mandats, quoi=f"{self.niu} · mandats", continue_=False)
        return self

    @model_validator(mode="after")
    def _rien_avant_la_creation(self) -> Entreprise:
        for libelle, periodes in (
            ("régime", self.regimes),
            ("rattachement", self.rattachements),
            ("adhésion", self.adhesions),
        ):
            for periode in periodes:
                if periode.debut < self.date_creation:
                    raise ValueError(
                        f"{self.niu} : un {libelle} commence le {periode.debut}, avant la "
                        f"création de l'entreprise le {self.date_creation}."
                    )
        return self

    @model_validator(mode="after")
    def _exercices_contigus(self) -> Entreprise:
        """Les exercices se suivent sans trou ni chevauchement.

        Un trou entre deux exercices signifierait une période de l'existence de
        l'entreprise non couverte par des comptes — ce qui n'existe pas.
        """
        triees = sorted(self.exercices, key=lambda e: e.ouverture)
        for precedent, suivant in zip(triees, triees[1:], strict=False):
            attendu = precedent.cloture + timedelta(days=1)
            if suivant.ouverture != attendu:
                raise ValueError(
                    f"{self.niu} : l'exercice {suivant.libelle} ouvre le "
                    f"{suivant.ouverture} alors que le précédent se clôt le "
                    f"{precedent.cloture}. Les exercices se suivent sans trou ni "
                    "chevauchement."
                )
        return self

    # ── Lectures à une date — la seule façon d'interroger un statut ─────────

    def regime_au(self, a_la_date: date) -> RegimeFiscal:
        """Le régime en vigueur à cette date.

        C'est **la** méthode que D et E appellent. Une facture de 2024 se contrôle
        avec le régime de 2024, jamais avec celui d'aujourd'hui.
        """
        statut = resoudre(self.regimes, a_la_date)
        if statut is None:
            raise StatutIntrouvable(
                f"{self.niu} : aucun régime connu au {a_la_date}. Périodes déclarées : "
                + ", ".join(f"[{s.debut} → {s.fin or 'en cours'}[" for s in self.regimes)
            )
        return statut.regime  # type: ignore[attr-defined]

    def assujettie_tva_au(self, a_la_date: date) -> bool:
        """L'entreprise récupère-t-elle la TVA à cette date ?

        Formuler la question ainsi plutôt que de comparer une énumération partout
        est délibéré : le sens métier survivra au changement de nomenclature.
        """
        return self.regime_au(a_la_date) is RegimeFiscal.REEL

    def rattachement_au(self, a_la_date: date) -> CentreRattachement:
        statut = resoudre(self.rattachements, a_la_date)
        if statut is None:
            raise StatutIntrouvable(
                f"{self.niu} : aucun centre de rattachement connu au {a_la_date}."
            )
        return statut.centre  # type: ignore[attr-defined]

    def adhesion_au(self, a_la_date: date) -> Adhesion | None:
        """L'adhésion en cours, ou `None`. L'absence est une réponse, pas une erreur."""
        statut = resoudre(self.adhesions, a_la_date)
        return statut  # type: ignore[return-value]

    def est_adherente_au(self, a_la_date: date) -> bool:
        return self.adhesion_au(a_la_date) is not None

    def adherente_sur_toute_la_periode(self, du: date, au_inclus: date) -> bool:
        """Une **même** adhésion couvre-t-elle chaque jour, du premier au dernier ?

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **C'EST ICI QUE CETTE QUESTION SE RÉPOND, ET NULLE PART AILLEURS.**

        Le passage fiscal le disait depuis le premier jour : « la question se
        répond en comparant des intervalles, et c'est au portefeuille de le
        faire ». Il ne le faisait pas, et la liasse recevait la réponse **de la
        requête**, avec `True` par défaut.

        ⚠️ **UNE MÊME ADHÉSION, PAS UNE SUITE D'ADHÉSIONS.** Une résiliation au
        30 juin suivie d'une réadhésion au 1er juillet laisse chaque jour couvert,
        et c'est pourtant une rupture : deux contrats, deux dates d'effet, et
        l'abattement ouvert par la seconde ne remonte pas à la première. Exiger
        une seule période est la lecture prudente, et la prudence a un sens
        précis ici : c'est le Centre qui atteste l'adhésion à l'administration.

        **Question ouverte (Q17).** Une adhésion prise en cours d'exercice
        ouvre-t-elle l'abattement pour cet exercice-là ? Cette méthode répond non.
        Si le cabinet établit le contraire, c'est cette méthode, et elle seule,
        qui changera.
        ─────────────────────────────────────────────────────────────────────────
        """
        return any(
            adhesion.debut <= du and (adhesion.fin is None or adhesion.fin > au_inclus)
            for adhesion in self.adhesions
        )

    def mandatee_pour(self, code_obligation: str, a_la_date: date) -> bool:
        """Le Centre a-t-il qualité pour déposer cette déclaration ce jour-là ?"""
        return any(m.couvre_obligation(code_obligation, a_la_date) for m in self.mandats)

    def exercice_couvrant(self, jour: date) -> Exercice:
        for exercice in self.exercices:
            if exercice.contient(jour):
                return exercice
        raise StatutIntrouvable(
            f"{self.niu} : aucun exercice ne couvre le {jour}. Exercices déclarés : "
            + (
                ", ".join(f"{e.libelle} [{e.ouverture} → {e.cloture}]" for e in self.exercices)
                or "aucun"
            )
        )

    # ── Lectures d'ensemble ─────────────────────────────────────────────────

    @property
    def regime_courant(self) -> StatutRegime:
        """Le statut ouvert. Il en existe toujours exactement un."""
        return sorted(self.regimes, key=lambda s: s.debut)[-1]

    @property
    def premier_exercice(self) -> Exercice | None:
        return min(self.exercices, key=lambda e: e.ouverture, default=None)

    def exercices_clos_depuis(self, depuis: date) -> int:
        """Combien d'exercices se sont entièrement écoulés depuis cette date.

        Sert au décompte de la période probatoire. Un exercice n'est compté que
        s'il a **commencé après** la date et qu'il est clos : un exercice à cheval
        sur l'événement ne compte pas, sinon on abrégerait la période probatoire
        d'un exercice complet.
        """
        return sum(1 for e in self.exercices if e.ouverture >= depuis and e.clos)


def statut_initial(
    regime: RegimeFiscal, a_la_creation: date, *, precision: str = ""
) -> StatutRegime:
    """Le premier statut d'une entreprise qu'on vient de créer.

    Fourni pour éviter que chaque appelant réinvente le motif `CREATION` — et
    surtout pour que le contexte I, quand il convertira un dossier de création en
    adhérent, n'ait pas à connaître la mécanique des périodes.

    ⚠️ **`precision` a été ajouté parce que son absence rendait la fonction
    inutilisable.** Le contexte I, celui-là même que cette docstring nomme,
    construisait son `StatutRegime` à la main : il avait besoin de dire de quel
    dossier de création le régime venait, et la fonction ne le permettait pas.

    Une aide qu'on ne peut pas employer n'aide personne, et elle est pire qu'une
    absence d'aide : elle laisse croire que le motif `CREATION` est posé en un
    seul endroit alors qu'il l'est en deux.
    """
    return StatutRegime(
        debut=a_la_creation,
        regime=regime,
        motif=MotifChangement.CREATION,
        precision=precision,
    )
