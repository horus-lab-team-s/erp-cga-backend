"""La veille : ce qui dort est dit une fois, et une seule.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE NE FERME RIEN, ET NE RELANCE PERSONNE

Il dépose un événement interne par dossier qui dort, et marque le dossier comme
signalé. Il ne classe pas sans suite, il ne réaffecte pas, il n'écrit pas au
prospect.

⚠️ **C'est la règle du chantier, appliquée ici** : *l'état suit ce que le geste
prouve, pas ce qu'il espère.* Un prospect qui ne répond pas n'a rien refusé. Le
silence prouve qu'aucun échange n'a eu lieu, pas qu'il n'y en aura jamais.
Classer sans suite sur un silence ferait décider la machine à la place du centre,
et sur la seule information qu'elle n'a pas.

Le classement existe, et c'est une route : un humain le décide, avec un motif.

POURQUOI CETTE VEILLE EXISTE, ET CE QU'ELLE RÉPARE

`charge_par_responsable` exclut les états terminaux, en promettant qu'« un ancien
collaborateur productif ne paraîtra pas surchargé pour toujours ». La promesse
était vraie et inatteignable : **rien ne mettait jamais un dossier à l'état
terminal `SANS_SUITE`**. Mesuré sur une base réelle, trois prospects muets pèsent
encore trois dossiers huit mois plus tard, et l'affectation, qui choisit le moins
chargé, punit indéfiniment celui qui les a reçus.

Une garde que rien ne déclenche protège moins qu'une garde absente : l'absente,
on la voit.

LE DÉPÔT ET LE MARQUAGE PARTAGENT LA TRANSACTION DE L'APPELANT

Comme pour la relance, et pour la même raison. Marquer sans déposer perdrait
l'alerte pour toujours, puisque le marqueur empêche le passage suivant de
recommencer. Déposer sans marquer redéposerait à chaque passage.

⚠️ **La boîte d'envoi ne dédoublonne pas.** Redéposer le même identifiant
réécrit la ligne et remet `publie_le` à `None` : l'alerte repartirait. Le
marqueur n'est donc pas une commodité, c'est ce qui tient la propriété.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)
from app.orchestration.boite_d_envoi import EvenementSortant, deposer

__all__ = [
    "NOM_EN_SOUFFRANCE",
    "RapportDeVeille",
    "veiller_les_dossiers",
]

#: Le nom métier, tel qu'un exploitant le lira dans un journal à trois heures du
#: matin. Voir `balayage_de_relance` pour la même discipline.
NOM_EN_SOUFFRANCE = "DossierEnSouffrance"


class BoiteOuDeposer(Protocol):
    def deposer(self, evenement: EvenementSortant) -> None: ...


class DepotDossiers(Protocol):
    """Ce que la veille exige du dépôt, et rien de plus.

    ⚠️ `ouverts` est filtré par état, et la veille l'appelle **une fois par
    état sous veille** plutôt qu'une fois pour tout charger. Le port le
    documentait déjà ainsi : « l'ordonnanceur balaye état par état, avec un
    délai propre à chacun ». C'est ce balayage-là, écrit deux chantiers après
    la phrase qui l'annonçait.
    """

    def ouverts(self, *, etat: EtatDossier | None = None) -> list[DossierCommercial]: ...

    def enregistrer(self, dossier: DossierCommercial) -> None: ...


class RapportDeVeille(BaseModel):
    """Ce qu'un passage a vu. Sans aucune donnée de prospect."""

    model_config = ConfigDict(frozen=True)

    #: Les dossiers ouverts dans un état sous veille. Sert à distinguer « rien
    #: ne dort » de « rien n'a été lu », qui n'appellent pas la même enquête.
    examines: int = 0
    #: Ceux qui dorment et viennent d'être signalés.
    signales: tuple[str, ...] = ()
    #: Ceux qui dorment et l'avaient déjà été. Comptés, jamais redéposés : c'est
    #: ce nombre qui dit qu'une alerte n'a pas été traitée, et il grossit tant
    #: que personne n'agit.
    deja_signales: int = 0

    @property
    def resume(self) -> str:
        return (
            f"{self.examines} dossier(s) sous veille examiné(s), "
            f"{len(self.signales)} signalé(s), "
            f"{self.deja_signales} déjà signalé(s) et toujours immobile(s)"
        )


def veiller_les_dossiers(
    delais: Mapping[EtatDossier, timedelta],
    *,
    dossiers: DepotDossiers,
    boite: BoiteOuDeposer,
    a_l_instant: datetime,
    identifiant: Callable[[str], str],
) -> RapportDeVeille:
    """Signale ce qui dort, marque ce qui a été signalé, et rend compte.

    ─────────────────────────────────────────────────────────────────────────────
    L'ORDRE DES ÉTATS EST CELUI DU RÉFÉRENTIEL, ET IL NE COMPTE PAS

    Aucun dossier n'est dans deux états à la fois : les listes sont disjointes,
    et rien ne peut donc être signalé deux fois dans un même passage.

    ⚠️ **Un état sans délai n'est pas parcouru du tout.** Ce n'est pas une
    optimisation : parcourir tous les ouverts pour n'en garder qu'une fraction
    ferait grossir chaque passage avec le portefeuille entier, y compris les
    dossiers payés de l'année, que `ouverts` exclut déjà mais qu'une requête
    non filtrée finirait par ramener le jour où quelqu'un « simplifie ».
    ─────────────────────────────────────────────────────────────────────────────
    """
    examines = 0
    signales: list[str] = []
    deja = 0

    for etat in delais:
        for dossier in dossiers.ouverts(etat=etat):
            examines += 1
            if not dossier.en_souffrance(a_l_instant, delais):
                continue
            if dossier.signale_le is not None:
                deja += 1
                continue
            deposer(
                boite,
                # ⚠️ L'identifiant porte l'état **et** l'instant d'entrée dans
                # cet état. Un dossier qui repasse par `AFFECTÉE` après une
                # reprise est une seconde attente, donc un second fait : leur
                # donner le même identifiant ferait perdre l'un des deux, la
                # boîte réécrivant la ligne existante.
                identifiant(f"{dossier.reference}-{etat.value}-{_horodate(dossier)}"),
                NOM_EN_SOUFFRANCE,
                # La clé d'ordre est la référence seule : deux alertes du même
                # dossier se suivent, celles de dossiers différents ne
                # s'attendent pas.
                dossier.reference,
                _charge(dossier, a_l_instant, delais[etat]),
                a_l_instant,
            )
            dossiers.enregistrer(dossier.signaler(a_l_instant))
            signales.append(dossier.reference)

    return RapportDeVeille(
        examines=examines, signales=tuple(signales), deja_signales=deja
    )


def _horodate(dossier: DossierCommercial) -> str:
    """La seconde près suffit : un dossier n'entre pas deux fois dans le même
    état dans la même seconde, et une microseconde dans un identifiant se
    recopie mal dans un ticket."""
    return dossier.depuis_le.strftime("%Y%m%d%H%M%S")


def _charge(
    dossier: DossierCommercial, a_l_instant: datetime, delai: timedelta
) -> dict[str, Any]:
    """Ce que l'alerte transporte. Le strict nécessaire pour agir.

    ⚠️ **Ni le nom du prospect, ni son numéro, ni son message.** L'alerte est
    interne mais elle passe par une file, des journaux et des sauvegardes, qui
    la recopient tous. Celui qui la traitera ouvrira le dossier.

    `responsable` fait exception, et c'est le seul champ qui n'est pas
    strictement nécessaire à l'identification : sans lui, l'alerte devrait être
    ouverte pour savoir à qui elle s'adresse, et une alerte qu'il faut ouvrir
    pour savoir si elle vous concerne n'est pas lue.
    """
    return {
        "dossier": dossier.reference,
        "etat": dossier.etat.value,
        "responsable": dossier.responsable,
        "depuis_le": dossier.depuis_le.isoformat(),
        # En heures, et arrondi : une durée sérialisée par Python se relit mal
        # dans un journal, et la fraction d'heure n'informe personne.
        "immobile_depuis_heures": int(
            (a_l_instant - dossier.depuis_le).total_seconds() // 3600
        ),
        "delai_heures": int(delai.total_seconds() // 3600),
    }
