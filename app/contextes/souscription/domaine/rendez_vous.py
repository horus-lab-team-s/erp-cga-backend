"""Le rendez-vous de création : pour qui ne sait pas remplir le formulaire seul (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE L'UTILISATEUR A DEMANDÉ

« Des clients n'auront pas suffisamment de connaissance pour remplir le formulaire. Il doit
y avoir un sélecteur visible qui, à la sélection, fixe un rendez-vous en présentiel ou en
ligne, avec les heures, pénalisé en cas de non-respect. Il paie comme l'autre les frais de
dossier. » Et : « par défaut, ressors des propositions, que le responsable de la création
peut fermer : c'est lui qui gère la qualification des dossiers de création. »

CE QUI EST ICI

* `AgendaDeCreation` : les plages hebdomadaires, la durée d'un rendez-vous, les modes, et ce
  que le responsable a FERMÉ (un créneau, un jour). `AGENDA_PAR_DEFAUT` est la proposition
  d'ouverture : du lundi au vendredi, 9 h–12 h et 14 h–16 h, une heure, les deux modes ;
* `RendezVous` : un créneau pris par un dossier, puis HONORÉ ou MANQUÉ (constaté par le
  cabinet), ou ANNULÉ ;
* `creneaux_libres` : ce que le site propose.

⚠️ LES HEURES SONT CELLES DE DOUALA, SANS FUSEAU. Le client et le cabinet lisent « mardi
10 h » ; l'horloge du produit est en UTC (`partage/horloge`). On compare donc à
`a_douala(maintenant())`, jamais à `maintenant()`.

⚠️ LA PÉNALITÉ N'EST PAS ICI. Son montant est une décision du super administrateur, rangée
avec les frais de dossier (`RegleDesVersements.penalite_rendez_vous`). Ici, on dit seulement
qu'un rendez-vous a été manqué.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AGENDA_PAR_DEFAUT",
    "AgendaDeCreation",
    "CreneauRefuse",
    "EtatRendezVous",
    "ModeRendezVous",
    "PlageHoraire",
    "RendezVous",
    "creneaux_libres",
    "verifier_le_creneau",
]


class CreneauRefuse(ValueError):
    """Le créneau demandé n'est pas proposé, ou il est déjà pris."""


class ModeRendezVous(StrEnum):
    PRESENTIEL = "PRESENTIEL"
    EN_LIGNE = "EN_LIGNE"


class EtatRendezVous(StrEnum):
    PREVU = "PREVU"
    HONORE = "HONORE"
    #: Le client n'est pas venu : la pénalité fixée par le super administrateur est due.
    MANQUE = "MANQUE"
    ANNULE = "ANNULE"


class PlageHoraire(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: 0 = lundi … 6 = dimanche.
    jour: int = Field(ge=0, le=6)
    debut: time
    fin: time

    @model_validator(mode="after")
    def _dans_l_ordre(self) -> PlageHoraire:
        if self.debut >= self.fin:
            raise ValueError("une plage commence avant de finir.")
        return self


class AgendaDeCreation(BaseModel):
    """Ce que le responsable de la création ouvre et ferme. Une ligne par locataire."""

    model_config = ConfigDict(frozen=True)

    plages: tuple[PlageHoraire, ...]
    duree_minutes: int = Field(default=60, ge=15, le=240)
    modes: tuple[ModeRendezVous, ...] = (ModeRendezVous.PRESENTIEL, ModeRendezVous.EN_LIGNE)
    #: Des créneaux précis fermés (leur heure de début, à Douala).
    creneaux_fermes: tuple[datetime, ...] = ()
    #: Des journées entières fermées (congé, jour férié, séminaire).
    jours_fermes: tuple[date, ...] = ()
    #: On ne réserve pas pour dans une heure : le responsable doit pouvoir s'organiser.
    delai_minimal_heures: int = Field(default=24, ge=0, le=168)
    #: Jusqu'où le site propose des créneaux.
    horizon_jours: int = Field(default=14, ge=1, le=90)
    fixe_par: str = Field(min_length=1)
    fixe_le: datetime

    def ouvert_pour(self, mode: ModeRendezVous) -> bool:
        return mode in self.modes


def _plages_par_defaut() -> tuple[PlageHoraire, ...]:
    plages: list[PlageHoraire] = []
    for jour in range(5):  # du lundi au vendredi
        plages.append(PlageHoraire(jour=jour, debut=time(9), fin=time(12)))
        plages.append(PlageHoraire(jour=jour, debut=time(14), fin=time(16)))
    return tuple(plages)


#: La proposition d'ouverture, tant que le responsable n'a rien fixé. ⚠️ Une PROPOSITION :
#: il ferme ce qui ne lui convient pas, il ne part pas d'une page vide.
AGENDA_PAR_DEFAUT = AgendaDeCreation(
    plages=_plages_par_defaut(),
    fixe_par="systeme",
    fixe_le=datetime(2026, 11, 9),
)


class RendezVous(BaseModel):
    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    #: La référence du dossier commercial.
    dossier: str = Field(min_length=1)
    mode: ModeRendezVous
    #: À Douala, sans fuseau.
    debut: datetime
    fin: datetime
    etat: EtatRendezVous = EtatRendezVous.PREVU
    pris_le: datetime
    #: Pour un rendez-vous en ligne : le lien de visioconférence, posé par le cabinet.
    lien: str | None = None
    constate_le: datetime | None = None
    constate_par: str | None = None
    motif: str | None = None
    #: Le lien de visioconférence est parti chez le client.
    lien_envoye_le: datetime | None = None
    #: Le rappel de la veille est parti (garde du rejeu du travail de rappel).
    rappel_envoye_le: datetime | None = None

    @property
    def occupe_le_creneau(self) -> bool:
        """Un rendez-vous annulé libère son créneau ; les autres le gardent."""
        return self.etat is not EtatRendezVous.ANNULE


def _creneaux_proposes(agenda: AgendaDeCreation, a_douala: datetime) -> list[datetime]:
    pas = timedelta(minutes=agenda.duree_minutes)
    au_plus_tot = a_douala + timedelta(hours=agenda.delai_minimal_heures)
    fermes = set(agenda.creneaux_fermes)
    proposes: list[datetime] = []
    for decalage in range(agenda.horizon_jours + 1):
        jour = (a_douala + timedelta(days=decalage)).date()
        if jour in agenda.jours_fermes:
            continue
        for plage in agenda.plages:
            if plage.jour != jour.weekday():
                continue
            debut = datetime.combine(jour, plage.debut)
            limite = datetime.combine(jour, plage.fin)
            while debut + pas <= limite:
                if debut >= au_plus_tot and debut not in fermes:
                    proposes.append(debut)
                debut += pas
    return sorted(proposes)


def creneaux_libres(
    agenda: AgendaDeCreation, pris: Iterable[RendezVous], a_douala: datetime
) -> list[datetime]:
    """Les heures de début proposées et non prises, à Douala."""
    occupes = {r.debut for r in pris if r.occupe_le_creneau}
    return [c for c in _creneaux_proposes(agenda, a_douala) if c not in occupes]


def verifier_le_creneau(
    agenda: AgendaDeCreation,
    pris: Iterable[RendezVous],
    debut: datetime,
    mode: ModeRendezVous,
    a_douala: datetime,
) -> datetime:
    """La fin du créneau, ou `CreneauRefuse` avec une phrase que le client comprend."""
    if not agenda.ouvert_pour(mode):
        raise CreneauRefuse("ce mode de rendez-vous n'est pas proposé pour l'instant.")
    debut = debut.replace(tzinfo=None, second=0, microsecond=0)
    if debut not in creneaux_libres(agenda, pris, a_douala):
        raise CreneauRefuse(
            "ce créneau n'est plus disponible : choisissez-en un autre dans la liste."
        )
    return debut + timedelta(minutes=agenda.duree_minutes)
