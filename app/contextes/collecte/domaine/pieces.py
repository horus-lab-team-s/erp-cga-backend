"""La pièce justificative et son cycle de vie.

─────────────────────────────────────────────────────────────────────────────────
LE POINT DE FRICTION RÉEL DU MÉTIER

Tout le reste du système est du calcul, et le calcul ne se trompe pas deux fois de
la même façon. La collecte, elle, dépend d'un tiers : l'adhérent. C'est là que les
dossiers s'enlisent, c'est là que les échéances se manquent, et c'est la seule
partie du produit dont la qualité ne dépend pas du cabinet.

D'où deux partis pris qui commandent tout ce module.

**LE CANAL N'AFFECTE PAS LA VALEUR PROBANTE.** Une facture photographiée et
envoyée par WhatsApp vaut exactement ce que vaut la même facture déposée sur le
portail : ce qui fait foi, c'est le document, pas le tuyau. Traiter WhatsApp comme
un canal de seconde zone reviendrait à refuser le seul canal que beaucoup de TPE
utiliseront réellement. Le canal ne sert qu'à deux choses : savoir par où relancer,
et mesurer d'où vient le retard.

**L'ÉTAT « EN ANOMALIE » N'EXISTE PAS.** Le cycle de vie décrit un *traitement*,
jamais une *qualité*. La qualité est dite par le rapport du contexte D, qui est
daté et immuable. Les confondre rendrait impossible le cas le plus courant qui
soit : une pièce parfaitement comptabilisée dont la charge sera réintégrée au
résultat fiscal. Elle est non conforme, et son traitement est pourtant achevé.

DEUX DATES, ET ELLES NE DISENT PAS LA MÊME CHOSE

`depose_le` est déclarée par l'expéditeur ; `recue_le` est horodatée par le
système. En mode hors ligne — une pièce photographiée dans un atelier sans réseau
et synchronisée neuf jours plus tard — l'écart entre les deux est le délai de
transmission, et c'est la mesure la plus utile que le cabinet puisse produire sur
un adhérent. Une seule date les confondrait et ferait disparaître la mesure.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.partage.copie import transiter

__all__ = [
    "CanalDepot",
    "EtatPiece",
    "PieceJustificative",
    "TransitionRefusee",
    "TypePiece",
    "empreinte",
]

#: ─────────────────────────────────────────────────────────────────────────────
#: POURQUOI CERTAINES PROPRIÉTÉS PORTENT `@computed_field`
#:
#: Une `@property` d'un modèle Pydantic **ne franchit pas la frontière HTTP** :
#: elle est calculée en mémoire et absente du JSON. Les écrans qui en avaient
#: besoin devraient alors la recalculer eux-mêmes — et le jour où la règle change,
#: le front et le back diraient deux choses différentes sans que personne ne sache
#: lequel a raison.
#:
#: Les propriétés qui portent une **décision métier** sont donc déclarées
#: `@computed_field` : elles voyagent avec l'objet, et il n'existe qu'une
#: implémentation de la règle. Les autres restent de simples propriétés.
#: ─────────────────────────────────────────────────────────────────────────────


class TransitionRefusee(ValueError):
    """Le cycle de vie interdit ce passage.

    Levée plutôt que rendue sous forme de booléen : une transition refusée
    silencieusement laisserait une pièce dans un état incohérent, et l'on ne s'en
    apercevrait qu'à la clôture.
    """


class CanalDepot(StrEnum):
    """Par où la pièce est entrée.

    Sert à relancer par le bon moyen et à mesurer les délais par canal — pas à
    hiérarchiser la valeur probante.
    """

    PORTAIL = "PORTAIL"
    MOBILE = "MOBILE"
    WHATSAPP = "WHATSAPP"
    COURRIEL = "COURRIEL"
    #: Relevé bancaire ou historique Mobile Money importé en lot.
    IMPORT_BANCAIRE = "IMPORT_BANCAIRE"
    #: Saisie par le cabinet à partir d'un original papier remis en main propre.
    DEPOT_CABINET = "DEPOT_CABINET"


class TypePiece(StrEnum):
    """Nature du document, telle que déclarée puis confirmée.

    C'est ce type qui décide du jeu de règles appliqué par le contexte D et de
    l'écriture proposée par le contexte E. Une erreur de type ici se propage
    jusqu'à la déclaration : d'où `INDETERMINE`, qui est un aveu honnête plutôt
    qu'une valeur par défaut arbitraire.
    """

    INDETERMINE = "INDETERMINE"
    FACTURE_ACHAT = "FACTURE_ACHAT"
    FACTURE_VENTE = "FACTURE_VENTE"
    RECU = "RECU"
    RELEVE_BANCAIRE = "RELEVE_BANCAIRE"
    RELEVE_MOBILE_MONEY = "RELEVE_MOBILE_MONEY"
    BULLETIN_PAIE = "BULLETIN_PAIE"
    QUITTANCE_IMPOT = "QUITTANCE_IMPOT"
    CONTRAT = "CONTRAT"
    AUTRE = "AUTRE"


#: Types qui ne donnent pas lieu à une écriture par eux-mêmes. Une pièce de ce
#: type peut être archivée sans jamais être comptabilisée, et ce n'est pas un
#: oubli : un contrat de bail justifie des écritures, il n'en est pas une.
TYPES_NON_COMPTABILISABLES = frozenset(
    {TypePiece.CONTRAT, TypePiece.RELEVE_BANCAIRE, TypePiece.RELEVE_MOBILE_MONEY}
)


class EtatPiece(StrEnum):
    """Où en est le traitement de la pièce, et rien d'autre.

    Cinq états, progression stricte. Le rejet de conformité n'en fait pas partie —
    voir l'en-tête du module.
    """

    RECUE = "RECUE"
    LUE = "LUE"
    RAPPROCHEE = "RAPPROCHEE"
    COMPTABILISEE = "COMPTABILISEE"
    ARCHIVEE = "ARCHIVEE"


_RANG: dict[EtatPiece, int] = {
    EtatPiece.RECUE: 0,
    EtatPiece.LUE: 1,
    EtatPiece.RAPPROCHEE: 2,
    EtatPiece.COMPTABILISEE: 3,
    EtatPiece.ARCHIVEE: 4,
}


def empreinte(contenu: bytes) -> str:
    """Empreinte SHA-256 du fichier déposé.

    Elle sert à une seule chose, mais elle la fait sans erreur possible :
    reconnaître qu'un fichier déjà reçu revient une seconde fois. Deux fichiers de
    même empreinte sont le même fichier — ce n'est pas une présomption, c'est une
    identité. Voir `domaine/doublons.py` pour le cas, bien plus fréquent, où c'est
    la même facture mais pas le même fichier.
    """
    return hashlib.sha256(contenu).hexdigest()


class PieceJustificative(BaseModel):
    """Un document reçu d'un adhérent, et l'état de son traitement."""

    model_config = ConfigDict(frozen=True)

    identifiant: str = Field(min_length=1)
    #: NIU de l'adhérent à qui la pièce appartient.
    entreprise: str = Field(min_length=1)

    canal: CanalDepot
    #: Déclarée par l'expéditeur : la date à laquelle il dit avoir déposé.
    depose_le: date
    #: Horodatée par le système à l'arrivée effective. Seule celle-ci fait foi.
    recue_le: datetime

    type: TypePiece = TypePiece.INDETERMINE
    etat: EtatPiece = EtatPiece.RECUE

    nom_fichier: str | None = None
    empreinte: str | None = None
    taille_octets: int | None = Field(default=None, ge=0)

    #: Référence portée par le document lui-même (numéro de facture). Renseignée
    #: après lecture, jamais avant : avant, on ne sait pas ce qu'on a reçu.
    reference_document: str | None = None
    date_document: date | None = None
    montant_ttc: Decimal | None = None
    emetteur: str | None = None

    #: Mouvement bancaire ou pièce en regard, une fois le rapprochement fait.
    reference_rapprochement: str | None = None
    #: Clé de l'écriture du contexte E, au format « 2026/AC/000042 ».
    reference_ecriture: str | None = None
    #: Rapport de conformité du contexte D. Sa référence seulement : la collecte
    #: ne stocke pas le verdict, elle sait où il est.
    reference_rapport: str | None = None

    motif_archivage: str | None = None
    depose_par: str | None = None
    commentaire: str | None = None

    @model_validator(mode="after")
    def _coherence(self) -> PieceJustificative:
        if self.recue_le.date() < self.depose_le:
            raise ValueError(
                f"{self.identifiant} : réception {self.recue_le.date()} antérieure au "
                f"dépôt déclaré {self.depose_le}. Une pièce ne peut pas arriver avant "
                "d'avoir été envoyée — soit l'horloge du terminal est fausse, soit la "
                "date de dépôt est saisie à la main et invraisemblable."
            )
        if self.etat is EtatPiece.COMPTABILISEE and self.reference_ecriture is None:
            raise ValueError(
                f"{self.identifiant} : une pièce comptabilisée porte la clé de son "
                "écriture. Sans elle, la piste d'audit est rompue dans le sens "
                "pièce → écriture, et c'est ce sens-là que le vérificateur emprunte."
            )
        if self.etat is EtatPiece.RAPPROCHEE and self.reference_rapprochement is None:
            raise ValueError(
                f"{self.identifiant} : une pièce rapprochée porte la référence du "
                "mouvement en regard."
            )
        if (
            self.etat is EtatPiece.ARCHIVEE
            and self.reference_ecriture is None
            and not self.motif_archivage
        ):
            raise ValueError(
                f"{self.identifiant} : archiver une pièce jamais comptabilisée exige un "
                "motif. C'est la seule trace qui expliquera, trois ans plus tard, "
                "pourquoi ce document n'a produit aucune écriture."
            )
        return self

    # ── Lectures ────────────────────────────────────────────────────────────

    @property
    def rang(self) -> int:
        return _RANG[self.etat]

    @computed_field
    @property
    def traitee(self) -> bool:
        """La pièce a produit son écriture, ou a été classée sans suite."""
        return self.rang >= _RANG[EtatPiece.COMPTABILISEE]

    @property
    def en_attente_de_traitement(self) -> bool:
        return not self.traitee

    @computed_field
    @property
    def identifiee(self) -> bool:
        """On sait ce que c'est, de qui, quand et pour combien.

        Tant que ce n'est pas vrai, la pièce ne peut ni être contrôlée par D ni
        être imputée par E : il n'y a rien à contrôler ni à imputer.
        """
        return (
            self.type is not TypePiece.INDETERMINE
            and self.reference_document is not None
            and self.date_document is not None
            and self.montant_ttc is not None
        )

    @computed_field
    @property
    def comptabilisable(self) -> bool:
        return self.type not in TYPES_NON_COMPTABILISABLES

    def jours_de_transmission(self) -> int:
        """Délai entre le dépôt déclaré et l'arrivée effective.

        Zéro pour un dépôt en ligne, potentiellement plusieurs jours pour une
        capture hors ligne synchronisée plus tard. C'est cet écart, et non la date
        de la facture, qui mesure la friction du canal.
        """
        return (self.recue_le.date() - self.depose_le).days

    def anciennete(self, a_la_date: date) -> int:
        """Jours écoulés depuis la réception. Le vrai indicateur d'enlisement."""
        return (a_la_date - self.recue_le.date()).days

    def retard_de_remise(self) -> int | None:
        """Jours entre l'émission du document et sa remise au cabinet.

        `None` tant que la date du document est inconnue. C'est cette mesure-là
        qu'on oppose à un adhérent qui reproche au cabinet un dépôt tardif : une
        facture de janvier remise le 8 avril ne pouvait pas figurer dans la
        déclaration de janvier.
        """
        if self.date_document is None:
            return None
        return (self.recue_le.date() - self.date_document).days

    # ── Transitions ─────────────────────────────────────────────────────────

    def avancer(self, etat: EtatPiece, **details) -> PieceJustificative:
        """Fait progresser la pièce. Rend une copie ; l'originale est gelée.

        Le retour en arrière est refusé. Une pièce comptabilisée ne redevient pas
        « reçue » : on contre-passe l'écriture, ce qui laisse deux traces au lieu
        d'en effacer une. C'est la même discipline qu'au contexte E, et pour la
        même raison.
        """
        if _RANG[etat] < self.rang:
            raise TransitionRefusee(
                f"{self.identifiant} : on ne revient pas de {self.etat} à {etat}. "
                "Défaire un traitement se fait par un acte qui laisse sa propre trace, "
                "pas par un retour d'état."
            )
        return transiter(self, etat=etat, **details)

    def marquer_lue(self, **details) -> PieceJustificative:
        """Passe à LUE une fois le document identifié.

        La lecture est refusée tant que la pièce n'est pas identifiée : marquer
        « lue » un document dont on ignore le type, le numéro et le montant
        ferait croire à un traitement qui n'a pas eu lieu, et la pièce
        disparaîtrait des écrans de travail sans avoir été traitée.
        """
        candidate = transiter(self, **details) if details else self
        if not candidate.identifiee:
            manquants = [
                nom
                for nom, valeur in (
                    ("type", candidate.type is not TypePiece.INDETERMINE),
                    ("reference_document", candidate.reference_document is not None),
                    ("date_document", candidate.date_document is not None),
                    ("montant_ttc", candidate.montant_ttc is not None),
                )
                if not valeur
            ]
            raise TransitionRefusee(
                f"{self.identifiant} : lecture impossible, il manque {manquants}. "
                "Une pièce non identifiée reste à l'état RECUE, où elle continue "
                "d'apparaître dans le travail à faire."
            )
        return candidate.avancer(EtatPiece.LUE)

    def comptabiliser(self, reference_ecriture: str) -> PieceJustificative:
        """Rattache la pièce à son écriture.

        Refusé pour les types qui ne produisent pas d'écriture : un relevé
        bancaire alimente le rapprochement, il n'est pas comptabilisé lui-même.
        """
        if not self.comptabilisable:
            raise TransitionRefusee(
                f"{self.identifiant} : un document de type {self.type} ne produit pas "
                "d'écriture par lui-même. Il s'archive avec son motif."
            )
        return self.avancer(EtatPiece.COMPTABILISEE, reference_ecriture=reference_ecriture)

    def archiver(self, motif: str | None = None) -> PieceJustificative:
        """Clôt le traitement.

        Une pièce comptabilisée s'archive sans motif — son écriture explique tout.
        Une pièce jamais comptabilisée en exige un.
        """
        return self.avancer(EtatPiece.ARCHIVEE, motif_archivage=motif or self.motif_archivage)
