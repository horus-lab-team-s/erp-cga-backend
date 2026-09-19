"""Le balayage de relance : ce qui est dû, déposé une fois, et jamais deux.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE N'ENVOIE RIEN

Il **dépose** des événements dans la boîte d'envoi. C'est le relais qui les remet
aux abonnés, et un abonné de messagerie qui les postera.

⚠️ La séparation n'est pas décorative. Un balayage qui posterait lui-même tiendrait
la connexion à la passerelle de messagerie pendant tout son passage, ferait échouer
le balayage entier sur un envoi refusé, et rejouerait tous les envois d'un lot si le
processus tombait au milieu. Le dépôt, lui, est **dans la même transaction que
l'inscription du suivi** : soit les deux, soit ni l'un ni l'autre.

C'est ce qui rend la propriété qui compte : **un client relancé au rang 2 ne peut
pas l'être une seconde fois au rang 2**, même si le processus meurt entre les deux
écritures.

DEUX SORTES DE SORTIE, ET ELLES NE VONT PAS AU MÊME ENDROIT

Une **relance due** produit un message au client, par un modèle configuré. Une
**acceptée impayée** ne produit aucun message : elle remonte à un humain. Le client
s'est engagé et n'a pas réglé ; continuer à lui envoyer des modèles ne produit rien
qu'une facture de messagerie. Voir `impayees_a_reprendre` dans le domaine.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.domaine.proforma import Proforma
from app.contextes.souscription.domaine.relance import (
    PlanDeRelance,
    RelanceADeclencher,
    SuiviDeRelance,
    impayees_a_reprendre,
    relances_dues,
)
from app.orchestration.boite_d_envoi import EvenementSortant, deposer

__all__ = [
    "NOM_IMPAYEE",
    "NOM_RELANCE",
    "RapportDeBalayage",
    "balayer_les_relances",
]

#: Les noms métier, tels que le document de conception les écrit. Ce sont eux qui
#: figureront dans les journaux qu'un exploitant lit à trois heures du matin, et
#: dans les abonnements : un nom technique y serait illisible.
NOM_RELANCE = "RelanceDue"
NOM_IMPAYEE = "AccepteeImpayee"


class BoiteOuDeposer(Protocol):
    """Ce que ce module exige de la boîte d'envoi, et rien de plus."""

    def deposer(self, evenement: EvenementSortant) -> None: ...


class DepotSuivis(Protocol):
    def tous(self) -> dict[str, SuiviDeRelance]: ...

    def enregistrer(self, suivi: SuiviDeRelance) -> None: ...


class RapportDeBalayage(BaseModel):
    """Ce qu'un passage a produit. Sans aucun contenu de message."""

    model_config = ConfigDict(frozen=True)

    #: Les proformas examinées. Sert à distinguer « rien à faire » de « rien lu ».
    examinees: int = 0
    relances: tuple[str, ...] = ()
    #: Les acceptées impayées remontées à un humain. Pas des relances.
    impayees: tuple[str, ...] = ()
    #: Les relances de dernier palier. ⚠️ Après elles le système se tait, et le
    #: responsable doit le savoir : c'est le moment où un dossier cesse d'être
    #: suivi par la machine.
    dernieres: tuple[str, ...] = ()

    @property
    def resume(self) -> str:
        return (
            f"{self.examinees} proforma(s) examinée(s), {len(self.relances)} relance(s) "
            f"dont {len(self.dernieres)} dernier(s) palier(s), "
            f"{len(self.impayees)} impayée(s) à reprendre"
        )


def balayer_les_relances(
    proformas: Sequence[Proforma],
    plan: PlanDeRelance,
    *,
    suivis: DepotSuivis,
    boite: BoiteOuDeposer,
    a_l_instant: datetime,
    identifiant: Callable[[str], str],
) -> RapportDeBalayage:
    """Dépose ce qui est dû, inscrit ce qui a été déposé, et rend compte.

    ─────────────────────────────────────────────────────────────────────────────
    L'ORDRE DES DEUX ÉCRITURES N'A PAS D'IMPORTANCE, ET C'EST VOULU

    Le dépôt de l'événement et l'inscription du suivi sont dans la même transaction :
    les inverser ne change rien, puisque ni l'un ni l'autre n'est visible avant la
    validation. C'est l'appelant qui tient cette transaction, et c'est pourquoi ce
    module ne valide rien lui-même.

    ⚠️ Un balayage qui validerait entre les deux écritures rouvrirait précisément le
    trou qu'on ferme : événement déposé, suivi non inscrit, et le client reçoit deux
    fois le même message au passage suivant.

    L'IDENTIFIANT PORTE LE RANG, LA CLÉ D'ORDRE NE LE PORTE PAS

    Ce sont deux choses distinctes de la boîte d'envoi, et les confondre coûte cher.

    L'**identifiant** est l'identité de l'événement : `PRO-2026-0001-2`. Deux
    relances de la même proforma à deux paliers sont deux faits différents, et leur
    donner le même identifiant ferait perdre l'une des deux.

    La **clé** est ce sur quoi la boîte tient l'ordre, et c'est le numéro **seul**.
    Deux relances de la même proforma sont donc sérialisées : le rang 3 ne peut pas
    partir avant le rang 2.

    ⚠️ La conséquence mérite d'être assumée plutôt que subie : **un rang 1 en échec
    bloque le rang 2 de la même proforma**. C'est voulu. Si le premier message n'est
    jamais parti, envoyer le second ferait recevoir au client une relance de
    deuxième niveau pour un message qu'il n'a jamais eu. Les autres proformas ne
    sont pas retenues : la boîte ne bloque que la clé fautive.
    ─────────────────────────────────────────────────────────────────────────────
    """
    etat = suivis.tous()
    dues = relances_dues(proformas, etat, plan, a_l_instant)

    relances: list[str] = []
    dernieres: list[str] = []
    for relance in dues:
        deposer(
            boite,
            identifiant(f"{relance.proforma}-{relance.rang}"),
            NOM_RELANCE,
            relance.proforma,
            _charge_relance(relance),
            a_l_instant,
        )
        # ⚠️ **`rangs_couverts`, et non `rang` seul.** Quand plusieurs paliers sont
        # dus, un seul message part et les précédents n'ont plus d'objet : ne
        # marquer que celui qui part ferait qu'au passage suivant un rang inférieur
        # serait encore libre et partirait. Le client recevrait un rappel moins
        # urgent après un rappel plus urgent.
        #
        # `avec_les` est rejouable : un rang n'entre qu'une fois. Sans cette garde,
        # un balayage rejoué doublerait le compteur et ferait croire que le client a
        # été relancé deux fois au même palier.
        precedent = etat.get(relance.proforma, SuiviDeRelance(proforma=relance.proforma))
        suivis.enregistrer(precedent.avec_les(relance.rangs_couverts, a_l_instant))
        relances.append(relance.proforma)
        if relance.derniere:
            dernieres.append(relance.proforma)

    impayees = []
    for proforma in impayees_a_reprendre(proformas, plan, a_l_instant):
        deposer(
            boite,
            identifiant(f"impayee-{proforma.numero}"),
            NOM_IMPAYEE,
            proforma.dossier,
            {
                "proforma": proforma.numero,
                "dossier": proforma.dossier,
                "transmise_le": proforma.transmise_le.isoformat()
                if proforma.transmise_le
                else None,
            },
            a_l_instant,
        )
        impayees.append(proforma.numero)

    return RapportDeBalayage(
        examinees=len(proformas),
        relances=tuple(relances),
        impayees=tuple(impayees),
        dernieres=tuple(dernieres),
    )


def _charge_relance(relance: RelanceADeclencher) -> dict[str, Any]:
    """Ce que l'événement transporte. Le strict nécessaire à l'envoi.

    ⚠️ **Ni le montant, ni le nom du client, ni son numéro.** Un événement qui
    transporte des données dont personne n'a besoin finit par en transporter qu'on
    ne voulait pas voir circuler : les journaux, les files et les sauvegardes le
    recopient tous. Celui qui postera lira la proforma et le dossier.

    `depuis_jours` plutôt que le `timedelta` : une charge d'événement se relit dans
    un journal et se compare d'une version à l'autre, et une durée sérialisée par
    Python y serait illisible.
    """
    return {
        "proforma": relance.proforma,
        "dossier": relance.dossier,
        "rang": relance.rang,
        "modele": relance.modele,
        "ton": relance.ton,
        "depuis_jours": relance.depuis.days,
        "derniere": relance.derniere,
    }
