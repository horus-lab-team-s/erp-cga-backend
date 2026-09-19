"""Traiter une notification de paiement, puis ouvrir l'accès.

─────────────────────────────────────────────────────────────────────────────────
CE TRAITEMENT NE LÈVE JAMAIS

Il rend toujours un `ResultatEncaissement`, y compris quand la charge utile est
illisible, quand le rapprochement échoue, ou quand l'ouverture d'accès casse.

La raison est mécanique : le prestataire renvoie sa notification tant qu'il ne
reçoit pas de réponse satisfaisante. Une erreur non rattrapée produit une réponse
d'erreur, donc un renvoi, donc une nouvelle erreur — une boucle qui ne s'arrête
qu'à l'épuisement de ses tentatives, en laissant des traces d'erreur partout et
sans que le problème réel soit jamais traité.

On répond donc toujours favorablement, et l'on enregistre ce qui s'est passé.
C'est le comportement du module d'origine, et il est délibéré.

TROIS ISSUES, ET ELLES NE SE VALENT PAS

**Rapproché et validé** — le cas normal. L'accès est ouvert dans la foulée.

**Rapproché et rejeté** — l'abonné a refusé, ou le montant ne correspond pas. La
souscription est abandonnée, rien n'est ouvert.

**Non rapproché** — un encaissement a peut-être eu lieu, et l'on ne sait pas à
quoi le rattacher. C'est la seule des trois qui réclame une intervention humaine,
et c'est pour cela qu'elle est journalisée sous une action distincte,
`paiement.non_affecte` : elle doit se chercher en une requête.

CE MODULE NE SAIT PLUS CE QU'IL ENCAISSE, ET C'EST CE QUI LE REND RÉUTILISABLE

Le règlement, le rapprochement, l'idempotence, le contrôle de montant et
l'expiration sont imposés par le **prestataire**, pas par le métier : ils sont les
mêmes qu'on règle un abonnement ou une proforma. La **suite** diffère : une
souscription réglée s'active et ouvre un accès ; une proforma réglée ouvre un
tenant.

Tout était écrit pour la souscription seule. `appliquer_evenement` lisait
`souscriptions.lire(paiement.souscription)` sans se demander si c'en était une, et
le parcours d'acquisition — qui vend lui aussi — faisait donc **saisir
l'encaissement à la main** pendant que l'intégration du prestataire tournait à
côté pour l'autre flux.

⚠️ La suite est désormais un `SuiteDuPaiement`, choisi d'après `paiement.nature`.
C'est le même motif d'inversion que l'ordonnanceur et le registre : *le mécanisme
central ne connaît pas ses cas, il consulte une table.* Ajouter un troisième objet
payable n'ouvrira plus ce fichier.

L'ACTIVATION EST SÉPARÉE DE L'ENCAISSEMENT, ET C'EST VOLONTAIRE

Si l'ouverture d'accès échoue, le paiement **reste validé** et la souscription
**reste payée**. On ne rembourse pas, on ne rejette pas, on ne revient pas en
arrière : l'argent est bien arrivé. La souscription apparaît alors dans
`a_activer()`, et le rattrapage consiste à réessayer l'activation — pas à rejouer
le paiement.

Confondre les deux gestes conduirait à rejeter un encaissement réel parce qu'un
serveur de courriel était indisponible.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.contextes.souscription.domaine.paiements import (
    EvenementPaiement,
    MontantIncoherent,
    NaturePaiement,
    Paiement,
    StatutPaiement,
    StrategieRapprochement,
    rapprocher,
)
from app.contextes.souscription.domaine.ports import (
    DepotPaiements,
    DepotSouscriptions,
    FournisseurPaiement,
    ServiceOuvertureAcces,
)
from app.contextes.souscription.domaine.souscriptions import (
    Souscription,
    TransitionRefusee,
)
from app.contextes.transverse.api import JournalAudit

__all__ = [
    "Aboutissement",
    "ResultatEncaissement",
    "SuiteDeSouscription",
    "SuiteDuPaiement",
    "VerificationDIdentite",
    "activer_souscription",
    "appliquer_evenement",
    "traiter_notification",
]

#: Délai entre l'encaissement et la prise d'effet de la prestation. Nul :
#: l'adhérent qui vient de payer doit accéder à son espace immédiatement. Un
#: décalage « le lendemain » produirait exactement l'appel au cabinet qu'on
#: cherche à éviter.
DELAI_PRISE_EFFET = timedelta(0)


class Aboutissement(BaseModel):
    """Ce que la suite d'un paiement a produit, une fois l'argent encaissé.

    ⚠️ `abouti=False` n'annule **jamais** l'encaissement. L'argent est arrivé ; ce
    qui a échoué est la conséquence — l'ouverture d'un accès, celle d'un tenant.
    Voir l'en-tête : confondre les deux gestes conduirait à rejeter un
    encaissement réel parce qu'un serveur de courriel était indisponible.
    """

    model_config = ConfigDict(frozen=True)

    abouti: bool
    #: Le compte ouvert, s'il y en a un. Rendu à l'appelant pour qu'il puisse le
    #: dire sans relire la souscription.
    compte: str | None = None
    message: str


class SuiteDuPaiement(Protocol):
    """Ce qui arrive une fois qu'un paiement est réglé, ou refusé.

    ─────────────────────────────────────────────────────────────────────────────
    DEUX MÉTHODES, ET AUCUNE NE LÈVE

    Le traitement d'une notification ne lève jamais — voir l'en-tête. Une suite
    qui lèverait ferait remonter l'exception jusqu'à la réponse HTTP, donc un
    renvoi du prestataire, donc une boucle. Chaque réalisation rattrape ce qui la
    concerne et le journalise.

    ⚠️ **`encaisser` est rejouable.** Le prestataire renvoie ses notifications, et
    la réconciliation repasse par le même chemin. Une suite qui ouvrirait deux
    tenants pour deux appels identiques annulerait la garantie que la boîte
    d'envoi et les clés d'idempotence construisent en amont.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def encaisser(self, paiement: Paiement, a_l_instant: datetime) -> Aboutissement:
        """L'argent est arrivé. Faire ce que cela déclenche."""
        ...

    def refuser(self, paiement: Paiement, motif: str, a_l_instant: datetime) -> None:
        """L'abonné a refusé, ou le montant ne correspond pas."""
        ...


class SuiteDeSouscription:
    """La suite historique : une souscription réglée s'active et ouvre un accès.

    ─────────────────────────────────────────────────────────────────────────────
    Le code de cette classe est **exactement** celui qui vivait dans
    `appliquer_evenement`, déplacé sans être réécrit. C'était la condition pour
    que le raccordement du parcours d'acquisition ne change rien au flux qui
    fonctionnait déjà : un déplacement se relit, une réécriture se vérifie.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def __init__(
        self,
        *,
        souscriptions: DepotSouscriptions,
        acces: ServiceOuvertureAcces,
        journal: JournalAudit,
    ) -> None:
        self._souscriptions = souscriptions
        self._acces = acces
        self._journal = journal

    def encaisser(self, paiement: Paiement, a_l_instant: datetime) -> Aboutissement:
        souscription = self._souscriptions.lire(paiement.reference_reglee).encaisser(
            a_l_instant
        )
        self._souscriptions.enregistrer(souscription)
        # ⚠️ Pas 83 : aucune vérification d'identité n'a eu lieu, et le traitement
        # automatique ne peut pas en faire. Voir `_activer`.
        active, compte, message = _activer(
            souscription,
            souscriptions=self._souscriptions,
            acces=self._acces,
            journal=self._journal,
            a_l_instant=a_l_instant,
            verification=None,
        )
        return Aboutissement(abouti=active, compte=compte, message=message)

    def refuser(self, paiement: Paiement, motif: str, a_l_instant: datetime) -> None:
        souscription = self._souscriptions.lire(paiement.reference_reglee)
        try:
            self._souscriptions.enregistrer(souscription.abandonner(a_l_instant, motif))
        except TransitionRefusee:
            # Déjà encaissée par ailleurs : un refus arrivé après coup ne défait
            # pas un encaissement confirmé. On le journalise et on n'y touche pas.
            self._journal.ajouter(
                horodatage=a_l_instant,
                acteur="systeme",
                action="paiement.refus_tardif",
                objet_type="souscription",
                objet_id=souscription.reference,
                apres={"etat": souscription.etat, "motif": motif},
            )


class ResultatEncaissement(BaseModel):
    """Ce que la notification a produit. Rendu même en cas d'échec."""

    model_config = ConfigDict(frozen=True)

    rapproche: bool
    paiement: str | None = None
    #: Ce que le paiement réglait — une souscription, ou un numéro de proforma.
    #: ⚠️ Ce champ s'appelait `souscription`, et il a été renommé avec celui du
    #: domaine : un nom qui ment coûte plus cher qu'une migration.
    reference_reglee: str | None = None
    statut: StatutPaiement | None = None
    strategie: StrategieRapprochement | None = None
    activee: bool = False
    compte: str | None = None
    message: str

    @computed_field
    @property
    def demande_une_intervention(self) -> bool:
        """Un encaissement non rapproché, ou payé sans avoir pu être activé.

        Sérialisé parce que c'est le champ que la supervision lit. « Traité » et
        « traité correctement » ne sont pas la même chose, et la différence doit
        être visible sans relire les messages.
        """
        if not self.rapproche:
            return True
        return self.statut == StatutPaiement.VALIDE and not self.activee


def traiter_notification(
    charge_utile: dict[str, Any],
    *,
    fournisseur: FournisseurPaiement,
    paiements: DepotPaiements,
    suites: Mapping[NaturePaiement, SuiteDuPaiement],
    journal: JournalAudit,
    a_l_instant: datetime,
) -> ResultatEncaissement:
    """Point d'entrée de la notification du prestataire. Atomique et rejouable.

    ⚠️ `suites` est une **table**, et non deux paramètres nommés. Un troisième
    objet payable n'ajoutera donc pas un argument à cette signature ni une
    branche à ce corps : il déposera une entrée de plus. C'est le motif
    d'inversion employé par l'ordonnanceur et par le registre des services.
    """
    evenement = fournisseur.lire_notification(charge_utile)
    if evenement is None:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.notification_illisible",
            objet_type="notification",
            apres={"cles": sorted(charge_utile)},
        )
        return ResultatEncaissement(
            rapproche=False,
            message=(
                "notification illisible ; enregistrée pour examen. On répond tout de "
                "même favorablement, faute de quoi le prestataire la renverrait en boucle."
            ),
        )

    attendu = fournisseur.identifiant_marchand()
    if (
        attendu
        and evenement.identifiant_marchand
        and evenement.identifiant_marchand != attendu
    ):
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.marchand_etranger",
            objet_type="notification",
            apres={"recu": evenement.identifiant_marchand, "attendu": attendu},
        )
        return ResultatEncaissement(
            rapproche=False,
            message=(
                "notification destinée à un autre marchand. Le prestataire ne signant "
                "pas ses appels, cette vérification est l'un des rares contrôles "
                "disponibles."
            ),
        )

    trouve = rapprocher(evenement, paiements.rapprochables(), a_l_instant)
    if trouve is None:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.non_affecte",
            objet_type="notification",
            apres={
                "identifiant_produit": evenement.identifiant_produit,
                "reference_externe": evenement.reference_externe,
                "telephone": evenement.telephone,
                "montant": str(evenement.montant) if evenement.montant else None,
                "reussi": evenement.reussi,
            },
        )
        return ResultatEncaissement(
            rapproche=False,
            message=(
                "aucun paiement en attente ne correspond. À traiter à la main : un "
                "encaissement a peut-être eu lieu, et le rattacher au hasard serait pire "
                "que de ne pas le rattacher."
            ),
        )

    paiement, strategie = trouve
    suite = suites.get(paiement.nature)
    if suite is None:
        # ⚠️ Une nature sans suite est une erreur de câblage, pas un fait métier :
        # l'argent est arrivé et personne ne sait quoi en faire. On journalise
        # sous l'action que la supervision cherche, et on ne lève pas — sans quoi
        # le prestataire renverrait la notification en boucle.
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.suite_absente",
            objet_type="paiement",
            objet_id=paiement.identifiant,
            apres={"nature": paiement.nature, "connues": sorted(suites)},
        )
        return ResultatEncaissement(
            rapproche=False,
            paiement=paiement.identifiant,
            reference_reglee=paiement.reference_reglee,
            message=(
                f"aucune suite n'est câblée pour un paiement de nature "
                f"{paiement.nature}. L'encaissement a eu lieu et n'a produit aucun "
                "effet : à reprendre à la main."
            ),
        )
    return appliquer_evenement(
        paiement,
        evenement,
        strategie=strategie,
        paiements=paiements,
        suite=suite,
        journal=journal,
        a_l_instant=a_l_instant,
    )


def appliquer_evenement(
    paiement: Paiement,
    evenement: EvenementPaiement,
    *,
    strategie: StrategieRapprochement,
    paiements: DepotPaiements,
    suite: SuiteDuPaiement,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> ResultatEncaissement:
    """Applique un évènement à un paiement déjà identifié.

    Employé par les **deux** chemins : la notification entrante, qui a dû
    rapprocher d'abord, et la réconciliation, qui interroge un paiement qu'elle
    connaît déjà. Écrire deux fois cette logique la ferait diverger — et l'une des
    deux versions finirait par activer là où l'autre rejette.
    """
    if not evenement.reussi:
        motif = evenement.motif or "refusé par le prestataire ou par l'abonné"
        paiements.enregistrer(paiement.rejeter(a_l_instant, motif))
        suite.refuser(paiement, motif, a_l_instant)
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.rejete",
            objet_type="paiement",
            objet_id=paiement.identifiant,
            apres={"motif": motif, "strategie": strategie},
        )
        return ResultatEncaissement(
            rapproche=True,
            paiement=paiement.identifiant,
            reference_reglee=paiement.reference_reglee,
            statut=StatutPaiement.REJETE,
            strategie=strategie,
            message=f"paiement refusé : {motif}",
        )

    deja_valide = paiement.statut == StatutPaiement.VALIDE
    try:
        valide = paiement.valider(
            a_l_instant,
            strategie=strategie,
            reference_externe=evenement.reference_externe,
            montant_constate=evenement.montant,
        )
    except MontantIncoherent as ecart:
        paiements.enregistrer(paiement.rejeter(a_l_instant, str(ecart)))
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.montant_incoherent",
            objet_type="paiement",
            objet_id=paiement.identifiant,
            apres={"attendu": str(paiement.montant), "recu": str(evenement.montant)},
        )
        return ResultatEncaissement(
            rapproche=True,
            paiement=paiement.identifiant,
            reference_reglee=paiement.reference_reglee,
            statut=StatutPaiement.REJETE,
            strategie=strategie,
            message=str(ecart),
        )
    except ValueError as refus:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.validation_refusee",
            objet_type="paiement",
            objet_id=paiement.identifiant,
            apres={"statut": paiement.statut, "motif": str(refus)},
        )
        return ResultatEncaissement(
            rapproche=True,
            paiement=paiement.identifiant,
            reference_reglee=paiement.reference_reglee,
            statut=paiement.statut,
            strategie=strategie,
            message=str(refus),
        )

    paiements.enregistrer(valide)

    if not deja_valide:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.valide",
            objet_type="paiement",
            objet_id=valide.identifiant,
            apres={
                "montant": str(valide.montant),
                "strategie": strategie,
                "nature": valide.nature,
                "reference_reglee": valide.reference_reglee,
            },
        )

    # ⚠️ **Le paiement est enregistré avant que la suite ne s'exécute**, et
    # l'ordre compte. Si la suite échoue, l'encaissement reste acquis et
    # l'objet réglé apparaît dans sa file de rattrapage. L'inverse ferait
    # rejouer un paiement réel parce qu'une ouverture d'accès a échoué.
    aboutissement = suite.encaisser(valide, a_l_instant)
    return ResultatEncaissement(
        rapproche=True,
        paiement=valide.identifiant,
        reference_reglee=valide.reference_reglee,
        statut=StatutPaiement.VALIDE,
        strategie=strategie,
        activee=aboutissement.abouti,
        compte=aboutissement.compte,
        message=aboutissement.message,
    )


class VerificationDIdentite(BaseModel):
    """Qui a vérifié que le payeur est bien l'entreprise, et comment (pas 83)."""

    model_config = ConfigDict(frozen=True)

    par: str = Field(min_length=1)
    #: Ce qui a été vu : « RCCM et CNI du gérant présentés au cabinet le 15/09 ».
    #: C'est la ligne que le cabinet relira le jour où l'entreprise contestera l'accès.
    comment: str = Field(min_length=20)


def activer_souscription(
    reference: str,
    *,
    verification: VerificationDIdentite,
    souscriptions: DepotSouscriptions,
    acces: ServiceOuvertureAcces,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> Souscription:
    """Ouvre l'accès d'une souscription payée, **après vérification d'identité**.

    C'est le seul chemin qui ouvre un accès à un dossier depuis le pas 83 (voir
    `_activer`). Le geste d'ouverture reste celui du traitement automatique
    d'avant : un chemin distinct finirait par créer les comptes autrement. Ce qui
    change, c'est qu'il exige une vérification nommée.
    """
    souscription = souscriptions.lire(reference)
    active, _, message = _activer(
        souscription,
        souscriptions=souscriptions,
        acces=acces,
        journal=journal,
        a_l_instant=a_l_instant,
        verification=verification,
    )
    if not active:
        raise RuntimeError(message)
    return souscriptions.lire(reference)


def _activer(
    souscription: Souscription,
    *,
    souscriptions: DepotSouscriptions,
    acces: ServiceOuvertureAcces,
    journal: JournalAudit,
    a_l_instant: datetime,
    verification: VerificationDIdentite | None,
) -> tuple[bool, str | None, str]:
    """Ouvre l'accès si la souscription en réclame un **et que l'identité est vérifiée**.

    Ne lève pas. Rend `(activée, compte, message)`. L'échec laisse la souscription à
    `PAYEE`, donc visible — voir l'en-tête.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ PAS 83 : UN PAIEMENT NE PROUVE PAS QU'ON EST L'ENTREPRISE

    L'engagement d'un devis est **public**, et le NIU de la souscription est déclaré
    par le visiteur. L'accès s'ouvrait dès l'encaissement. Essai, avant correction :
    un inconnu règle 12 500 FCFA en déclarant le NIU de SARL BATIMENT PLUS, reçoit
    le lien, définit son mot de passe, et lit **la fiche du dossier, ses pièces avec
    fournisseurs et montants, son échéancier**, et y **dépose un fichier**. Deux
    comptes adhérents coexistent alors sur le même dossier.

    Refuser seulement les NIU déjà au portefeuille ne suffirait pas : un NIU est
    imprimé sur chaque facture. Un concurrent souscrirait avec celui d'une entreprise
    qui n'est pas encore cliente, et serait habilité le jour où elle le deviendrait.

    La règle, désormais : **le traitement automatique n'ouvre jamais d'accès à un
    dossier**. Le paiement est acquis, la souscription reste payée et figure dans les
    encaissements à activer, et le cabinet ouvre l'accès après avoir vérifié
    l'identité (`activer_souscription`, sous `GERER_COMPTES`, avec la vérification
    nommée). Comment prouver en ligne qu'on est l'entreprise reste une question
    ouverte (Q24).
    ─────────────────────────────────────────────────────────────────────────
    """
    if souscription.activee_le is not None:
        return (
            True,
            souscription.compte,
            f"déjà activée le {souscription.activee_le} ; rejeu sans effet",
        )

    if not souscription.ouvre_un_acces:
        # Une formation payée n'ouvre aucun dossier. Lui en ouvrir un donnerait
        # accès à une comptabilité qui n'a rien à voir avec la prestation.
        return (
            False,
            None,
            "paiement encaissé ; cette prestation n'ouvre pas d'accès à un dossier",
        )

    if verification is None:
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="souscription.verification_requise",
            objet_type="souscription",
            objet_id=souscription.reference,
            apres={"niu": souscription.niu, "courriel": souscription.prospect.courriel},
        )
        return (
            False,
            None,
            (
                "paiement encaissé ; l'accès au dossier s'ouvrira après vérification de "
                "votre identité par le cabinet, qui vous contactera. L'argent n'est ni "
                "rendu ni perdu."
            ),
        )

    try:
        compte = acces.ouvrir(souscription, a_l_instant)
    except Exception as echec:  # noqa: BLE001 — voir l'en-tête : on ne lève jamais ici
        journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="souscription.activation_echouee",
            objet_type="souscription",
            objet_id=souscription.reference,
            apres={"erreur": f"{type(echec).__name__}: {echec}"},
        )
        return (
            False,
            None,
            (
                f"paiement encaissé, accès non ouvert : {echec}. La souscription reste "
                "payée et figure dans les encaissements à activer ; l'argent n'est ni "
                "rendu ni perdu."
            ),
        )

    activee = souscription.activer(
        a_l_instant,
        compte=compte,
        prend_effet_le=(a_l_instant + DELAI_PRISE_EFFET).date(),
    )
    souscriptions.enregistrer(activee)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur="systeme",
        action="souscription.activee",
        objet_type="souscription",
        objet_id=activee.reference,
        apres={
            "compte": compte,
            "niu": activee.niu,
            "verifiee_par": verification.par,
            "verification": verification.comment,
        },
    )
    return True, compte, "paiement encaissé et accès ouvert ; le lien est parti"
