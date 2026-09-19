"""L'encaissement mobile, et les garanties qui empêchent le double-comptage.

─────────────────────────────────────────────────────────────────────────────────
CE MODULE EST UN PORTAGE, ET IL FAUT DIRE D'OÙ

La mécanique reprise ici vient du module `mail+paiement/paiement/` du dépôt, deux
applications Django extraites d'un projet en production où elles ont été durcies
après incidents. Ce qui est repris n'est pas son code — il est en Django, et l'ERP
est en FastAPI — mais **ses invariants**, qui sont indépendants du cadre :

* une clé d'idempotence propre, envoyée au prestataire comme identifiant de
  produit, pour qu'il déduplique de son côté ;
* un traitement de notification atomique et rejouable ;
* un rapprochement à trois stratégies, parce que le prestataire ne renvoie pas
  toujours notre identifiant ;
* une réconciliation périodique qui repêche les encaissements restés en attente ;
* une péremption à vingt-quatre heures.

POURQUOI TROIS STRATÉGIES, ET NON UNE

La première — notre identifiant, rendu tel quel — devrait suffire. Elle ne suffit
pas : selon le chemin qu'emprunte l'opération chez l'opérateur, le champ revient
parfois vide, parfois remplacé par une référence maison.

La deuxième rattrape par la référence que le prestataire nous a donnée à
l'initiation.

La troisième rattrape par le numéro de téléphone, sur une fenêtre de trente
minutes, **à montant égal**. Cette dernière condition n'est pas une précaution de
style : sans elle, deux souscriptions engagées depuis le même téléphone dans la
même demi-heure se croiseraient, et la moins chère validerait la plus chère.

Ce qui arrive quand le rapprochement échoue est précisément le scénario à éviter :
l'argent est débité, rien ne s'active, le client ne voit rien venir — **et il
repaie**. C'est cela, la « compensation » que le module d'origine a appris à
prévenir.

UN MONTANT DIFFÉRENT N'EST PAS UN PAIEMENT PARTIEL, C'EST UN REJET

Accepter un encaissement inférieur au dû activerait douze mois de domiciliation
pour cent francs. Le paiement est rejeté avec son motif, et la somme reste à
traiter à la main — ce qui est visible, contrairement à une activation indue.

CE QU'AUCUNE NOTIFICATION NE PROUVE

Le prestataire **ne signe pas** ses notifications : pas de HMAC, pas d'en-tête de
signature. La sécurité repose sur HTTPS, une adresse de rappel secrète, et la
vérification de l'identifiant marchand. Un statut « payé » ne se déduit donc
jamais d'un appel entrant seul : c'est le rapprochement qui fait foi, et
l'interrogation de statut auprès du prestataire qui confirme.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.partage.telephone import normaliser_telephone

__all__ = [
    "DELAI_EXPIRATION",
    "FENETRE_RAPPROCHEMENT",
    "EvenementPaiement",
    "ESPACEMENT_MAXIMAL",
    "MontantIncoherent",
    "NaturePaiement",
    "Paiement",
    "StatutPaiement",
    "StrategieRapprochement",
    "rapprocher",
]

#: Vingt-quatre heures. Au-delà, un encaissement resté en attente est réputé
#: abandonné : le client a fermé son téléphone, n'a pas saisi son code, ou
#: l'opérateur a perdu l'opération. Le laisser en attente indéfiniment
#: encombrerait la réconciliation d'opérations mortes, et laisserait croire à un
#: encaissement possible là où il n'y en a plus.
DELAI_EXPIRATION = timedelta(hours=24)

#: Le plus grand espacement entre deux interrogations du prestataire. Une heure :
#: au-delà, sur une opération qui expire à vingt-quatre heures, on n'interrogerait
#: plus assez souvent pour que le filet serve à quelque chose.
ESPACEMENT_MAXIMAL = timedelta(hours=1)

#: Trente minutes pour la troisième stratégie de rapprochement. Au-delà, le
#: rapprochement par téléphone devient trop hasardeux : un même numéro peut avoir
#: engagé deux opérations dans la journée.
FENETRE_RAPPROCHEMENT = timedelta(minutes=30)


class StatutPaiement(StrEnum):
    EN_ATTENTE = "EN_ATTENTE"
    VALIDE = "VALIDE"
    REJETE = "REJETE"
    #: Resté en attente au-delà du délai. Distinct de REJETE : personne n'a
    #: refusé, l'opération s'est simplement perdue, et le traitement diffère —
    #: on peut proposer de recommencer.
    EXPIRE = "EXPIRE"


class NaturePaiement(StrEnum):
    """Ce qu'un paiement règle, donc ce qui doit arriver quand il est encaissé.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CE DISCRIMINANT EXISTE

    Le règlement, le rapprochement, l'idempotence et l'expiration sont les mêmes
    quel que soit l'objet payé : c'est le prestataire qui les impose, pas le
    métier. **La suite, elle, diffère.** Une souscription réglée s'active et ouvre
    un accès ; une proforma réglée ouvre un tenant.

    Tout le mécanisme de paiement était écrit pour la souscription seule, et
    `appliquer_evenement` lisait `souscriptions.lire(paiement.souscription)` sans
    se demander si c'en était une. Le parcours d'acquisition, qui vend lui aussi,
    faisait donc saisir l'encaissement **à la main** pendant que l'intégration du
    prestataire tournait à côté pour l'autre flux.

    ⚠️ **Le discriminant n'est pas une commodité de branchement.** Sans lui, la
    seule façon de raccorder le parcours aurait été de deviner la nature depuis le
    format de la référence, c'est-à-dire de router un encaissement sur une
    coïncidence de chaîne de caractères.
    ─────────────────────────────────────────────────────────────────────────────
    """

    #: Un abonnement au service du centre : mise en route, puis mensualités.
    SOUSCRIPTION = "SOUSCRIPTION"
    #: Une proforma du parcours d'acquisition. Son règlement ouvre un tenant.
    PROFORMA = "PROFORMA"


class StrategieRapprochement(StrEnum):
    """Comment un encaissement a été rattaché. Enregistré, et pas seulement
    utilisé : quand une anomalie survient, savoir par quelle voie le
    rapprochement s'est fait est la première chose qu'on regarde."""

    IDENTIFIANT = "IDENTIFIANT"
    REFERENCE_EXTERNE = "REFERENCE_EXTERNE"
    TELEPHONE_ET_RECENCE = "TELEPHONE_ET_RECENCE"


class MontantIncoherent(ValueError):
    """Le montant encaissé diffère du montant dû — voir l'en-tête."""


class EvenementPaiement(BaseModel):
    """Une notification du prestataire, normalisée.

    Volontairement pauvre : ce que le prestataire envoie en plus ne nous regarde
    pas, et le recopier ferait entrer sa forme de charge utile dans notre domaine.
    Le jour où l'on change de prestataire, seul l'adaptateur traduit.
    """

    model_config = ConfigDict(frozen=True)

    #: Notre clé d'idempotence, telle qu'elle revient. Souvent absente : c'est
    #: précisément la raison des deux autres stratégies.
    identifiant_produit: str | None = None

    #: La référence du prestataire.
    reference_externe: str | None = None

    #: `True` = encaissé, `False` = refusé. Le vocabulaire du prestataire est
    #: traduit par l'adaptateur : le domaine ne connaît ni « SUCCESS », ni
    #: « FAILED », ni les six variantes qui existent selon les versions.
    reussi: bool

    montant: Decimal | None = Field(default=None, ge=0)
    telephone: str | None = None
    identifiant_marchand: str | None = None
    motif: str | None = None

    @field_validator("telephone", mode="before")
    @classmethod
    def _normaliser(cls, valeur: object) -> object:
        """Un numéro non normalisé ferait échouer la troisième stratégie sans
        que rien ne le signale. On tolère l'échec de normalisation ici — le
        prestataire peut envoyer n'importe quoi — mais on ne prétend pas avoir
        un numéro exploitable quand ce n'en est pas un."""
        if not isinstance(valeur, str) or not valeur.strip():
            return None
        try:
            return normaliser_telephone(valeur)
        except ValueError:
            return None


class Paiement(BaseModel):
    """Un encaissement, de son initiation à son sort définitif."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)

    #: Ce que ce paiement règle : une souscription ou une proforma. C'est lui qui
    #: décide de la **suite**, jamais le format de `reference_reglee`.
    nature: NaturePaiement = NaturePaiement.SOUSCRIPTION

    #: La référence de l'objet réglé — une souscription, ou le numéro d'une
    #: proforma selon `nature`.
    #:
    #: ⚠️ **Ce champ s'appelait `souscription`.** Il a été renommé le jour où un
    #: paiement a pu régler autre chose : un champ nommé `souscription` qui porte
    #: un numéro de proforma est exactement le défaut du pas 21, où le message
    #: libre d'un prospect voyageait dans un champ nommé `region_demande`. Un nom
    #: qui ment coûte plus cher qu'une migration.
    reference_reglee: str = Field(min_length=1)

    #: Envoyée au prestataire comme identifiant de produit, pour qu'il déduplique
    #: de son côté. C'est la première ligne de défense contre le double-débit :
    #: deux appels portant la même clé ne produisent qu'une opération.
    cle_idempotence: str = Field(min_length=8)

    montant: Decimal = Field(gt=0)
    telephone: str = Field(min_length=9)

    #: La mensualité que ce paiement règle — `souscription/AAAAMMJJ`.
    #:
    #: `None` pour le paiement de mise en route, engagé avant que l'échéancier
    #: n'existe. C'est ce champ qui empêche de prélever deux fois le même mois :
    #: sans lui, deux appels concurrents pour mai produiraient deux débits, et
    #: rien ne dirait lequel est de trop.
    echeance: str | None = None

    statut: StatutPaiement = StatutPaiement.EN_ATTENTE
    initie_le: datetime

    #: Rendue par le prestataire à l'initiation, puis employée pour la deuxième
    #: stratégie de rapprochement.
    reference_externe: str | None = None

    confirme_le: datetime | None = None
    solde_le: datetime | None = None
    motif: str | None = None
    strategie: StrategieRapprochement | None = None

    #: Nombre d'interrogations de statut faites par la réconciliation. Sert à
    #: repérer les opérations sur lesquelles on s'acharne, **et à espacer les
    #: suivantes** : voir `a_interroger`.
    verifications: int = Field(default=0, ge=0)

    #: Quand le prestataire a été interrogé pour la dernière fois. `None` tant
    #: qu'il ne l'a jamais été.
    #:
    #: ⚠️ Non promu en colonne : la réconciliation charge les paiements en attente,
    #: qu'elle chargeait déjà, et lit ce champ en mémoire. Une colonne serait une
    #: migration et un index à décider pour une lecture qui ne coûte rien.
    derniere_verification: datetime | None = None

    @field_validator("telephone", mode="before")
    @classmethod
    def _normaliser(cls, valeur: object) -> object:
        return normaliser_telephone(valeur) if isinstance(valeur, str) else valeur

    # ── Ce que le paiement sait dire de lui-même ────────────────────────────

    @computed_field
    @property
    def encaisse(self) -> bool:
        return self.statut == StatutPaiement.VALIDE

    @computed_field
    @property
    def clos(self) -> bool:
        """Plus rien ne peut lui arriver. Sérialisé parce que c'est ce champ qui
        décide si l'écran propose de recommencer."""
        return self.statut != StatutPaiement.EN_ATTENTE

    def perime(self, a_l_instant: datetime) -> bool:
        return (
            self.statut == StatutPaiement.EN_ATTENTE
            and a_l_instant - self.initie_le >= DELAI_EXPIRATION
        )

    # ── Transitions ─────────────────────────────────────────────────────────

    def valider(
        self,
        a_l_instant: datetime,
        *,
        strategie: StrategieRapprochement,
        reference_externe: str | None = None,
        montant_constate: Decimal | None = None,
    ) -> Paiement:
        """Encaisse. **Rejouable** : un paiement déjà validé se rend inchangé.

        C'est la garantie centrale. Le prestataire renvoie plusieurs fois la même
        notification — c'est normal, et son mécanisme de reprise l'y pousse. Lever
        ici obligerait l'adaptateur à distinguer les rejeux des vraies erreurs, et
        cette distinction finirait par se tromper.

        Le montant est contrôlé quand il est fourni. Un écart n'est pas un
        paiement partiel — voir l'en-tête.
        """
        if self.statut == StatutPaiement.VALIDE:
            return self
        if self.statut in (StatutPaiement.REJETE, StatutPaiement.EXPIRE):
            raise ValueError(
                f"paiement {self.identifiant} déjà {self.statut} le {self.solde_le} : "
                "il ne se valide plus. Un encaissement arrivé après un rejet doit être "
                "traité à la main, pas absorbé silencieusement."
            )
        if montant_constate is not None and montant_constate != self.montant:
            raise MontantIncoherent(
                f"paiement {self.identifiant} : {montant_constate} encaissés pour "
                f"{self.montant} dus. Un montant différent n'est pas un paiement "
                "partiel — la somme reste à traiter à la main."
            )
        return self.model_copy(
            update={
                "statut": StatutPaiement.VALIDE,
                "confirme_le": a_l_instant,
                "solde_le": a_l_instant,
                "strategie": strategie,
                "reference_externe": reference_externe or self.reference_externe,
            }
        )

    def rejeter(self, a_l_instant: datetime, motif: str) -> Paiement:
        """Refus du prestataire ou de l'abonné. Rejouable comme la validation."""
        if self.statut != StatutPaiement.EN_ATTENTE:
            return self
        return self.model_copy(
            update={
                "statut": StatutPaiement.REJETE,
                "solde_le": a_l_instant,
                "motif": motif,
            }
        )

    def expirer(self, a_l_instant: datetime) -> Paiement:
        """Péremption après vingt-quatre heures d'attente."""
        if not self.perime(a_l_instant):
            return self
        return self.model_copy(
            update={
                "statut": StatutPaiement.EXPIRE,
                "solde_le": a_l_instant,
                "motif": (
                    f"aucune confirmation reçue en {DELAI_EXPIRATION.total_seconds() // 3600:.0f} "
                    "heures ; l'opération est réputée abandonnée"
                ),
            }
        )

    def avec_reference(self, reference: str) -> Paiement:
        """Enregistre la référence rendue par le prestataire à l'initiation.

        Sans elle, la deuxième stratégie de rapprochement n'existerait pas.
        """
        return self.model_copy(update={"reference_externe": reference})

    def apres_verification(self, a_l_instant: datetime) -> Paiement:
        """Une interrogation de plus, et la date de celle-ci."""
        return self.model_copy(
            update={
                "verifications": self.verifications + 1,
                "derniere_verification": a_l_instant,
            }
        )

    def espacement(self, delai_minimal: timedelta) -> timedelta:
        """Combien attendre avant la prochaine interrogation.

        Le délai double à chaque tentative, et plafonne. Le calcul est ici et non
        chez l'appelant : c'est une propriété de l'opération, et deux appelants en
        donneraient deux versions.
        """
        double = delai_minimal * (2**self.verifications)
        return min(double, ESPACEMENT_MAXIMAL)

    def a_interroger(self, a_l_instant: datetime, delai_minimal: timedelta) -> bool:
        """Faut-il demander son statut au prestataire, maintenant ?

        ─────────────────────────────────────────────────────────────────────────
        ⚠️ **SANS ESPACEMENT, LE FILET DEVIENT UNE TEMPÊTE D'APPELS.**

        La réconciliation était une route qu'un humain devait cliquer : elle
        passait rarement, et interroger chaque paiement en attente à chaque
        passage ne coûtait rien. Branchée à l'ordonnanceur, elle passe toutes les
        cinq minutes, et une opération que l'abonné a abandonnée resterait en
        attente vingt-quatre heures : **288 interrogations pour un seul paiement
        que personne ne validera jamais.**

        Le délai double donc à chaque tentative, et plafonne à une heure. Sur les
        mêmes vingt-quatre heures : 5, 10, 20, 40 minutes, puis une fois par
        heure, soit **vingt-six** interrogations au lieu de 288.

        ⚠️ Vingt-six, et non vingt-sept comme l'estimation le laissait croire. Le
        chiffre est **compté** par `test_espacement_des_interrogations.py`, qui
        déroule vingt-quatre heures de passages ; il n'est pas calculé de tête.

        ⚠️ Le premier examen reste à `delai_minimal` : le filet sert les cas où la
        notification **n'est pas venue**, pas ceux où elle n'est **pas encore**
        venue.
        ─────────────────────────────────────────────────────────────────────────
        """
        if a_l_instant - self.initie_le < delai_minimal:
            return False
        if self.derniere_verification is None:
            return True
        return a_l_instant - self.derniere_verification >= self.espacement(
            delai_minimal
        )


def rapprocher(
    evenement: EvenementPaiement,
    en_attente: Sequence[Paiement],
    a_l_instant: datetime,
) -> tuple[Paiement, StrategieRapprochement] | None:
    """Retrouve l'encaissement que cette notification concerne.

    Trois stratégies, essayées dans cet ordre — voir l'en-tête pour le pourquoi.
    Rend `None` quand aucune ne trouve : c'est un encaissement non affecté, à
    traiter à la main, et il ne faut surtout pas le rattacher au hasard.

    ⚠️ Ne filtre pas sur le statut : c'est l'appelant qui décide. Un rejeu portant
    sur un paiement déjà validé doit **retrouver ce paiement**, pour que la
    validation puisse se déclarer sans effet. Le lui cacher ferait retomber la
    notification dans le cas « non affecté », et l'on croirait à un encaissement
    orphelin là où tout s'est bien passé.
    """
    if evenement.identifiant_produit:
        trouve = next(
            (p for p in en_attente if p.cle_idempotence == evenement.identifiant_produit),
            None,
        )
        if trouve is not None:
            return trouve, StrategieRapprochement.IDENTIFIANT

    if evenement.reference_externe:
        trouve = next(
            (p for p in en_attente if p.reference_externe == evenement.reference_externe),
            None,
        )
        if trouve is not None:
            return trouve, StrategieRapprochement.REFERENCE_EXTERNE

    if evenement.telephone:
        candidats = [
            p
            for p in en_attente
            if p.telephone == evenement.telephone
            and p.statut == StatutPaiement.EN_ATTENTE
            and a_l_instant - p.initie_le <= FENETRE_RAPPROCHEMENT
            # Le montant est exigé — voir l'en-tête : sans lui, deux souscriptions
            # engagées depuis le même téléphone dans la demi-heure se croiseraient.
            and (evenement.montant is None or p.montant == evenement.montant)
        ]
        if candidats:
            # Le plus récent : si deux opérations subsistent malgré le filtre de
            # montant, la dernière engagée est celle que l'abonné vient de payer.
            return (
                max(candidats, key=lambda p: p.initie_le),
                StrategieRapprochement.TELEPHONE_ET_RECENCE,
            )

    return None
