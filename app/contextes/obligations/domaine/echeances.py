"""Le calcul d'une échéance, et celui des pénalités de retard.

─────────────────────────────────────────────────────────────────────────────────
LA RÈGLE QUI COMMANDE TOUT CE MODULE

**Une date limite se calcule, elle ne se stocke pas.**

Le 15 mars n'est pas une constante : c'est le résultat de « clôture + 75 jours »
appliqué à un exercice civil. Pour un exercice clos au 30 juin, l'échéance tombe
en septembre. Et le dépôt est par ailleurs **échelonné selon le centre de
rattachement** — un dossier à la DGE et un dossier au CDI n'ont pas la même date.

Stocker des dates figées dans une table est la faute de conception la plus
courante d'un module d'échéancier. Elle ne se voit pas tant que tous les adhérents
clôturent au 31 décembre, et elle se révèle au premier exercice décalé — c'est-à-
dire au moment où l'on a le plus de dossiers et le moins de temps.

CE QUE CE MODULE NE CONNAÎT PAS

Aucun jour limite, aucun délai, aucun taux de pénalité. Tout cela vit au
référentiel normatif, daté, et lui est passé par le `TypeObligation`. Le module
sait *comment* calculer, jamais *avec quelles valeurs*.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.contextes.portefeuille.contrats import CentreRattachement, RegimeFiscal
from app.contextes.transverse.contrats import Portail

__all__ = [
    "Penalite",
    "Periodicite",
    "TypeObligation",
    "calculer_echeance",
    "calculer_penalite",
    "mois_de_retard",
]


class Periodicite(StrEnum):
    """À quel rythme l'obligation revient, et donc comment son échéance se calcule."""

    MENSUELLE = "MENSUELLE"
    TRIMESTRIELLE = "TRIMESTRIELLE"

    #: Rattachée à l'exercice comptable : l'échéance court à partir de la clôture.
    #: C'est le cas de la DSF, et c'est celui qui casse toute présomption d'année
    #: civile.
    ANNUELLE_EXERCICE = "ANNUELLE_EXERCICE"

    #: Rattachée à l'année civile, quelle que soit la clôture de l'exercice.
    #: La patente, par exemple.
    ANNUELLE_CIVILE = "ANNUELLE_CIVILE"

    PONCTUELLE = "PONCTUELLE"


class TypeObligation(BaseModel):
    """Ce qu'est une obligation déclarative, indépendamment de tout dossier.

    ⚠️ SOURCE DE CES DONNÉES. Le dossier d'architecture place `TypeObligation` au
    contexte A · Référentiel, au même titre qu'un paramètre légal — la périodicité
    et la formule d'échéance viennent du CGI et changent avec lui.

    Ce contexte déclare malgré tout la forme dont il a besoin, et un port va la
    chercher. C'est la même discipline que pour le plan comptable du contexte E :
    le métier exprime son besoin, l'adaptateur sait où le satisfaire.
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    #: ⚠️ Le guichet où l'obligation se dépose (pas 59). **Sans valeur par défaut.**
    #:
    #: Un accusé CNPS ne prouve rien devant la DGI, et l'inverse. Un défaut
    #: « DGI » aurait fait consigner une cotisation sociale au mauvais guichet sans
    #: que rien ne le signale. Chaque obligation du catalogue dit donc le sien, et le
    #: constat de dépôt le reprend : l'appelant ne le choisit pas.
    portail: Portail
    periodicite: Periodicite

    #: Jour du mois **suivant** la période, pour les obligations périodiques.
    #: Vient du référentiel : `TVA_JOUR_LIMITE_DEPOT`, `CNPS_JOUR_LIMITE_PAIEMENT`.
    jour_limite: int | None = Field(default=None, ge=1, le=31)

    #: Délai après la clôture, pour les obligations rattachées à l'exercice.
    #: Vient du référentiel : `DSF_DELAI_JOURS_APRES_CLOTURE`.
    delai_jours_apres_cloture: int | None = Field(default=None, ge=0)

    #: Jour et mois fixes, pour les obligations rattachées à l'année civile.
    jour_civil: int | None = Field(default=None, ge=1, le=31)
    mois_civil: int | None = Field(default=None, ge=1, le=12)

    #: ⚠️ L'ÉCHELONNEMENT PAR CENTRE. Le dépôt de la DSF est historiquement
    #: échelonné selon que l'entreprise relève du CDI, du CIME ou de la DGE.
    #: Le décalage est exprimé en jours ajoutés à l'échéance de base.
    #:
    #: La note du paramètre `DSF_DELAI_JOURS_APRES_CLOTURE` au référentiel signale
    #: précisément qu'il « doit devenir un paramètre par centre, pas une
    #: constante ». C'est ici que cela se matérialise — les valeurs, elles,
    #: restent à obtenir du fiscaliste.
    decalage_par_centre: dict[CentreRattachement, int] = Field(default_factory=dict)

    #: À qui l'obligation s'applique. `None` = à tous les régimes.
    regimes_concernes: list[RegimeFiscal] | None = None

    #: Conditions tenant à la situation du dossier.
    exige_assujettissement_tva: bool = False
    exige_salaries: bool = False

    #: ⚠️ L'obligation est-elle due même sans opération de la période ?
    #:
    #: C'est le cas de la déclaration de TVA : une entreprise assujettie qui n'a
    #: rien vendu ni acheté doit déposer une déclaration portant la mention
    #: « néant ». L'obligation naît de l'assujettissement, pas de l'activité.
    #:
    #: Presque aucun dirigeant ne le sait, et c'est une source de pénalités
    #: absurdes : on est sanctionné pour n'avoir pas déclaré qu'on n'avait rien à
    #: déclarer. Un échéancier alimenté par les pièces reçues serait donc
    #: structurellement faux — il faut le générer à partir du **profil**.
    declaration_neant_due: bool = False

    #: ⚠️ L'OBLIGATION EST-ELLE EXIGIBLE AVANT LA FIN DE SA PÉRIODE ?
    #:
    #: La règle générale est qu'on ne déclare pas une période avant qu'elle ne
    #: soit écoulée. La contribution des patentes y échappe : elle est due **en
    #: début d'année, pour l'année en cours**. Ce n'est pas une déclaration d'un
    #: passé, c'est un droit d'exercer payé d'avance.
    #:
    #: Confondre les deux familles conduit à l'un de ces deux défauts : soit
    #: l'échéancier refuse de produire la patente — ce que le premier catalogue
    #: réel a fait apparaître —, soit il accepte des déclarations antidatées.
    payable_d_avance: bool = False

    @model_validator(mode="after")
    def _formule_complete(self) -> TypeObligation:
        if self.periodicite in (Periodicite.MENSUELLE, Periodicite.TRIMESTRIELLE):
            if self.jour_limite is None:
                raise ValueError(
                    f"{self.code} : une obligation périodique doit porter son jour limite. "
                    "Sans lui, aucune échéance ne peut être calculée."
                )
        elif self.periodicite is Periodicite.ANNUELLE_EXERCICE:
            if self.delai_jours_apres_cloture is None:
                raise ValueError(
                    f"{self.code} : une obligation rattachée à l'exercice doit porter son "
                    "délai après clôture. Le 15 mars n'est pas une constante."
                )
        elif self.periodicite is Periodicite.ANNUELLE_CIVILE:
            if self.jour_civil is None or self.mois_civil is None:
                raise ValueError(
                    f"{self.code} : une obligation civile doit porter son jour et son mois."
                )
        return self

    def concerne(
        self,
        regime: RegimeFiscal,
        *,
        assujettie_tva: bool,
        a_des_salaries: bool,
    ) -> bool:
        """L'obligation s'applique-t-elle à ce profil ?

        ⚠️ Le moteur ne raisonne **jamais** « régime synthétique, donc rien à
        faire ». Le caractère libératoire d'un forfait libère de l'impôt sur le
        bénéfice, et de lui seul : les cotisations sociales, les retenues à la
        source et les taxes locales subsistent intégralement.

        C'est le quatrième des huit pièges du dossier de vision, et le plus
        coûteux : un échéancier qui l'ignorerait générerait des retards sur toute
        une part du portefeuille — et la responsabilité en incomberait au Centre.
        """
        if self.regimes_concernes is not None and regime not in self.regimes_concernes:
            return False
        if self.exige_assujettissement_tva and not assujettie_tva:
            return False
        if self.exige_salaries and not a_des_salaries:
            return False
        return True


def _jour_valide(annee: int, mois: int, jour: int) -> date:
    """Ramène le jour au dernier du mois quand il n'existe pas.

    Un jour limite au 31 n'a pas de sens en février. Plutôt que de lever — ce qui
    bloquerait un échéancier entier pour un mois court —, on retient le dernier
    jour du mois, qui est le comportement administratif usuel.
    """
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, min(jour, dernier))


def _mois_suivant(jour: date) -> tuple[int, int]:
    return (jour.year + 1, 1) if jour.month == 12 else (jour.year, jour.month + 1)


def calculer_echeance(
    type_obligation: TypeObligation,
    *,
    fin_de_periode: date,
    centre: CentreRattachement,
    cloture_exercice: date | None = None,
) -> date:
    """La date limite de cette obligation, pour cette période et ce dossier.

    `fin_de_periode` est le dernier jour couvert par la déclaration — le 31 juillet
    pour la TVA de juillet. `cloture_exercice` n'est requis que pour les
    obligations rattachées à l'exercice.

    Le décalage par centre est **ajouté** à l'échéance de base. Il vaut zéro pour
    les centres non déclarés, ce qui est le comportement voulu : un décalage absent
    signifie « pas d'échelonnement connu », pas « échéance inconnue ».
    """
    decalage = timedelta(days=type_obligation.decalage_par_centre.get(centre, 0))

    if type_obligation.periodicite in (Periodicite.MENSUELLE, Periodicite.TRIMESTRIELLE):
        annee, mois = _mois_suivant(fin_de_periode)
        base = _jour_valide(annee, mois, type_obligation.jour_limite or 1)

    elif type_obligation.periodicite is Periodicite.ANNUELLE_EXERCICE:
        if cloture_exercice is None:
            raise ValueError(
                f"{type_obligation.code} : cette obligation court à partir de la clôture "
                "de l'exercice, qui doit être fournie. C'est précisément pourquoi la date "
                "ne peut pas être une constante."
            )
        base = cloture_exercice + timedelta(days=type_obligation.delai_jours_apres_cloture or 0)

    elif type_obligation.periodicite is Periodicite.ANNUELLE_CIVILE:
        base = _jour_valide(
            fin_de_periode.year,
            type_obligation.mois_civil or 1,
            type_obligation.jour_civil or 1,
        )

    else:  # PONCTUELLE
        base = fin_de_periode

    return base + decalage


# ── Les pénalités ────────────────────────────────────────────────────────────────


def mois_de_retard(echeance: date, a_la_date: date) -> int:
    """Nombre de mois de retard, toute fraction de mois comptant pour un mois entier.

    > **Question ouverte.** Le décompte exact — mois calendaires ou périodes de
    > trente jours, fraction comptée ou non — n'est pas au référentiel. Le
    > traitement retenu ici est le plus défavorable au contribuable, donc le plus
    > prudent pour le Centre : mieux vaut annoncer une pénalité légèrement
    > surestimée qu'une surprise au paiement. À faire confirmer.
    """
    if a_la_date <= echeance:
        return 0
    mois = (a_la_date.year - echeance.year) * 12 + (a_la_date.month - echeance.month)
    if a_la_date.day > echeance.day:
        mois += 1
    return max(mois, 1)


class Penalite(BaseModel):
    """Ce que coûte un retard, décomposé pour être explicable à l'adhérent."""

    model_config = ConfigDict(frozen=True)

    montant_du: Decimal
    jours_de_retard: int
    mois_de_retard: int
    penalite_fixe: Decimal
    majoration_mensuelle: Decimal

    @computed_field
    @property
    def total(self) -> Decimal:
        return self.penalite_fixe + self.majoration_mensuelle

    @computed_field
    @property
    def a_regler(self) -> Decimal:
        return self.montant_du + self.total


def calculer_penalite(
    montant_du: Decimal,
    *,
    echeance: date,
    a_la_date: date,
    taux_fixe: Decimal,
    taux_mensuel: Decimal,
) -> Penalite:
    """La pénalité encourue à cette date.

    Les deux taux viennent du référentiel — `PENALITE_RETARD_TAUX_FIXE` et
    `PENALITE_RETARD_TAUX_MENSUEL` —, exprimés en pourcentage et au statut à
    valider. Ce module ne les connaît pas : il les reçoit.

    Le calcul est arrondi au franc, sans décimale : le franc CFA n'en comporte pas.
    """
    mois = mois_de_retard(echeance, a_la_date)
    jours = max((a_la_date - echeance).days, 0)

    if mois == 0:
        zero = Decimal(0)
        return Penalite(
            montant_du=montant_du,
            jours_de_retard=0,
            mois_de_retard=0,
            penalite_fixe=zero,
            majoration_mensuelle=zero,
        )

    fixe = (montant_du * taux_fixe / Decimal(100)).quantize(Decimal(1))
    majoration = (montant_du * taux_mensuel * Decimal(mois) / Decimal(100)).quantize(Decimal(1))

    return Penalite(
        montant_du=montant_du,
        jours_de_retard=jours,
        mois_de_retard=mois,
        penalite_fixe=fixe,
        majoration_mensuelle=majoration,
    )
