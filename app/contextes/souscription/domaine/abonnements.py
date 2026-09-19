"""L'échéancier d'un abonnement, et ce qui arrive quand il n'est pas réglé.

─────────────────────────────────────────────────────────────────────────────────
LE MANQUE QUE CE MODULE COMBLE

La première mensualité était encaissée à la souscription, et **plus rien ne se
passait**. Un abonnement dont la deuxième échéance n'est jamais appelée n'est pas
un abonnement : c'est une vente unique déguisée, et le cabinet travaille onze mois
gratuitement.

L'ÉCHÉANCIER SE CALCULE, IL NE SE STOCKE PAS

Même discipline qu'au contexte F. Il découle de trois choses — la date d'effet, la
périodicité, la date de résiliation — et toutes trois sont déjà portées par la
souscription. Le persister figerait un calendrier qui deviendrait faux à la
première résiliation, et personne ne verrait qu'il l'est devenu.

Ce qui est stocké, ce sont les **paiements**. Une échéance est identifiée par sa
période, et son état se lit en confrontant l'échéancier aux paiements reçus.

LES PÉRIODES SUIVENT LA DATE D'EFFET, PAS LE MOIS CIVIL

Une souscription du 17 mars produit des périodes du 17 au 16, pas du 1er au 31.
C'est ce qui évite le prorata — et le prorata est ce qui rend une première facture
incompréhensible. « Vous payez 12 500 F par mois » doit vouloir dire exactement
cela dès le premier prélèvement.

⚠️ Le 31 janvier est ramené au 28 ou 29 février, puis **le 31 revient** en mars :
la période suit la date d'effet, elle ne dérive pas. Sans cela, un abonnement
souscrit le 31 glisserait au 28 pour toujours après un seul février.

ON APPELLE AVANT L'ÉCHÉANCE, PAS LE JOUR MÊME

Cinq jours. Un prélèvement mobile échoue souvent pour une raison passagère —
solde insuffisant le 30 du mois, téléphone éteint, réseau. Appeler le jour même
ne laisse aucune marge pour recommencer avant que le service ne s'interrompe.

SUSPENDRE N'EST PAS SÉQUESTRER

Passé le délai de grâce, le **service** s'arrête : plus de traitement de pièces,
plus de déclarations préparées. L'adhérent conserve l'accès en lecture à son
dossier.

Ce n'est pas de la générosité. Les pièces déposées et les écritures produites
sont **ses** documents comptables, qu'il est légalement tenu de conserver dix ans.
Les lui retenir pour obtenir un paiement l'exposerait à un manquement dont il
n'est pas responsable — et exposerait le cabinet à devoir s'en expliquer.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.souscription.domaine.paiements import Paiement, StatutPaiement
from app.contextes.souscription.domaine.souscriptions import (
    EtatSouscription,
    Souscription,
)

__all__ = [
    "DELAI_APPEL",
    "DELAI_GRACE",
    "JALONS_RELANCE",
    "EcheanceAbonnement",
    "EtatEcheance",
    "echeancier",
    "mois_apres",
]

#: Cinq jours avant la période, pour laisser le temps de recommencer — voir
#: l'en-tête.
DELAI_APPEL = timedelta(days=5)

#: Quinze jours après l'exigibilité avant que le service ne s'arrête. Un
#: prélèvement qui échoue n'est pas un défaut de paiement : c'est souvent un
#: solde momentané. Couper au premier échec ferait perdre des adhérents solvables.
DELAI_GRACE = timedelta(days=15)

#: Jours après l'exigibilité où l'on relance. Le premier est le lendemain — pas
#: pour presser, mais parce que l'adhérent ignore souvent que le prélèvement a
#: échoué : l'opérateur ne le lui dit pas.
JALONS_RELANCE = (1, 7, 14)


def mois_apres(depart: date, mois: int) -> date:
    """La même date, `mois` mois plus tard, ramenée à la fin du mois si besoin.

    Le 31 janvier plus un mois donne le 28 février — ou le 29. Ce qui compte est
    que l'on calcule toujours **depuis la date d'effet**, jamais de proche en
    proche : sans cela, un abonnement souscrit un 31 glisserait au 28 pour
    toujours après un seul février.
    """
    total = depart.month - 1 + mois
    annee = depart.year + total // 12
    mois_cible = total % 12 + 1
    jour = min(depart.day, calendar.monthrange(annee, mois_cible)[1])
    return date(annee, mois_cible, jour)


class EtatEcheance(StrEnum):
    """Où en est une échéance, à une date donnée.

    Six états, et deux distinctions portent tout le sens.

    `A_APPELER` contre `A_VENIR` : la première réclame une action aujourd'hui, la
    seconde ne réclame rien. Les confondre produirait soit des prélèvements
    prématurés, soit une liste de travail qui ne se vide jamais.

    `IMPAYEE` contre `EN_DEFAUT` : la première est un incident — on relance. La
    seconde a dépassé le délai de grâce et déclenche l'arrêt du service. Les
    confondre couperait le service au premier échec de prélèvement.
    """

    #: Réglée. Un paiement validé couvre la période.
    REGLEE = "REGLEE"
    #: Un prélèvement est en cours ; l'abonné n'a pas encore saisi son code.
    EN_COURS = "EN_COURS"
    #: À appeler aujourd'hui.
    A_APPELER = "A_APPELER"
    #: Trop tôt pour appeler.
    A_VENIR = "A_VENIR"
    #: Exigible et non réglée. On relance.
    IMPAYEE = "IMPAYEE"
    #: Délai de grâce dépassé. Le service s'arrête — mais pas la lecture.
    EN_DEFAUT = "EN_DEFAUT"


class EcheanceAbonnement(BaseModel):
    """Une mensualité, calculée et non stockée."""

    model_config = ConfigDict(frozen=True)

    souscription: str = Field(min_length=1)
    #: 1 pour la mensualité réglée à la souscription. Sert de numéro d'ordre
    #: lisible — « la troisième échéance » se dit, « la période du 17 mai » se
    #: cherche.
    numero: int = Field(ge=1)

    periode_debut: date
    periode_fin: date
    montant: Decimal = Field(gt=0)

    #: Le jour où le prélèvement est demandé.
    appelee_le: date
    #: Le jour où la période commence, donc où le règlement est dû. Un abonnement
    #: se paie d'avance : on règle le mois qu'on va consommer.
    exigible_le: date

    etat: EtatEcheance
    paiement: str | None = None

    @computed_field
    @property
    def cle(self) -> str:
        """L'identité de l'échéance : souscription et période.

        C'est elle que porte le paiement, et c'est elle qui empêche de prélever
        deux fois le même mois. Elle n'inclut pas le numéro : renuméroter après
        une résiliation partielle changerait la clé d'échéances déjà réglées.
        """
        return f"{self.souscription}/{self.periode_debut:%Y%m%d}"

    def jours_de_retard(self, a_la_date: date) -> int:
        """Négatif avant l'exigibilité. Zéro le jour même."""
        return (a_la_date - self.exigible_le).days

    @computed_field
    @property
    def a_relancer(self) -> bool:
        """Sérialisé : c'est la colonne que l'écran de recouvrement lit."""
        return self.etat in (EtatEcheance.IMPAYEE, EtatEcheance.EN_DEFAUT)


def _etat(
    exigible_le: date,
    appelee_le: date,
    paiement: Paiement | None,
    a_la_date: date,
) -> EtatEcheance:
    """L'état d'une échéance, résolu à une date. L'ordre des tests est le sujet.

    Un paiement validé l'emporte sur tout : une échéance réglée en retard reste
    réglée, et la faire apparaître impayée ferait relancer un adhérent à jour.
    """
    if paiement is not None and paiement.statut is StatutPaiement.VALIDE:
        return EtatEcheance.REGLEE
    if paiement is not None and paiement.statut is StatutPaiement.EN_ATTENTE:
        return EtatEcheance.EN_COURS
    if a_la_date >= exigible_le + DELAI_GRACE:
        return EtatEcheance.EN_DEFAUT
    if a_la_date >= exigible_le:
        return EtatEcheance.IMPAYEE
    if a_la_date >= appelee_le:
        return EtatEcheance.A_APPELER
    return EtatEcheance.A_VENIR


def echeancier(
    souscription: Souscription,
    paiements: Sequence[Paiement],
    *,
    jusqu_au: date,
) -> list[EcheanceAbonnement]:
    """Les mensualités d'un abonnement, de sa mise en route à une date.

    Rend une liste vide pour une souscription qui n'est pas un abonnement, qui
    n'a jamais pris effet, ou dont le montant mensuel est nul. Ce n'est pas une
    erreur : la plupart des souscriptions du catalogue sont ponctuelles.

    Une souscription résiliée cesse de produire des échéances **à la fin de la
    période en cours**, jamais au jour de la résiliation : le mois entamé a été
    payé d'avance, il est dû jusqu'au bout.
    """
    if souscription.abonnement_mensuel <= 0:
        return []
    if souscription.prend_effet_le is None:
        return []
    if souscription.etat not in (EtatSouscription.ACTIVEE, EtatSouscription.RESILIEE):
        return []

    par_cle = {p.echeance: p for p in paiements if p.echeance is not None}
    #: Le paiement de la souscription ne porte pas de clé d'échéance : il a été
    #: engagé avant qu'elles n'existent. Il règle la première.
    initial = next(
        (p for p in paiements if p.echeance is None and p.encaisse), None
    )

    depart = souscription.prend_effet_le
    fin = souscription.close_le.date() if souscription.close_le else None

    echeances: list[EcheanceAbonnement] = []
    numero = 1
    while True:
        debut = mois_apres(depart, numero - 1)
        # La borne est la date d'**appel**, pas le début de période : une
        # échéance devient visible cinq jours avant, et c'est précisément à ce
        # moment qu'il faut la voir. Borner sur `debut` la ferait apparaître le
        # jour de son exigibilité — donc trop tard pour l'appeler à l'avance.
        if debut - DELAI_APPEL > jusqu_au:
            break
        # La résiliation arrête l'échéancier à la période **suivante** : le mois
        # entamé a été payé d'avance et reste dû.
        if fin is not None and debut > fin:
            break

        prochaine = mois_apres(depart, numero)
        paiement = par_cle.get(f"{souscription.reference}/{debut:%Y%m%d}")
        if paiement is None and numero == 1:
            paiement = initial

        echeances.append(
            EcheanceAbonnement(
                souscription=souscription.reference,
                numero=numero,
                periode_debut=debut,
                periode_fin=prochaine - timedelta(days=1),
                montant=souscription.abonnement_mensuel,
                appelee_le=debut - DELAI_APPEL,
                exigible_le=debut,
                etat=_etat(debut, debut - DELAI_APPEL, paiement, jusqu_au),
                paiement=paiement.identifiant if paiement else None,
            )
        )
        numero += 1

    return echeances
