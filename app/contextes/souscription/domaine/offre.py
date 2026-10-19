"""L'offre commerciale du cabinet, et son barème daté.

─────────────────────────────────────────────────────────────────────────────────
UN TARIF EST UNE FONCTION DU TEMPS, COMME UNE VALEUR LÉGALE

C'est la même discipline qu'au référentiel normatif, appliquée à des montants qui
ne sont pas légaux mais commerciaux. Un `Tarif` porte `[du, au[`, et on ne lit
jamais « le prix » sans dire à quelle date.

La raison est concrète : un devis établi en mars et payé en juin doit être
honoré au prix de mars. Sans barème daté, l'augmentation du 1er avril
s'appliquerait rétroactivement à un client qui a déjà reçu son devis — et
personne ne verrait qu'elle s'est appliquée.

CE BARÈME N'EST PAS UNE VALEUR LÉGALE, ET LA DISTINCTION IMPORTE

Les **honoraires** du cabinet lui appartiennent : il les fixe, il les change, il
n'a de compte à rendre à personne. Ils vivent donc ici.

Les **frais officiels** d'une création d'entreprise — caisse du guichet unique,
droits d'enregistrement, RCCM, journal officiel, timbres — sont des valeurs
légales. Elles relèvent du contexte A · Référentiel normatif, datées et
justifiées, et le principe n° 1 du projet interdit de les écrire ailleurs.

⚠️ **Conséquence assumée : la création d'entreprise n'est pas souscriptible en
ligne.** Ses frais officiels ne sont pas au référentiel, et l'on sait déjà que les
montants employés par la vitrine sont approximatifs — le commentaire de
`bareme-creation.ts` signale un écart entre le barème du dessin, agrégé en trois
lignes, et la proforma réelle du cabinet en huit lignes. Encaisser un montant
qu'on sait faux serait pire que ne rien encaisser.

La création produit donc une **demande de devis**, pas une souscription payable.
C'est exactement ce que la vitrine annonce déjà : « Le devis définitif vous est
confirmé après examen de votre projet, sans frais de dossier. »

LES FORMULES SONT DES TRANCHES, ET LA DERNIÈRE N'A PAS DE PRIX

Jusqu'à 50 millions de chiffre d'affaires, 12 500 par mois. De 50 à 100 millions,
35 000. Au-delà, **sur devis** — et ce n'est pas une paresse de barème : au-delà
de 100 millions l'entreprise sort du régime du centre agréé, la prestation change
de nature, et un prix affiché serait un prix faux.

Une formule sans montant ne peut donc pas être souscrite en ligne. Le modèle le
dit plutôt que de laisser un zéro traîner.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

__all__ = [
    "FixationRefusee",
    "Formule",
    "NatureService",
    "Periodicite",
    "Service",
    "ServiceIntrouvable",
    "StatutTarif",
    "Tarif",
    "TarifFixe",
    "TarifIndisponible",
    "appliquer_les_fixations",
    "fixer",
    "service_par_code",
]


class NatureService(StrEnum):
    """Ce que le client achète, et cela commande tout le reste.

    `ABONNEMENT` — une prestation continue, réglée par période. La souscription
    ouvre un accès qui dure tant que le règlement suit.

    `ANNUEL` — payé d'avance pour douze mois, comme la domiciliation. Ce n'est pas
    un abonnement mensuel douze fois : il n'y a qu'un encaissement, et l'échéance
    est la date anniversaire.

    `PONCTUEL` — une chose est faite, elle est payée, la relation s'arrête. Aucun
    accès permanent n'est ouvert.

    `SUR_ETUDE` — le prix dépend d'un examen du dossier. Aucun encaissement en
    ligne : voir l'en-tête.
    """

    ABONNEMENT = "ABONNEMENT"
    ANNUEL = "ANNUEL"
    PONCTUEL = "PONCTUEL"
    SUR_ETUDE = "SUR_ETUDE"


class Periodicite(StrEnum):
    MENSUELLE = "MENSUELLE"
    ANNUELLE = "ANNUELLE"
    UNIQUE = "UNIQUE"


class ServiceIntrouvable(LookupError):
    """Le code de service demandé n'existe pas au catalogue."""


class TarifIndisponible(LookupError):
    """Aucun tarif ne couvre cette date.

    Lève plutôt que de rendre le dernier connu. Un barème dont une période n'est
    pas couverte est une erreur de saisie, et facturer « le tarif le plus proche »
    reviendrait à inventer un prix.
    """


class StatutTarif(StrEnum):
    """Qui a décidé de ce prix.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE PRIX EST UNE DÉCISION DU GÉRANT, PAS UNE DONNÉE DU CODE.

    Les montants du catalogue viennent des textes de la maquette de la vitrine.
    Personne au cabinet ne les a fixés : ils illustrent le mécanisme. Le
    référentiel de tarification le dit de ses propres valeurs (`A_VALIDER`), et
    le moteur de chiffrage « rend un intervalle, jamais un prix » : c'est un
    humain qui décide.

    Le catalogue public, lui, les affichait comme fermes ET en encaissait le
    paiement. Un visiteur pouvait régler 12 500 F par mois un tarif qu'aucun
    responsable n'avait arrêté. Il n'apparaissait pas non plus sur la fiche de
    contreseing : il échappait à toute validation.
    ─────────────────────────────────────────────────────────────────────────────
    """

    #: Le montant vient du catalogue de démonstration. Il s'affiche comme
    #: indicatif, et ne se paie pas en ligne.
    A_VALIDER = "A_VALIDER"
    #: Le gérant l'a arrêté, à une date. Il se paie.
    FIXE = "FIXE"


class FixationRefusee(ValueError):
    """Un tarif qu'on ne peut pas fixer tel quel."""


class Tarif(BaseModel):
    """Un montant en vigueur sur un intervalle `[du, au[`.

    Borne haute exclue, comme partout ailleurs dans le système : sans elle, le
    dernier jour d'un barème appartiendrait à deux tarifs à la fois.
    """

    model_config = ConfigDict(frozen=True)

    #: En FCFA. `None` = pas de prix affiché, prestation sur étude.
    montant: Decimal | None = Field(default=None, ge=0)
    du: date
    au: date | None = None

    #: Ce qui a motivé le tarif ou son changement. Un prix sans histoire ne se
    #: défend pas trois ans plus tard devant un client qui demande pourquoi.
    motif: str | None = None

    #: À valider tant que le gérant ne l'a pas arrêté : voir `StatutTarif`.
    statut: StatutTarif = StatutTarif.A_VALIDER
    #: Le compte qui l'a fixé, et quand. Un prix fixé sans nom ne se défend pas.
    #:
    #: ⚠️ EXCLU DE LA SÉRIALISATION : le catalogue est servi par une route
    #: PUBLIQUE, et l'identifiant du compte du gérant n'a rien à faire sur le
    #: téléphone d'un visiteur. La console le lit sur l'objet, pas dans le JSON.
    fixe_par: str | None = Field(default=None, exclude=True)
    fixe_le: datetime | None = None

    @model_validator(mode="after")
    def _bornes(self) -> Tarif:
        if self.au is not None and self.au <= self.du:
            raise ValueError(
                f"tarif incohérent : fin {self.au} antérieure ou égale au début {self.du}"
            )
        if self.statut is StatutTarif.FIXE and (
            self.montant is None or self.fixe_par is None or self.fixe_le is None
        ):
            raise ValueError(
                "un tarif fixé porte un montant, le compte qui l'a fixé et l'instant : "
                "un prix « fixé » par personne n'engage personne."
            )
        return self

    @property
    def fixe(self) -> bool:
        return self.statut is StatutTarif.FIXE

    def couvre(self, a_la_date: date) -> bool:
        return self.du <= a_la_date and (self.au is None or a_la_date < self.au)


class Formule(BaseModel):
    """Une tranche de l'offre d'adhésion, bornée par le chiffre d'affaires.

    Les bornes sont `[plancher, plafond[` en FCFA. `plafond` à `None` signifie
    « au-delà », et c'est la tranche qui n'a pas de prix.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    plancher: Decimal = Field(ge=0)
    plafond: Decimal | None = None
    tarifs: list[Tarif] = Field(min_length=1)

    #: Ce que la formule comprend. Affiché tel quel : un client qui compare deux
    #: formules compare des listes, pas des prix.
    inclus: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bornes(self) -> Formule:
        if self.plafond is not None and self.plafond <= self.plancher:
            raise ValueError(
                f"formule {self.code} : plafond {self.plafond} inférieur ou égal au "
                f"plancher {self.plancher}"
            )
        return self

    @computed_field
    @property
    def souscriptible_en_ligne(self) -> bool:
        """Faux dès qu'un tarif est sans montant.

        Sérialisé, parce que c'est le champ qui commande l'affichage du bouton
        « Souscrire ». Le front ne doit pas déduire cette règle d'un montant nul —
        il la lirait un jour comme « gratuit ».

        ⚠️ 19 octobre 2026 : LE TARIF OUVERT, et non tout l'historique. Une fixation
        clôt le tarif sans montant et ouvre le prix arrêté ; à lire tous les tarifs, un
        service qui avait un jour été « à fixer » restait invendable pour toujours.
        """
        return _tarif_ouvert_chiffre(self.tarifs)

    def couvre(self, chiffre_affaires: Decimal) -> bool:
        if chiffre_affaires < self.plancher:
            return False
        return self.plafond is None or chiffre_affaires < self.plafond

    def tarif_au(self, a_la_date: date) -> Tarif:
        for tarif in self.tarifs:
            if tarif.couvre(a_la_date):
                return tarif
        raise TarifIndisponible(
            f"aucun tarif de la formule {self.code} ne couvre le {a_la_date}. "
            "Un barème qui ne couvre pas une date est une erreur de saisie : "
            "facturer le tarif le plus proche reviendrait à inventer un prix."
        )


class Service(BaseModel):
    """Une prestation du catalogue.

    Deux formes coexistent, et c'est voulu : un service peut avoir un tarif
    unique — la domiciliation, la formation —, ou se décliner en formules bornées
    par le chiffre d'affaires — l'adhésion. Les forcer dans une seule forme
    obligerait à inventer une formule unique pour la domiciliation, ce qui
    afficherait une tranche de chiffre d'affaires sans objet.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    nature: NatureService
    periodicite: Periodicite

    #: L'un ou l'autre, jamais les deux.
    tarifs: list[Tarif] = Field(default_factory=list)
    formules: list[Formule] = Field(default_factory=list)

    #: Unité affichée : « par mois », « pour douze mois », « par personne ».
    unite: str | None = None
    resume: str | None = None
    inclus: list[str] = Field(default_factory=list)

    #: Vrai quand le service ouvre un accès à un dossier — donc quand le paiement
    #: doit déclencher la création d'un compte. Une formation payée n'ouvre aucun
    #: dossier, et lui en ouvrir un donnerait accès à une comptabilité.
    ouvre_un_dossier: bool = False

    @model_validator(mode="after")
    def _une_seule_forme(self) -> Service:
        if bool(self.tarifs) == bool(self.formules):
            raise ValueError(
                f"service {self.code} : renseigner soit des tarifs, soit des formules, "
                "jamais les deux ni aucun. Deux barèmes concurrents sur un même service "
                "produiraient deux prix, et rien ne dirait lequel fait foi."
            )
        return self

    @computed_field
    @property
    def souscriptible_en_ligne(self) -> bool:
        """Voir l'en-tête du module : la création d'entreprise ne l'est pas, parce
        que ses frais officiels ne sont pas au référentiel."""
        if self.nature == NatureService.SUR_ETUDE:
            return False
        if self.formules:
            return any(f.souscriptible_en_ligne for f in self.formules)
        return _tarif_ouvert_chiffre(self.tarifs)

    def tarif_au(self, a_la_date: date) -> Tarif:
        """Le tarif d'un service à barème unique."""
        if self.formules:
            raise ValueError(
                f"service {self.code} : décliné en formules, le tarif dépend du chiffre "
                "d'affaires. Passer par formule_pour() d'abord."
            )
        for tarif in self.tarifs:
            if tarif.couvre(a_la_date):
                return tarif
        raise TarifIndisponible(
            f"aucun tarif du service {self.code} ne couvre le {a_la_date}"
        )

    def formule_pour(self, chiffre_affaires: Decimal) -> Formule:
        """La tranche qui couvre ce chiffre d'affaires."""
        if not self.formules:
            raise ValueError(f"service {self.code} : pas de formules, tarif unique")
        for formule in self.formules:
            if formule.couvre(chiffre_affaires):
                return formule
        raise ValueError(
            f"aucune formule de {self.code} ne couvre un chiffre d'affaires de "
            f"{chiffre_affaires}. Les tranches du barème laissent un trou : "
            "c'est une erreur de saisie, pas un cas limite."
        )


def service_par_code(services: Sequence[Service], code: str) -> Service:
    """Lève plutôt que de rendre `None` : un code inconnu vient d'une faute de
    frappe ou d'un lien périmé, et dans les deux cas il faut le dire."""
    for service in services:
        if service.code == code:
            return service
    raise ServiceIntrouvable(
        f"service « {code} » inconnu. Codes au catalogue : "
        f"{', '.join(sorted(s.code for s in services))}"
    )


class TarifFixe(BaseModel):
    """La décision du gérant : tel prix, pour telle prestation, à partir de tel jour.

    Conservée telle quelle, et rejouée sur le catalogue à chaque lecture : c'est
    l'historique des décisions qui fait foi, pas un catalogue réécrit. On sait
    ainsi qui a fixé chaque prix, quand, et à partir de quand il s'applique.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    service: str = Field(min_length=1)
    #: La tranche, pour un service à formules. `None` pour un service à tarif
    #: unique.
    formule: str | None = None
    montant: Decimal = Field(gt=0)
    a_partir_du: date
    motif: str = Field(min_length=3)
    fixe_par: str = Field(min_length=1)
    fixe_le: datetime


def _tarif_ouvert_chiffre(tarifs: Sequence[Tarif]) -> bool:
    """Le tarif sans date de fin porte un montant (voir `Formule.souscriptible_en_ligne`)."""
    ouverts = [t for t in tarifs if t.au is None]
    return bool(ouverts) and all(t.montant is not None for t in ouverts)


def _ouvrir_une_periode(tarifs: list[Tarif], fixation: TarifFixe) -> list[Tarif]:
    """Coupe le barème au jour de la fixation, et pose le nouveau prix après.

    ⚠️ ON NE RÉÉCRIT JAMAIS UN PRIX PASSÉ. Le tarif en cours s'arrête la veille
    de la fixation ; ceux qui commencent après sont remplacés. Réécrire la
    période en cours changerait le prix d'un devis déjà émis, que le client a
    peut-être déjà montré à son associé.
    """
    jour = fixation.a_partir_du
    garde: list[Tarif] = []
    for t in tarifs:
        if t.du >= jour:
            continue  # remplacé par la fixation
        if t.au is None or t.au > jour:
            garde.append(t.model_copy(update={"au": jour}))
        else:
            garde.append(t)
    garde.append(
        Tarif(
            montant=fixation.montant,
            du=jour,
            au=None,
            motif=fixation.motif,
            statut=StatutTarif.FIXE,
            fixe_par=fixation.fixe_par,
            fixe_le=fixation.fixe_le,
        )
    )
    return sorted(garde, key=lambda t: t.du)


def fixer(service: Service, fixation: TarifFixe) -> Service:
    """Applique une décision du gérant à un service du catalogue."""
    if fixation.service != service.code:
        raise FixationRefusee(
            f"la fixation porte sur {fixation.service}, pas sur {service.code}"
        )
    if service.nature is NatureService.SUR_ETUDE:
        raise FixationRefusee(
            f"{service.code} se chiffre sur étude, dossier par dossier : un prix unique "
            "n'y a pas de sens. Le cabinet l'arrête sur la proposition du client."
        )
    if service.formules:
        if fixation.formule is None:
            raise FixationRefusee(
                f"{service.code} se décline en formules : préciser laquelle."
            )
        codes = [f.code for f in service.formules]
        if fixation.formule not in codes:
            raise FixationRefusee(
                f"{service.code} n'a pas de formule {fixation.formule} "
                f"(formules : {', '.join(codes)})"
            )
        formules = [
            f.model_copy(update={"tarifs": _ouvrir_une_periode(f.tarifs, fixation)})
            if f.code == fixation.formule
            else f
            for f in service.formules
        ]
        return service.model_copy(update={"formules": formules})
    if fixation.formule is not None:
        raise FixationRefusee(
            f"{service.code} a un tarif unique : il n'a pas de formule "
            f"{fixation.formule}."
        )
    return service.model_copy(
        update={"tarifs": _ouvrir_une_periode(service.tarifs, fixation)}
    )


def appliquer_les_fixations(
    catalogue: Sequence[Service], fixations: Sequence[TarifFixe]
) -> list[Service]:
    """Le catalogue tel que le gérant l'a arrêté, décision après décision.

    ⚠️ Dans l'ordre où elles ont été PRISES (`fixe_le`), pas dans celui de leur
    date d'effet : si le gérant fixe d'abord mars, puis corrige février, les deux
    décisions doivent tenir, et c'est leur succession qui le garantit.
    """
    services = list(catalogue)
    for f in sorted(fixations, key=lambda x: x.fixe_le):
        services = [fixer(s, f) if s.code == f.service else s for s in services]
    return services

