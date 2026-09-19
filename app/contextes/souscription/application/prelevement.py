"""Appeler les mensualités, relancer les impayés, arrêter le service.

─────────────────────────────────────────────────────────────────────────────────
TROIS GESTES, ET ILS S'ENCHAÎNENT DANS LE TEMPS

**Appeler** — cinq jours avant la période, demander le prélèvement. L'abonné
reçoit un menu sur son téléphone et valide.

**Relancer** — J+1, J+7, J+14 après l'exigibilité. Le premier jalon n'est pas de
la pression : **l'adhérent ignore souvent que le prélèvement a échoué**, parce
que l'opérateur ne le lui dit pas. La relance est d'abord une information.

**Arrêter le service** — au-delà du délai de grâce. Et « le service » ne veut pas
dire « l'accès » : voir plus bas.

L'APPEL EST IDEMPOTENT PAR ÉCHÉANCE, ET DEUX GARDES CONCOURENT

La première est la machine à états : une échéance dont un prélèvement est en
cours n'est plus `A_APPELER`, donc n'est plus examinée. Elle couvre le cas
courant — l'ordonnanceur rejoue sa tâche dans la journée.

La seconde est la clé portée par le paiement, et elle couvre le cas qui reste :
**un prélèvement refusé n'est pas rappelé automatiquement**.

Ce second point est une décision, pas une conséquence. Rappeler chaque jour un
prélèvement refusé produirait un menu de paiement quotidien sur le téléphone de
l'adhérent — ce qu'il vit comme du harcèlement, et ce que chaque tentative peut
lui facturer. Le rattrapage passe donc par la relance, qui l'informe, et par un
règlement qu'il déclenche lui-même.

⚠️ La contrepartie : une échéance refusée reste due et ne sera **jamais** rappelée
par la tâche. C'est la relance qui la porte, et l'arrêt du service qui la
sanctionne.

SUSPENDRE LE SERVICE N'EST PAS FERMER LE DOSSIER

Passé la grâce, le cabinet cesse de traiter les pièces et de préparer les
déclarations. L'adhérent **conserve l'accès en lecture** à son dossier.

Ce n'est pas de la mansuétude commerciale. Les pièces déposées et les écritures
produites sont ses documents comptables, qu'il est légalement tenu de conserver
dix ans. Les lui retenir pour obtenir un règlement l'exposerait à un manquement
dont il n'est pas responsable, et exposerait le cabinet à devoir s'en expliquer.

⚠️ La distinction n'est pas encore **appliquée** : le contexte K ne connaît
aujourd'hui que la suspension d'un compte, qui coupe tout. Ce module produit donc
la décision et la journalise ; ce qu'il faudrait pour l'exécuter est une
habilitation restreinte à la lecture, et c'est écrit dans `RESTE_A_FAIRE`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field

from app.contextes.souscription.domaine.abonnements import (
    DELAI_GRACE,
    JALONS_RELANCE,
    EtatEcheance,
    echeancier,
)
from app.contextes.souscription.domaine.paiements import Paiement
from app.contextes.souscription.domaine.ports import (
    DepotPaiements,
    DepotSouscriptions,
    FournisseurPaiement,
)
from app.contextes.souscription.domaine.souscriptions import Souscription
from app.contextes.transverse.api import JournalAudit

__all__ = [
    "RESTE_A_FAIRE",
    "RappelEcheance",
    "RapportPrelevement",
    "ServiceASuspendre",
    "appeler_les_echeances",
    "relances_du_jour",
    "services_a_suspendre",
]

#: ⚠️ Ce que ce module décide et n'exécute pas encore — voir l'en-tête.
RESTE_A_FAIRE = (
    "La suspension du service sans perte de lecture réclame une habilitation "
    "restreinte au contexte K : aujourd'hui, suspendre un compte coupe tout. "
    "`services_a_suspendre` rend donc la décision, que le cabinet applique à la "
    "main, plutôt que d'exécuter une coupure trop large."
)


class RapportPrelevement(BaseModel):
    """Ce qu'un passage d'appel a produit."""

    model_config = ConfigDict(frozen=True)

    examinees: int
    appelees: int
    deja_appelees: int
    refusees: int

    #: Ce qui a été demandé aujourd'hui. C'est le chiffre que la direction lit :
    #: « 14 appels » ne dit rien, « 175 000 F appelés » se compare au
    #: prévisionnel.
    montant_appele: Decimal = Decimal(0)

    paiements: list[str] = []

    @computed_field
    @property
    def demande_une_intervention(self) -> bool:
        """Des échéances ont été refusées à l'initiation.

        Distinct d'un prélèvement qui échouera plus tard faute de solde : ici,
        le prestataire n'a même pas accepté la demande. C'est un problème de
        configuration ou de numéro, pas de trésorerie du client.
        """
        return self.refusees > 0


class RappelEcheance(BaseModel):
    """Une relance à émettre aujourd'hui."""

    model_config = ConfigDict(frozen=True)

    souscription: str
    echeance: str
    niu: str | None
    courriel: str
    telephone: str
    montant: Decimal
    jours_de_retard: int
    jalon: int

    @computed_field
    @property
    def derniere(self) -> bool:
        """Le dernier jalon avant l'arrêt du service.

        Sérialisé, parce que le message n'est pas le même : les deux premières
        relances informent, la dernière annonce une conséquence. Les envoyer sur
        le même gabarit ferait passer l'avertissement pour un rappel de plus.
        """
        return self.jalon == max(JALONS_RELANCE)


def appeler_les_echeances(
    *,
    souscriptions: DepotSouscriptions,
    paiements: DepotPaiements,
    fournisseur: FournisseurPaiement,
    journal: JournalAudit,
    a_l_instant: datetime,
    identifiant: str,
    cle_idempotence: str,
) -> RapportPrelevement:
    """Demande le prélèvement de toutes les mensualités appelables aujourd'hui.

    `identifiant` et `cle_idempotence` sont des **préfixes** : chaque échéance
    appelée reçoit les siens, suffixés de son numéro. Les tirer ici plutôt que
    dans la boucle rend le passage reproductible — rejouer la tâche avec les
    mêmes préfixes ne produit pas de nouvelles clés, et le prestataire déduplique.
    """
    aujourd_hui = a_l_instant.date()
    examinees = appelees = deja = refusees = 0
    engages: list[str] = []
    total = Decimal(0)

    for souscription in souscriptions.lister():
        dues = [
            echeance
            for echeance in echeancier(
                souscription,
                paiements.par_souscription(souscription.reference),
                jusqu_au=aujourd_hui,
            )
            if echeance.etat is EtatEcheance.A_APPELER
        ]
        for echeance in dues:
            examinees += 1
            # Garde d'idempotence : un paiement porte déjà cette clé.
            if any(
                p.echeance == echeance.cle
                for p in paiements.par_souscription(souscription.reference)
            ):
                deja += 1
                continue

            paiement = Paiement(
                identifiant=f"{identifiant}-{echeance.numero:03d}",
                reference_reglee=souscription.reference,
                cle_idempotence=f"{cle_idempotence}-{echeance.numero:03d}",
                montant=echeance.montant,
                telephone=souscription.prospect.telephone,
                echeance=echeance.cle,
                initie_le=a_l_instant,
            )
            # Voir `engagement.py` : on écrit avant d'appeler. Entre un
            # enregistrement de trop et un encaissement perdu, on choisit
            # l'enregistrement de trop.
            paiements.enregistrer(paiement)

            initiation = fournisseur.initier(
                montant=paiement.montant,
                telephone=paiement.telephone,
                cle_idempotence=paiement.cle_idempotence,
                libelle=f"{souscription.libelle} — échéance {echeance.numero}",
            )
            if not initiation.accepte:
                motif = initiation.message or "le prestataire a refusé l'initiation"
                paiements.enregistrer(paiement.rejeter(a_l_instant, motif))
                refusees += 1
                continue

            if initiation.reference_externe:
                paiement = paiement.avec_reference(initiation.reference_externe)
                paiements.enregistrer(paiement)

            appelees += 1
            total += echeance.montant
            engages.append(paiement.identifiant)
            journal.ajouter(
                horodatage=a_l_instant,
                acteur="systeme",
                action="abonnement.echeance_appelee",
                objet_type="souscription",
                objet_id=souscription.reference,
                apres={
                    "echeance": echeance.cle,
                    "numero": echeance.numero,
                    "montant": str(echeance.montant),
                },
            )

    return RapportPrelevement(
        examinees=examinees,
        appelees=appelees,
        deja_appelees=deja,
        refusees=refusees,
        montant_appele=total,
        paiements=engages,
    )


def relances_du_jour(
    souscriptions: Sequence[Souscription],
    paiements: DepotPaiements,
    a_la_date: date,
) -> list[RappelEcheance]:
    """Les impayés dont le retard tombe exactement sur un jalon.

    « Exactement » : sans cela, un impayé de vingt jours déclencherait les trois
    relances à chaque passage, et l'adhérent recevrait un message par jour
    jusqu'au règlement. Une relance quotidienne se filtre en trois jours et cesse
    d'être lue.
    """
    rappels: list[RappelEcheance] = []
    for souscription in souscriptions:
        for echeance in echeancier(
            souscription,
            paiements.par_souscription(souscription.reference),
            jusqu_au=a_la_date,
        ):
            if not echeance.a_relancer:
                continue
            retard = echeance.jours_de_retard(a_la_date)
            if retard not in JALONS_RELANCE:
                continue
            rappels.append(
                RappelEcheance(
                    souscription=souscription.reference,
                    echeance=echeance.cle,
                    niu=souscription.niu,
                    courriel=souscription.prospect.courriel,
                    telephone=souscription.prospect.telephone,
                    montant=echeance.montant,
                    jours_de_retard=retard,
                    jalon=retard,
                )
            )
    return rappels


class ServiceASuspendre(BaseModel):
    """Une souscription dont le service doit s'arrêter — sans perte de lecture."""

    model_config = ConfigDict(frozen=True)

    souscription: str
    niu: str | None
    compte: str | None
    courriel: str
    montant_du: Decimal
    echeances_en_defaut: list[str]
    depuis_le: date

    @computed_field
    @property
    def consigne(self) -> str:
        """Ce que le cabinet doit faire, et ce qu'il ne doit pas faire.

        Rendue dans la réponse plutôt que laissée à une procédure écrite ailleurs :
        c'est au moment d'agir qu'on a besoin de la règle, pas dans un classeur.
        """
        return (
            "Arrêter le traitement des pièces et la préparation des déclarations. "
            "⚠️ Ne pas suspendre le compte : l'adhérent conserve l'accès en lecture "
            "à ses documents comptables, qu'il est tenu de conserver dix ans."
        )


def services_a_suspendre(
    souscriptions: Sequence[Souscription],
    paiements: DepotPaiements,
    a_la_date: date,
) -> list[ServiceASuspendre]:
    """Les abonnements dont au moins une échéance a dépassé le délai de grâce.

    Rend une **décision**, pas une exécution — voir `RESTE_A_FAIRE`. Ce qui
    manque pour l'appliquer est une habilitation restreinte à la lecture ; en
    attendant, exécuter reviendrait à couper l'accès entier, ce que l'en-tête
    explique qu'il ne faut pas faire.
    """
    decisions: list[ServiceASuspendre] = []
    for souscription in souscriptions:
        en_defaut = [
            echeance
            for echeance in echeancier(
                souscription,
                paiements.par_souscription(souscription.reference),
                jusqu_au=a_la_date,
            )
            if echeance.etat is EtatEcheance.EN_DEFAUT
        ]
        if not en_defaut:
            continue
        decisions.append(
            ServiceASuspendre(
                souscription=souscription.reference,
                niu=souscription.niu,
                compte=souscription.compte,
                courriel=souscription.prospect.courriel,
                montant_du=sum((e.montant for e in en_defaut), Decimal(0)),
                echeances_en_defaut=[e.cle for e in en_defaut],
                depuis_le=min(e.exigible_le for e in en_defaut) + DELAI_GRACE,
            )
        )
    return decisions
