"""L'obligation d'un dossier, et la génération de l'échéancier.

─────────────────────────────────────────────────────────────────────────────────
DEUX DÉCISIONS DE MODÉLISATION QUI MÉRITENT D'ÊTRE DÉFENDUES

**« En retard » n'est pas un statut stocké.** Le dossier de conception liste six
états, dont `EN_RETARD`. Il n'en reste que cinq ici, parce que le retard n'est pas
une propriété de l'obligation : c'est une comparaison entre son échéance et la
date du jour. Le stocker obligerait à balayer tout le portefeuille chaque nuit
pour le tenir à jour, et une obligation deviendrait « en retard » avec un jour de
décalage selon l'heure du traitement. `en_retard(a_la_date)` répond toujours juste,
sans traitement de fond.

**L'échéancier se génère depuis le PROFIL, jamais depuis les pièces reçues.**
Une entreprise assujettie qui n'a réalisé aucune opération doit tout de même
déposer une déclaration portant la mention « néant ». L'obligation naît de
l'assujettissement, pas de l'activité. Un échéancier alimenté par les factures
reçues serait donc structurellement faux, et il le serait précisément pour les
dossiers dormants — ceux dont personne ne s'occupe, et qui accumulent les
pénalités en silence.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.contextes.obligations.domaine.echeances import (
    Periodicite,
    TypeObligation,
    calculer_echeance,
)
from app.contextes.portefeuille.contrats import Entreprise, Exercice
from app.contextes.transverse.contrats import AccuseReception, reference_de_depot
from app.partage.copie import transiter

__all__ = [
    "JALONS_RELANCE",
    "ObligationInstance",
    "Relance",
    "StatutObligation",
    "generer_echeancier",
    "relances_du_jour",
]

#: Jours par rapport à l'échéance où l'adhérent est relancé. Négatif = avant.
#: Le J+1 n'est pas une relance de politesse : c'est le moment où la pénalité
#: commence à courir, et où il faut agir dans la journée.
JALONS_RELANCE = (-15, -7, -2, 1)


class StatutObligation(StrEnum):
    """Où en est le traitement d'une obligation.

    Cinq états, et une progression stricte. `EN_RETARD` n'y figure pas : voir
    l'en-tête du module.
    """

    A_FAIRE = "A_FAIRE"
    EN_PREPARATION = "EN_PREPARATION"
    PRETE = "PRETE"
    DECLAREE = "DECLAREE"
    PAYEE = "PAYEE"


#: Ordre de progression. Une obligation n'avance jamais à reculons — sauf par
#: correction explicite, qui est un acte tracé et non un changement d'état.
_RANG = {
    StatutObligation.A_FAIRE: 0,
    StatutObligation.EN_PREPARATION: 1,
    StatutObligation.PRETE: 2,
    StatutObligation.DECLAREE: 3,
    StatutObligation.PAYEE: 4,
}


class ObligationInstance(BaseModel):
    """Une obligation concrète : ce dossier, cette période, cette échéance."""

    model_config = ConfigDict(frozen=True)

    entreprise: str = Field(min_length=1)
    code_obligation: str = Field(min_length=1)
    libelle: str = Field(min_length=1)

    periode_debut: date
    periode_fin: date
    echeance: date

    statut: StatutObligation = StatutObligation.A_FAIRE

    #: Exigible avant la fin de sa période — la patente. Voir `TypeObligation`.
    payable_d_avance: bool = False

    #: Estimé tant que la déclaration n'est pas établie. `None` quand aucune
    #: estimation n'est possible — et surtout pas zéro, qui laisserait croire
    #: qu'il n'y a rien à payer.
    montant_estime: Decimal | None = None

    #: Complétude du dossier. C'est ce que la maquette affiche à côté du montant :
    #: un montant estimé sur un dossier incomplet n'engage à rien.
    pieces_recues: int = Field(default=0, ge=0)
    pieces_attendues: int = Field(default=0, ge=0)

    declaree_le: date | None = None
    payee_le: date | None = None
    reference_depot: str | None = None

    #: L'obligation dépend de la présence de salariés, et le fichier du personnel
    #: n'a pas pu répondre (pas 56). Elle figure **par prudence** : une obligation
    #: affichée à tort se vérifie en une minute, une obligation omise se découvre au
    #: contrôle de la CNPS, avec les majorations.
    effectif_a_confirmer: bool = False

    @model_validator(mode="after")
    def _coherence(self) -> ObligationInstance:
        if self.periode_fin < self.periode_debut:
            raise ValueError(
                f"{self.code_obligation} : période incohérente, fin {self.periode_fin} "
                f"antérieure au début {self.periode_debut}."
            )
        if self.echeance < self.periode_fin and not self.payable_d_avance:
            raise ValueError(
                f"{self.code_obligation} : échéance {self.echeance} antérieure à la fin de "
                f"la période {self.periode_fin}. On ne déclare pas une période avant qu'elle "
                "ne soit écoulée — sauf obligation payable d'avance, ce que celle-ci ne "
                "déclare pas être."
            )
        if self.statut is StatutObligation.DECLAREE and self.declaree_le is None:
            raise ValueError(
                f"{self.code_obligation} : une obligation déclarée porte sa date de dépôt. "
                "C'est elle qui prouve que l'obligation a été remplie dans les délais."
            )
        if self.statut is StatutObligation.PAYEE and self.payee_le is None:
            raise ValueError(f"{self.code_obligation} : une obligation payée porte sa date.")
        return self

    # ── Lectures ────────────────────────────────────────────────────────────

    @computed_field
    @property
    def deposee(self) -> bool:
        return _RANG[self.statut] >= _RANG[StatutObligation.DECLAREE]

    @computed_field
    @property
    def complete(self) -> bool:
        return self.pieces_attendues > 0 and self.pieces_recues >= self.pieces_attendues

    @computed_field
    @property
    def completude(self) -> Decimal | None:
        if self.pieces_attendues == 0:
            return None
        return (Decimal(self.pieces_recues) / Decimal(self.pieces_attendues)).quantize(
            Decimal("0.01")
        )

    def jours_restants(self, a_la_date: date) -> int:
        """Positif avant l'échéance, négatif après."""
        return (self.echeance - a_la_date).days

    def en_retard(self, a_la_date: date) -> bool:
        """Calculé, jamais stocké — voir l'en-tête du module."""
        return not self.deposee and a_la_date > self.echeance

    # ── Transitions ─────────────────────────────────────────────────────────

    def avancer(self, statut: StatutObligation, **details) -> ObligationInstance:
        """Fait progresser l'obligation. Rend une copie ; l'originale est gelée.

        Le retour en arrière est refusé : une déclaration déposée ne redevient pas
        « à faire ». Corriger un dépôt est un acte distinct, qui laisse sa propre
        trace — comme la contre-passation en comptabilité.
        """
        if _RANG[statut] < _RANG[self.statut]:
            raise ValueError(
                f"{self.code_obligation} : on ne revient pas de {self.statut} à {statut}. "
                "Une déclaration déposée se corrige par un acte tracé, pas par un retour "
                "d'état."
            )
        return transiter(self, statut=statut, **details)


# ── La génération de l'échéancier ────────────────────────────────────────────────


def _fin_de_mois(annee: int, mois: int) -> date:
    return date(annee, mois, calendar.monthrange(annee, mois)[1])


def _periodes_mensuelles(exercice: Exercice) -> list[tuple[date, date]]:
    """Chaque mois de l'exercice, borné par l'exercice lui-même.

    Le premier mois est tronqué quand l'exercice ouvre en cours de mois : une
    entreprise créée le 15 mars ne déclare pas la première quinzaine de mars.
    """
    periodes: list[tuple[date, date]] = []
    annee, mois = exercice.ouverture.year, exercice.ouverture.month
    while True:
        debut = max(date(annee, mois, 1), exercice.ouverture)
        fin = min(_fin_de_mois(annee, mois), exercice.cloture)
        periodes.append((debut, fin))
        if fin >= exercice.cloture:
            return periodes
        annee, mois = (annee + 1, 1) if mois == 12 else (annee, mois + 1)


def _periodes_trimestrielles(exercice: Exercice) -> list[tuple[date, date]]:
    """Les trimestres civils recoupés avec l'exercice."""
    periodes: list[tuple[date, date]] = []
    annee = exercice.ouverture.year
    while True:
        for premier_mois in (1, 4, 7, 10):
            debut_t = date(annee, premier_mois, 1)
            fin_t = _fin_de_mois(annee, premier_mois + 2)
            if fin_t < exercice.ouverture or debut_t > exercice.cloture:
                continue
            periodes.append(
                (max(debut_t, exercice.ouverture), min(fin_t, exercice.cloture))
            )
        if date(annee, 12, 31) >= exercice.cloture:
            return periodes
        annee += 1


def generer_echeancier(
    entreprise: Entreprise,
    types: list[TypeObligation],
    exercice: Exercice,
    *,
    emploie_sur: Callable[[date, date], bool | None],
    accuse_de: Callable[[str], AccuseReception | None],
) -> list[ObligationInstance]:
    """Le calendrier complet d'un exercice, pour un dossier.

    ⚠️ **`emploie_sur` EST OBLIGATOIRE, ET SANS VALEUR PAR DÉFAUT.** (pas 56)

    Il remplace `a_des_salaries: bool = False`. Ce défaut rendait la CNPS et les
    retenues sur salaires invisibles de tout appelant qui ne pensait pas à le
    renseigner, c'est-à-dire de tous : trois appelants sur quatre ne l'acceptaient
    même pas, et le quatrième le recevait d'un écran qui ne l'envoyait jamais. Sans
    défaut, un appelant qui oublie la question ne compile plus.

    La réponse est demandée **pour chaque période**, jour de début et de fin
    compris : un dossier qui embauche en juin doit la CNPS de juin, pas celle de
    janvier. `None` veut dire « le personnel n'a pas pu répondre » : l'obligation
    figure alors, marquée `effectif_a_confirmer`.

    ⚠️ **`accuse_de` EST OBLIGATOIRE AUSSI, POUR LA MÊME RAISON.** (pas 58)

    L'échéancier se recalcule à chaque lecture, et il naissait toujours « à faire ».
    Rien ne lui disait qu'une obligation avait été déposée : une TVA de juillet dont
    l'accusé était consigné restait « en retard », la relance J+1 partait, et le
    pilotage comptait le retard. Le produit relançait un adhérent pour une déclaration
    qu'il avait déposée. Une obligation dont l'accusé existe est désormais
    **déclarée, à la date de l'accusé**, jamais à celle de la lecture.

    ⚠️ LE PROFIL EST ÉVALUÉ À LA FIN DE CHAQUE PÉRIODE, PAS UNE FOIS POUR TOUTES.

    C'est ce qui rend le franchissement de seuil correct. Une entreprise qui
    devient assujettie à la TVA en septembre n'a pas d'obligation de TVA pour
    janvier à août, et en a une à partir de septembre. Évaluer le régime une seule
    fois — au début ou à la fin de l'exercice — produirait dans un cas huit
    déclarations fantômes, dans l'autre quatre obligations manquées.

    L'échéance de chaque période est calculée à partir du centre de rattachement
    **en vigueur à cette période**, lui aussi historisé.
    """
    instances: list[ObligationInstance] = []

    for type_obligation in types:
        if type_obligation.periodicite is Periodicite.PONCTUELLE:
            continue  # une obligation ponctuelle naît d'un événement, pas du calendrier

        if type_obligation.periodicite is Periodicite.MENSUELLE:
            periodes = _periodes_mensuelles(exercice)
        elif type_obligation.periodicite is Periodicite.TRIMESTRIELLE:
            periodes = _periodes_trimestrielles(exercice)
        else:
            periodes = [(exercice.ouverture, exercice.cloture)]

        for debut, fin in periodes:
            regime = entreprise.regime_au(fin)
            # ⚠️ La question n'est posée que pour les obligations qui en dépendent :
            # la TVA d'un dossier n'a pas à attendre le fichier du personnel.
            emploi = emploie_sur(debut, fin) if type_obligation.exige_salaries else False
            if not type_obligation.concerne(
                regime,
                assujettie_tva=entreprise.assujettie_tva_au(fin),
                a_des_salaries=emploi is not False,
            ):
                continue

            instance = ObligationInstance(
                    entreprise=entreprise.niu,
                    code_obligation=type_obligation.code,
                    libelle=type_obligation.libelle,
                    periode_debut=debut,
                    periode_fin=fin,
                    payable_d_avance=type_obligation.payable_d_avance,
                    echeance=calculer_echeance(
                        type_obligation,
                        fin_de_periode=fin,
                        centre=entreprise.rattachement_au(fin),
                        cloture_exercice=exercice.cloture,
                    ),
                    effectif_a_confirmer=emploi is None,
                )
            accuse = accuse_de(
                reference_de_depot(entreprise.niu, type_obligation.code, debut, fin)
            )
            if accuse is not None:
                # Les mêmes champs que `constater_depot` : la date de l'accusé, jamais
                # celle du jour, parce que c'est elle qui dit si le dépôt était à temps.
                instance = instance.avancer(
                    StatutObligation.DECLAREE,
                    declaree_le=accuse.depose_le.date(),
                    reference_depot=accuse.numero,
                )
            instances.append(instance)

    return sorted(instances, key=lambda o: (o.echeance, o.code_obligation))


# ── Les relances ─────────────────────────────────────────────────────────────────


class Relance(BaseModel):
    """Une relance à émettre aujourd'hui pour une obligation."""

    model_config = ConfigDict(frozen=True)

    obligation: ObligationInstance
    jalon: int
    jours_restants: int

    @computed_field
    @property
    def urgente(self) -> bool:
        return self.jalon >= -2

    @computed_field
    @property
    def depassee(self) -> bool:
        return self.jalon > 0


def relances_du_jour(
    obligations: list[ObligationInstance],
    a_la_date: date,
    *,
    jalons: tuple[int, ...] = JALONS_RELANCE,
) -> list[Relance]:
    """Les relances à émettre ce jour-là, et elles seules.

    Une obligation déjà déposée n'est jamais relancée : c'est la première cause
    d'exaspération d'un adhérent, et elle décrédibilise toutes les relances
    suivantes.

    Le jalon J+1 relance le lendemain de l'échéance dépassée. Ce n'est pas une
    politesse : c'est le jour où la pénalité commence à courir.
    """
    relances: list[Relance] = []
    for obligation in obligations:
        if obligation.deposee:
            continue
        restants = obligation.jours_restants(a_la_date)
        for jalon in jalons:
            if restants == -jalon:
                relances.append(
                    Relance(obligation=obligation, jalon=jalon, jours_restants=restants)
                )
    return relances
