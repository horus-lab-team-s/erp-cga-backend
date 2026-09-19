"""La reprise : passer la main quand personne n'a rappelé.

─────────────────────────────────────────────────────────────────────────────────
LE SEUL GESTE QUE LA PLATEFORME S'AUTORISE SANS QU'UN HUMAIN LE DEMANDE

La veille signale et ne touche à rien. La reprise **agit** : elle désigne
quelqu'un d'autre. C'est une exception à la règle du chantier — *l'état suit ce
que le geste prouve, pas ce qu'il espère* — et elle mérite d'être justifiée
plutôt que glissée.

Elle tient parce que **le silence prouve ici exactement ce qu'on en conclut**.
Classer sans suite prétendrait savoir ce que le prospect veut, ce que personne ne
sait. Reprendre la main ne prétend rien sur le prospect : elle constate qu'aucun
échange n'a été enregistré depuis vingt-quatre heures, ce qui est un fait, et en
tire une conséquence sur **l'organisation du cabinet**, qui est son domaine.

Et le geste est borné, réversible, et sans effet de bord : le dossier reste
ouvert, rien n'est envoyé au prospect, le précédent responsable n'est pas
sanctionné, et la limite de trois reprises appartient au domaine.

CE QU'ELLE NE FAIT PAS QUAND ELLE NE PEUT PAS

Rien. Pas d'alerte, pas d'événement, pas de marqueur.

⚠️ **C'est délibéré, et c'est ce qui rend l'alerte de la veille lisible.** La
reprise remet l'ancienneté à zéro ; un dossier n'atteint donc les 48 heures de la
veille que si la reprise n'a **pas** pu aboutir. L'alerte cesse de dire « personne
n'a rappelé », qui serait du bruit, et dit « la machine a essayé de passer la main
et n'a pas pu », sur quoi un responsable de pôle peut agir.

Deux mécanismes qui alerteraient tous deux sur le même silence produiraient deux
alertes pour un fait, et celle qu'on lit finirait par être celle qu'on croit.

⚠️ **Un échec est donc réessayé à chaque passage**, et c'est voulu : « aucun
candidat disponible » est un état qui change tout seul, le jour où un compte est
activé ou une compétence enregistrée. Le coût est une lecture de l'annuaire par
dossier bloqué et par tour ; les dossiers ayant épuisé leurs trois reprises sont
écartés avant, donc l'arriéré ne grossit pas indéfiniment.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.application.affectation import (
    Candidature,
    RegleDAffectation,
    reaffecter_le_dossier,
)
from app.contextes.souscription.domaine.dossier_commercial import (
    REAFFECTATIONS_MAXIMALES,
    DossierCommercial,
    EtatDossier,
)
from app.orchestration.boite_d_envoi import EvenementSortant, deposer

__all__ = [
    "NOM_REPRIS",
    "RapportDeReprise",
    "reprendre_les_dossiers",
]

#: Le nom métier, tel qu'un exploitant le lira dans un journal.
NOM_REPRIS = "DossierRepris"


class BoiteOuDeposer(Protocol):
    def deposer(self, evenement: EvenementSortant) -> None: ...


class DepotDossiers(Protocol):
    def ouverts(self, *, etat: EtatDossier | None = None) -> list[DossierCommercial]: ...

    def enregistrer(self, dossier: DossierCommercial) -> None: ...


class RapportDeReprise(BaseModel):
    """Ce qu'un passage a produit. Sans aucune donnée de prospect."""

    model_config = ConfigDict(frozen=True)

    #: Les dossiers affectés dont l'ancienneté dépasse le délai, limite de
    #: reprises non atteinte. Distingue « rien à reprendre » de « rien lu ».
    examines: int = 0
    repris: tuple[str, ...] = ()
    #: Ceux pour lesquels aucun autre candidat ne convenait. Comptés, jamais
    #: signalés ici : c'est la veille qui les remontera à 48 heures, et le dire
    #: deux fois ferait deux alertes pour un fait.
    sans_repreneur: int = 0
    #: Ceux qui ont épuisé leurs trois reprises. Ils ne sont plus examinés du
    #: tout : ce nombre existe pour qu'on sache qu'ils sont là.
    limite_atteinte: int = 0

    @property
    def resume(self) -> str:
        return (
            f"{self.examines} dossier(s) examiné(s), {len(self.repris)} repris, "
            f"{self.sans_repreneur} sans repreneur, "
            f"{self.limite_atteinte} à la limite des reprises"
        )


def reprendre_les_dossiers(
    candidatures_pour: Callable[[str], Sequence[Candidature]],
    regles: Sequence[RegleDAffectation],
    *,
    dossiers: DepotDossiers,
    boite: BoiteOuDeposer,
    a_l_instant: datetime,
    delai_depasse: Callable[[DossierCommercial], bool],
    identifiant: Callable[[str], str],
    a_la_date: date | None = None,
) -> RapportDeReprise:
    """Passe la main sur ce qui dort, un dossier à la fois.

    ─────────────────────────────────────────────────────────────────────────────
    `candidatures_pour` EST UNE FONCTION, ET NON UNE LISTE

    Les candidats dépendent du **service demandé** : la compétence exigée n'est
    pas la même pour une création de SARL et pour une adhésion. Passer une liste
    unique obligerait l'appelant à choisir un service, donc à se tromper pour
    tous les autres.

    ⚠️ Elle est appelée **par dossier**, et la charge qu'elle lit change entre
    deux appels du même passage : un collaborateur qui vient de recevoir un
    dossier repris est plus chargé pour le suivant. C'est voulu. Monter les
    candidatures une fois pour tout le passage ferait verser trois dossiers
    d'affilée au même, précisément parce qu'il était le moins chargé au début.

    LA LIMITE EST VÉRIFIÉE ICI **ET** DANS LE DOMAINE

    Ici pour ne pas monter les candidatures d'un dossier qu'on ne peut pas
    reprendre ; là-bas parce que c'est le domaine qui garde la règle. La
    redondance est assumée : celle d'ici est une économie, celle du domaine est
    la vérité.
    ─────────────────────────────────────────────────────────────────────────────
    """
    examines = 0
    repris: list[str] = []
    sans_repreneur = 0
    limite = 0

    for dossier in dossiers.ouverts(etat=EtatDossier.AFFECTEE):
        if not delai_depasse(dossier):
            continue
        if dossier.reaffectations >= REAFFECTATIONS_MAXIMALES:
            limite += 1
            continue
        examines += 1

        precedent = dossier.responsable
        resultat = reaffecter_le_dossier(
            dossier,
            candidatures_pour(dossier.demande.service_souhaite),
            regles,
            a_l_instant,
            a_la_date=a_la_date,
        )
        if not resultat.affecte:
            sans_repreneur += 1
            continue

        dossiers.enregistrer(resultat.dossier)
        deposer(
            boite,
            # L'identité porte le rang de la reprise : deux reprises du même
            # dossier sont deux faits, et leur donner la même identité en
            # perdrait un, le dépôt étant une réécriture.
            identifiant(f"{dossier.reference}-{resultat.dossier.reaffectations}"),
            NOM_REPRIS,
            dossier.reference,
            _charge(resultat.dossier, precedent, a_l_instant),
            a_l_instant,
        )
        repris.append(dossier.reference)

    return RapportDeReprise(
        examines=examines,
        repris=tuple(repris),
        sans_repreneur=sans_repreneur,
        limite_atteinte=limite,
    )


def _charge(
    dossier: DossierCommercial, precedent: str | None, a_l_instant: datetime
) -> dict[str, Any]:
    """Ce que l'événement transporte.

    ⚠️ **Ni le nom du prospect, ni son numéro, ni son message**, pour la même
    raison qu'à la veille : un événement traverse une file, des journaux et des
    sauvegardes, qui le recopient tous.

    `precedent` y figure, et c'est le seul champ qui ne sert pas à identifier :
    sans lui, personne ne saurait de qui la main a été reprise, ce qui est
    exactement l'information qu'un responsable de pôle cherche quand il regarde
    pourquoi un dossier a changé de main trois fois.
    """
    return {
        "dossier": dossier.reference,
        "responsable": dossier.responsable,
        "precedent": precedent,
        "motif": dossier.motif_affectation,
        "reprise_numero": dossier.reaffectations,
        "reprises_restantes": REAFFECTATIONS_MAXIMALES - dossier.reaffectations,
        "reprise_le": a_l_instant.isoformat(),
    }
