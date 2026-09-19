"""Le tenant, et son cycle de vie.

─────────────────────────────────────────────────────────────────────────────────
CE QU'EST UN TENANT, ET SURTOUT CE QU'IL N'EST PAS

Une ligne en base, un schéma PostgreSQL, un préfixe de stockage. **Rien d'autre.**

Ce n'est pas un déploiement. Aucun conteneur ne démarre, aucun enregistrement DNS ne
s'écrit, aucun certificat ne se demande quand un client arrive. C'est cette propriété qui
rend la souscription instantanée au trois-centième client comme au premier, et son absence
est ce qui transforme une souscription en ticket d'exploitation.

L'OUVERTURE EST UNE TRANSACTION LONGUE, DONC ELLE SE REPREND

Sept étapes, franchies dans l'ordre, chacune idempotente. Le tenant porte l'étape qu'il a
atteinte, et c'est ce qui permet à une ouverture interrompue de **reprendre là où elle
s'est arrêtée** plutôt que de recommencer.

La distinction n'est pas théorique : recommencer depuis le début créerait un second schéma
pour le même tenant, et le premier resterait là sans que personne sache à quoi il sert.

DEUX RÈGLES QUI NE SE NÉGOCIENT PAS

**Aucune suppression automatique.** Un échec ne détruit jamais un schéma déjà créé. La
reprise est toujours préférable à la destruction : un schéma orphelin coûte quelques
mégaoctets, un schéma détruit à tort coûte une comptabilité.

**Le slug reste réservé après résiliation.** Le libérer enverrait les anciens liens, les
signets et les courriels archivés d'un client chez un concurrent. C'est le pire incident de
confidentialité que cette plateforme puisse produire.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ETAPES_ORDONNEES",
    "STATUTS_TERMINAUX",
    "EtapeOuverture",
    "NatureTenant",
    "StatutTenant",
    "Tenant",
    "TransitionInterdite",
    "etape_suivante",
]


class TransitionInterdite(ValueError):
    """Le tenant ne peut pas passer de son état courant à celui demandé.

    Volontairement une erreur et non un refus silencieux. Un provisionnement qui sauterait
    la création du schéma et amorcerait le plan comptable écrirait dans le vide, et
    l'anomalie ne se verrait qu'à la première écriture d'un client, des jours plus tard.
    """


class NatureTenant(StrEnum):
    """Les deux populations qui souscrivent, et elles ne se comportent pas pareil.

    La nature n'ouvre pas des droits différents — c'est l'affaire des habilitations — mais
    elle commande ce qu'on amorce à l'ouverture, et qui peut détenir un mandat sur qui.
    """

    #: Un centre de gestion agréé, avec ses agences et ses collaborateurs.
    CENTRE = "CENTRE"
    #: Une entreprise qui gère elle-même une partie de son travail.
    ENTREPRISE = "ENTREPRISE"


class StatutTenant(StrEnum):
    """Où en est un tenant. Distinct de l'étape, qui ne concerne que l'ouverture."""

    #: L'ouverture est en cours. Le sous-domaine ne répond pas encore.
    EN_OUVERTURE = "EN_OUVERTURE"
    #: Le sous-domaine répond et sert les requêtes.
    ACTIF = "ACTIF"
    #: Le sous-domaine répond une page de régularisation. **Les données restent intactes.**
    SUSPENDU = "SUSPENDU"
    #: L'accès est coupé. Le slug reste réservé, les données attendent leur purge.
    RESILIE = "RESILIE"
    #: L'ouverture a échoué définitivement. Rien n'est détruit, tout est diagnosticable.
    ECHEC = "ECHEC"


#: Les états dont on ne sort pas. Un tenant résilié qui repartirait n'existe pas : le
#: client revient avec une nouvelle souscription, donc un nouveau tenant — et l'historique
#: garde ainsi la trace de son départ au lieu de la réécrire.
STATUTS_TERMINAUX: frozenset[StatutTenant] = frozenset({StatutTenant.RESILIE})


class EtapeOuverture(StrEnum):
    """Les sept étapes de l'ouverture, dans l'ordre où elles se franchissent.

    ⚠️ L'ordre de déclaration **est** l'ordre métier : `etape_suivante` s'en sert. Réordonner
    ces membres change le comportement du provisionnement — ce n'est pas une énumération
    décorative.

    Chaque étape est idempotente : rejouée, elle constate que son travail est fait et rend
    le même résultat. C'est la condition pour qu'une livraison au moins une fois sur le bus
    soit acceptable.
    """

    #: Le slug est pris en base, index unique à l'appui. La base a arbitré les
    #: souscriptions simultanées.
    SLUG_RESERVE = "SLUG_RESERVE"
    #: La ligne du tenant existe : plan souscrit, quotas, période de facturation.
    LIGNE_CREEE = "LIGNE_CREEE"
    #: Le schéma PostgreSQL existe et porte les migrations à la version courante.
    SCHEMA_CREE = "SCHEMA_CREE"
    #: Le métier est amorcé : plan comptable du régime, exercice ouvert, journaux.
    METIER_AMORCE = "METIER_AMORCE"
    #: Le préfixe de stockage existe, avec sa clé de chiffrement et sa règle de rétention.
    STOCKAGE_OUVERT = "STOCKAGE_OUVERT"
    #: Le compte administrateur existe, avec son jeton d'activation à usage unique.
    ADMINISTRATEUR_CREE = "ADMINISTRATEUR_CREE"
    #: Tout est en place. Il ne reste qu'à basculer le statut.
    PRET = "PRET"


#: L'ordre des étapes, figé une fois pour que le reste s'y réfère.
ETAPES_ORDONNEES: tuple[EtapeOuverture, ...] = tuple(EtapeOuverture)


def etape_suivante(etape: EtapeOuverture) -> EtapeOuverture | None:
    """L'étape qui suit, ou None si l'ouverture est prête à basculer.

    ─────────────────────────────────────────────────────────────────────────
    ON N'AVANCE QUE D'UN CRAN, ET ON NE REVIENT JAMAIS

    Un provisionnement qui laisserait sauter une étape produirait des tenants à moitié
    faits, chacun d'une façon différente. Le saut le plus tentant — « le schéma existe
    déjà, passons directement au stockage » — est aussi le plus coûteux : il laisse un
    schéma sans ses migrations, qui accepte les écritures et perd les colonnes.

    Le retour en arrière n'est pas ouvert non plus, et pour une raison plus concrète encore.
    Revenir de SCHEMA_CREE à LIGNE_CREEE puis avancer de nouveau créerait un **second
    schéma** pour le même tenant, et le premier resterait là sans que personne sache à quoi
    il sert.
    ─────────────────────────────────────────────────────────────────────────
    """
    position = ETAPES_ORDONNEES.index(etape)
    if position + 1 >= len(ETAPES_ORDONNEES):
        return None
    return ETAPES_ORDONNEES[position + 1]


class Tenant(BaseModel):
    """Un client de la plateforme, au sens de l'isolation.

    Immuable : chaque transition rend un **nouveau** tenant. C'est ce qui permet de
    comparer l'avant et l'après, et ce qui empêche un cas d'usage d'altérer un tenant qu'un
    autre tient déjà.
    """

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    nature: NatureTenant
    statut: StatutTenant = StatutTenant.EN_OUVERTURE
    etape_atteinte: EtapeOuverture = EtapeOuverture.SLUG_RESERVE

    #: Quand le sous-domaine a commencé à répondre. None tant qu'il ne répond pas.
    ouvert_le: date | None = None
    suspendu_le: date | None = None
    resilie_le: date | None = None

    #: Pourquoi il est dans cet état. Obligatoire pour une suspension, une résiliation et
    #: un échec : un tenant coupé sans motif est un incident qu'on ne saura pas expliquer
    #: au client qui appelle.
    motif: str | None = None

    #: Combien de fois l'ouverture a été reprise. Au delà d'un seuil, l'ordonnanceur cesse
    #: de reprendre et bascule en échec plutôt que de boucler indéfiniment.
    reprises: int = 0

    @model_validator(mode="after")
    def _coherence(self) -> Tenant:
        if self.statut is StatutTenant.ACTIF and self.ouvert_le is None:
            raise ValueError(f"{self.slug} : un tenant actif porte sa date d'ouverture")
        for statut, champ in (
            (StatutTenant.SUSPENDU, "suspendu_le"),
            (StatutTenant.RESILIE, "resilie_le"),
        ):
            if self.statut is statut and getattr(self, champ) is None:
                raise ValueError(f"{self.slug} : un tenant {statut.value} porte sa date")
        if self.statut in _EXIGENT_UN_MOTIF and not (self.motif or "").strip():
            raise ValueError(
                f"{self.slug} : un tenant {self.statut.value} porte un motif. Sans lui, "
                "personne ne saura répondre au client qui appelle."
            )
        return self

    # ── Ce que la passerelle a besoin de savoir ──────────────────────────────

    @property
    def sert_les_requetes(self) -> bool:
        """Le tenant traite-t-il les requêtes métier ?

        Seul un tenant actif le fait. Un tenant suspendu répond, mais une page de
        régularisation : ses données sont intactes et le resteront.
        """
        return self.statut is StatutTenant.ACTIF

    @property
    def existe_publiquement(self) -> bool:
        """Le sous-domaine doit-il répondre autre chose qu'un 404 ?

        Un tenant en cours d'ouverture n'existe pas encore publiquement : son sous-domaine
        rend 404, ce qui est exactement le bon comportement puisqu'il **répond déjà**
        grâce à l'enregistrement DNS générique, bien avant qu'aucun tenant ne soit créé.
        """
        return self.statut in {StatutTenant.ACTIF, StatutTenant.SUSPENDU}

    @property
    def ouverture_achevee(self) -> bool:
        return self.etape_atteinte is EtapeOuverture.PRET


#: Un tenant dans l'un de ces états sans motif serait un incident inexplicable.
_EXIGENT_UN_MOTIF: frozenset[StatutTenant] = frozenset(
    {StatutTenant.SUSPENDU, StatutTenant.RESILIE, StatutTenant.ECHEC}
)
