"""Ce que la loi impose au Centre lui-même, mesuré au jour (10 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE

La plateforme encadrait les obligations des ADHÉRENTS (art. 118 (1)). Celles du CENTRE,
que le Code général des impôts fixe aux articles 118 et 119, n'étaient suivies nulle part :

  * art. 119 (3) : au moins cent adhérents actifs ouvrent au promoteur un abattement de
    50 % sur les revenus tirés du CGA et l'exemption des charges sur les salaires ;
  * art. 118 (5) : au moins un conseil fiscal et un expert-comptable inscrit à l'ordre, à
    raison d'un professionnel pour deux mille adhérents, sous peine de suspension ;
  * art. 118 (3) et (4) : les fourchettes des droits d'adhésion et des cotisations.

Les valeurs viennent de `Docs/referentiel/cabinet/conformite_du_centre.yaml`, sourcées.

⚠️ « ADHÉRENT ACTIF » N'EST PAS DÉFINI PAR LE TEXTE. On compte les adhésions en cours au
jour : ni une adhésion résiliée, ni une adhésion à venir. Le cabinet peut retenir une
lecture plus stricte (à jour de ses cotisations) ; l'écran dit la règle appliquée.

⚠️ LES FOURCHETTES NE SE COMPARENT À AUCUN PRIX DU CATALOGUE. L'offre « Suivi comptable et
fiscal, adhésion au centre agréé » est un prix mensuel qui couvre la tenue des comptes, les
déclarations et la DSF : ce n'est pas la cotisation de l'article 118 (4). Les comparer
serait faux. Les fourchettes sont rendues telles quelles, avec ce constat, jusqu'à ce que le
cabinet arrête quelle part de son offre constitue la cotisation.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from math import ceil
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Adherable",
    "ConformiteDuCentre",
    "Fourchette",
    "Professionnel",
    "ReglesDuCentre",
    "adherents_actifs",
    "evaluer_la_conformite",
    "professionnels_requis",
]


class Adherable(Protocol):
    """Ce que le calcul lit d'une entreprise du portefeuille, et rien de plus.

    ⚠️ Un `Protocol` plutôt que l'entité du portefeuille : un domaine n'emprunte à un autre
    contexte que ses contrats (`tests/test_architecture.py`). L'entreprise y répond telle
    quelle ; le domaine ne dépend de rien.
    """

    denomination: str

    def est_adherente_au(self, a_la_date: date) -> bool: ...


_A = TypeVar("_A", bound=Adherable)


class Fourchette(BaseModel):
    model_config = ConfigDict(frozen=True)
    minimum: Decimal = Field(ge=0)
    maximum: Decimal = Field(ge=0)


class Professionnel(BaseModel):
    model_config = ConfigDict(frozen=True)
    nom: str = Field(min_length=1)
    #: Le numéro d'inscription à l'ordre compétent.
    inscription: str = Field(min_length=1)


class ReglesDuCentre(BaseModel):
    """Le fichier `cabinet/conformite_du_centre.yaml`."""

    model_config = ConfigDict(frozen=True)
    seuil_adherents_actifs: int = Field(default=100, ge=1)
    adherents_par_professionnel: int = Field(default=2000, ge=1)
    droits_d_adhesion: Fourchette = Fourchette(minimum=Decimal(25000), maximum=Decimal(50000))
    cotisation_igs: Fourchette = Fourchette(minimum=Decimal(50000), maximum=Decimal(150000))
    cotisation_reel: Fourchette = Fourchette(minimum=Decimal(50000), maximum=Decimal(250000))
    conseils_fiscaux: list[Professionnel] = Field(default_factory=list)
    experts_comptables: list[Professionnel] = Field(default_factory=list)
    source: str = "cabinet/conformite_du_centre.yaml"


class ConformiteDuCentre(BaseModel):
    a_la_date: date
    #: Art. 119 (3).
    adherents_actifs: int
    seuil_adherents_actifs: int
    seuil_atteint: bool
    #: Combien il en manque pour atteindre le seuil ; zéro s'il est atteint.
    manque_pour_le_seuil: int
    #: Art. 118 (5) : par catégorie, conseil fiscal d'une part, expert-comptable de l'autre.
    professionnels_requis: int
    conseils_fiscaux: list[Professionnel]
    experts_comptables: list[Professionnel]
    professionnels_conformes: bool
    #: Art. 118 (3) et (4), rendues telles quelles (voir l'en-tête).
    droits_d_adhesion: Fourchette
    cotisation_igs: Fourchette
    cotisation_reel: Fourchette
    #: Ce qui demande une action du cabinet, en clair.
    a_faire: list[str]
    source: str


def adherents_actifs(entreprises: Iterable[_A], jour: date) -> list[_A]:
    """Les entreprises dont l'adhésion est en cours au jour, dans l'ordre du nom."""
    return sorted(
        (e for e in entreprises if e.est_adherente_au(jour)), key=lambda e: e.denomination
    )


def professionnels_requis(adherents: int, par_professionnel: int) -> int:
    """Au moins un, puis un de plus par tranche entamée de `par_professionnel` adhérents."""
    return max(1, ceil(adherents / par_professionnel))


def evaluer_la_conformite(
    entreprises: Iterable[Adherable], regles: ReglesDuCentre, jour: date
) -> ConformiteDuCentre:
    actifs = len(adherents_actifs(entreprises, jour))
    requis = professionnels_requis(actifs, regles.adherents_par_professionnel)
    conformes = (
        len(regles.conseils_fiscaux) >= requis and len(regles.experts_comptables) >= requis
    )
    a_faire: list[str] = []
    if not regles.conseils_fiscaux or not regles.experts_comptables:
        a_faire.append(
            "Déclarer les professionnels du Centre (conseil fiscal, expert-comptable inscrit à "
            "l'ordre) : sans eux, l'agrément peut être suspendu (CGI art. 118 (5))."
        )
    elif not conformes:
        a_faire.append(
            f"{requis} conseil(s) fiscal(aux) et {requis} expert(s)-comptable(s) sont requis "
            f"pour {actifs} adhérents : en déclarer davantage (CGI art. 118 (5))."
        )
    a_faire.append(
        "Arrêter quelle part de l'offre constitue la cotisation annuelle de l'article 118 (4) : "
        "le prix mensuel du suivi comptable couvre aussi la tenue des comptes et la DSF."
    )
    return ConformiteDuCentre(
        a_la_date=jour,
        adherents_actifs=actifs,
        seuil_adherents_actifs=regles.seuil_adherents_actifs,
        seuil_atteint=actifs >= regles.seuil_adherents_actifs,
        manque_pour_le_seuil=max(0, regles.seuil_adherents_actifs - actifs),
        professionnels_requis=requis,
        conseils_fiscaux=list(regles.conseils_fiscaux),
        experts_comptables=list(regles.experts_comptables),
        professionnels_conformes=conformes,
        droits_d_adhesion=regles.droits_d_adhesion,
        cotisation_igs=regles.cotisation_igs,
        cotisation_reel=regles.cotisation_reel,
        a_faire=a_faire,
        source=regles.source,
    )
