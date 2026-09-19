"""Les ports du contexte M.

─────────────────────────────────────────────────────────────────────────────────
CE QUE `DepotPaiements` EXIGE, ET QUI N'EST PAS NÉGOCIABLE

`enregistrer` doit être **atomique**, et `en_attente()` doit rendre une vue
cohérente. C'est là que se joue la protection contre le double-comptage : deux
notifications concurrentes portant sur le même encaissement doivent aboutir à une
seule validation.

Une réalisation en mémoire ne peut le garantir que dans un processus. En base, ce
sera une transaction avec verrou sur la ligne du paiement — et c'est écrit ici
pour que la migration ne l'oublie pas.

`FournisseurPaiement` EST DÉLIBÉRÉMENT PAUVRE

Quatre méthodes, aucune n'expose le vocabulaire du prestataire. Le jour où l'on
change d'opérateur — ou de pays —, seul l'adaptateur bouge. Cette frontière n'est
pas théorique : le module d'origine, en Django, est écrit contre Tara, et c'est
précisément ce couplage qu'on refuse de reproduire ici.

`interroger_statut` MÉRITE SON EXISTENCE

Une notification entrante n'est pas une preuve — le prestataire ne les signe pas.
`interroger_statut` est l'appel **sortant**, vers une adresse connue, sur une
connexion que nous avons ouverte. C'est lui qui fait foi, et c'est lui que la
réconciliation emploie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from app.contextes.souscription.domaine.devis import Devis
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
)
from app.contextes.souscription.domaine.offre import Service
from app.contextes.souscription.domaine.paiements import EvenementPaiement, Paiement
from app.contextes.souscription.domaine.proforma import Proforma
from app.contextes.souscription.domaine.qualification import Qualification
from app.contextes.souscription.domaine.relance import SuiviDeRelance
from app.contextes.souscription.domaine.souscriptions import Souscription

__all__ = [
    "DepotDevis",
    "DepotDossiersCommerciaux",
    "DepotPaiements",
    "DepotProformas",
    "DepotQualifications",
    "DepotSuivisDeRelance",
    "DepotServices",
    "DepotSouscriptions",
    "FournisseurPaiement",
    "InitiationPaiement",
    "ServiceOuvertureAcces",
]


class DepotServices(Protocol):
    """Le catalogue de l'offre.

    ⚠️ `charger` prend une date, comme le référentiel normatif : un barème se lit
    à une date ou ne se lit pas. Rendre « le catalogue courant » ferait afficher
    le tarif d'aujourd'hui sur un devis d'il y a trois semaines.
    """

    def charger(self, a_la_date: date) -> list[Service]: ...


class DepotDevis(Protocol):
    def lire(self, reference: str) -> Devis:
        """Lève si le devis n'existe pas."""
        ...

    def enregistrer(self, devis: Devis) -> None: ...

    def lister(self, *, courriel: str | None = None) -> list[Devis]: ...


class DepotDossiersCommerciaux(Protocol):
    """Les dossiers du parcours d'acquisition.

    ─────────────────────────────────────────────────────────────────────────
    TROIS LECTURES, ET PAS UNE DE PLUS

    `par_telephone` sert la détection de doublon. Elle rend ce qui existe pour
    un numéro depuis une date, et **ne dit pas** ce qu'être un doublon veut
    dire : cette règle est du métier, elle vit dans `doublon_parmi`, et elle
    changera. Un dépôt qui la porterait obligerait à modifier l'adaptateur
    SQL le jour où le critère s'enrichit.

    `ouverts` sert la veille et la console. Filtrée par état parce que
    l'ordonnanceur balaye état par état, avec un délai propre à chacun ;
    charger tous les dossiers ouverts pour n'en garder qu'un dixième ferait
    grossir le balayage avec le portefeuille.

    ⚠️ `ouverts` rend les dossiers **ouverts**, jamais les fermés. Un dossier
    payé n'a plus rien à faire dans une file de travail, et un dossier classé
    non plus. C'est aussi ce qui empêche la veille de remonter deux ans de
    dossiers le jour où quelqu'un ajoute un délai par erreur.
    ─────────────────────────────────────────────────────────────────────────
    """

    def lire(self, reference: str) -> DossierCommercial:
        """Lève si le dossier n'existe pas."""
        ...

    def enregistrer(self, dossier: DossierCommercial) -> None: ...

    def par_telephone(
        self, telephone: str, *, depuis: datetime
    ) -> list[DossierCommercial]:
        """Les dossiers dont une demande porte ce numéro, déposée depuis `depuis`.

        Le numéro est attendu sous sa forme canonique. Le normaliser ici serait
        une seconde normalisation, donc une seconde occasion de diverger de
        celle de la demande.
        """
        ...

    def ouverts(self, *, etat: EtatDossier | None = None) -> list[DossierCommercial]:
        """Les dossiers encore en cours, du plus ancien dans son état au plus
        récent : c'est celui qui attend depuis le plus longtemps qu'on traite
        d'abord."""
        ...


class DepotProformas(Protocol):
    """Les documents commerciaux chiffrés.

    ⚠️ `dernier_numero` sert à poursuivre la série, et **il peut mentir**. Entre
    la lecture et l'écriture, une autre requête a pu émettre : c'est la contrainte
    d'unicité qui arbitre, et l'appelant doit traiter le conflit en relisant.

    Verrouiller la série entière sérialiserait toutes les émissions du cabinet
    pour une garantie identique.

    `a_relancer` sert l'ordonnanceur, qui balaye les transmises sans réponse à
    trois, sept et quatorze jours. De la plus ancienne d'abord : c'est le client
    qui attend depuis le plus longtemps qu'on relance en premier.
    """

    def lire(self, numero: str) -> Proforma:
        """Lève si la proforma n'existe pas."""
        ...

    def enregistrer(self, proforma: Proforma) -> None: ...

    def par_dossier(self, dossier: str) -> list[Proforma]:
        """De la plus ancienne à la plus récente : l'ordre du fil des versions."""
        ...

    def dernier_numero(self, serie: str, annee: int) -> str | None: ...

    def a_relancer(self) -> list[Proforma]: ...


class DepotQualifications(Protocol):
    """Ce qu'on a appris d'un prospect, dossier par dossier.

    ⚠️ **`trouver` rend `None` plutôt que de lever.** Une qualification absente veut
    dire « pas encore commencée », qui est l'état de tout dossier neuf : ce n'est pas
    une anomalie. Lever obligerait chaque appelant à rattraper une exception pour
    ouvrir une qualification vide, ce qu'il fait de toute façon.
    """

    def trouver(self, dossier: str) -> Qualification | None: ...

    def enregistrer(self, qualification: Qualification) -> None: ...


class DepotSuivisDeRelance(Protocol):
    """Ce qui a déjà été relancé, proforma par proforma.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **UN PORT À PART DE `DepotProformas`, ET C'EST LA MÊME RAISON QUE LA TABLE.**

    La proforma est figée et vaut contrat : ce qui a été envoyé à un client doit
    ressortir à l'identique dix ans plus tard. Le compteur de relances, lui, se
    réécrit à chaque passage. Les mêler ferait réécrire un document contractuel
    pour une raison qui n'a rien de contractuel.

    `tous` rend un dictionnaire indexé par numéro parce que c'est exactement ce que
    `relances_dues` attend, et parce qu'un balayage lit **tous** les suivis d'un
    coup avant de décider : les chercher un par un ferait une requête par proforma
    transmise, soit des centaines par passage sur un cabinet actif.

    ⚠️ Un suivi absent n'est pas une erreur : il veut dire « jamais relancée », ce
    qui est l'état de toute proforma le jour de sa transmission. L'appelant
    fabrique alors un suivi vide, et c'est pourquoi ce port n'a pas de `lire` qui
    lève.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def tous(self) -> dict[str, SuiviDeRelance]:
        """Tous les suivis du cabinet, indexés par numéro de proforma."""
        ...

    def enregistrer(self, suivi: SuiviDeRelance) -> None: ...


class DepotSouscriptions(Protocol):
    def lire(self, reference: str) -> Souscription: ...

    def enregistrer(self, souscription: Souscription) -> None: ...

    def a_activer(self) -> list[Souscription]:
        """Les souscriptions payées et non activées.

        Cette méthode existe pour une raison précise : c'est **la** requête de
        surveillance. Un client qui a payé et n'a rien reçu doit apparaître sans
        qu'on ait à le chercher — voir l'en-tête de `souscriptions.py`.
        """
        ...

    def lister(self) -> list[Souscription]: ...


class DepotPaiements(Protocol):
    def lire(self, identifiant: str) -> Paiement: ...

    def par_souscription(self, souscription: str) -> list[Paiement]: ...

    def en_attente(self, *, depuis: datetime | None = None) -> list[Paiement]:
        """Les encaissements non soldés, pour la réconciliation."""
        ...

    def rapprochables(self) -> list[Paiement]:
        """Les candidats au rapprochement d'une notification entrante.

        Inclut les paiements **déjà validés**, et ce n'est pas une négligence :
        un rejeu doit retrouver son paiement pour que la validation se déclare
        sans effet. Le lui cacher ferait passer un rejeu ordinaire pour un
        encaissement orphelin — voir `rapprocher()`.
        """
        ...

    def enregistrer(self, paiement: Paiement) -> None:
        """⚠️ Doit être atomique — voir l'en-tête du module."""
        ...


class InitiationPaiement(Protocol):
    """Ce que le prestataire rend à l'initiation."""

    @property
    def reference_externe(self) -> str | None: ...

    @property
    def accepte(self) -> bool: ...

    @property
    def message(self) -> str | None: ...


class FournisseurPaiement(Protocol):
    """Le prestataire d'encaissement mobile."""

    def initier(
        self,
        *,
        montant: Decimal,
        telephone: str,
        cle_idempotence: str,
        libelle: str,
    ) -> InitiationPaiement:
        """Demande le débit. L'abonné saisit ensuite son code sur son téléphone.

        `cle_idempotence` est transmise au prestataire comme identifiant de
        produit : deux appels portant la même clé ne doivent produire qu'une
        opération de son côté.
        """
        ...

    def interroger_statut(
        self, *, cle_idempotence: str, reference_externe: str | None
    ) -> EvenementPaiement | None:
        """Appel sortant. **C'est lui qui fait foi** — voir l'en-tête."""
        ...

    def lire_notification(self, charge_utile: dict[str, Any]) -> EvenementPaiement | None:
        """Traduit une notification entrante en évènement de domaine.

        Rend `None` sur une charge utile qu'on ne sait pas lire, plutôt que de
        lever : une notification illisible ne doit pas produire d'erreur qui
        déclencherait des renvois en boucle chez le prestataire.
        """
        ...

    def identifiant_marchand(self) -> str | None:
        """Sert à écarter les notifications destinées à un autre marchand. Le
        prestataire ne signant pas ses appels, c'est l'un des rares contrôles
        disponibles."""
        ...


class ServiceOuvertureAcces(Protocol):
    """Ouvrir l'accès de l'adhérent à son dossier, et lui envoyer son lien.

    ─────────────────────────────────────────────────────────────────────────
    POURQUOI CE PORT PLUTÔT QU'UN APPEL DIRECT AU CONTEXTE K

    M **peut** lire K : celui-ci appartient au socle, et le graphe l'autorise.
    L'appel direct serait donc légal. Il serait néanmoins mauvais, pour deux
    raisons.

    D'abord la lisibilité du cas d'usage. Créer un compte demande six dépôts et
    un service de dérivation d'empreinte ; les faire traverser la signature de
    `activer_souscription` la rendrait illisible, et l'on ne verrait plus ce que
    le cas d'usage **fait** sous ce qu'il **transporte**.

    Ensuite la nature de la dépendance. Ce dont M a besoin n'est pas « le
    contexte K » : c'est « quelqu'un qui sait ouvrir un accès ». Le jour où un
    cabinet client délègue son identité à un annuaire externe, c'est cet
    adaptateur qui change, et rien d'autre.

    CE PORT DOIT LEVER PLUTÔT QUE DE RENDRE UN ÉCHEC SILENCIEUX

    Une exception laisse la souscription à `PAYEE`, donc visible dans la liste
    des encaissements non activés. Un `None` rendu discrètement la laisserait
    elle aussi à `PAYEE`, mais sans qu'aucun message ne dise pourquoi.
    ─────────────────────────────────────────────────────────────────────────
    """

    def ouvrir(self, souscription: Souscription, a_l_instant: datetime) -> str:
        """Rend l'identifiant du compte créé. Lève si l'accès ne peut pas être ouvert."""
        ...
