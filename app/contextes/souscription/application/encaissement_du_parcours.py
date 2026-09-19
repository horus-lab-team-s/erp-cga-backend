"""L'encaissement qui referme le parcours sur l'ouverture du tenant.

─────────────────────────────────────────────────────────────────────────────────
LA RÈGLE ABSOLUE DE CETTE ÉTAPE

> « Le paiement arrive par un rappel de l'opérateur, jamais par une confirmation
> du navigateur du client. Un client qui ferme son onglet après avoir payé doit
> voir son espace s'ouvrir quand même. »

Ce module ne connaît donc **aucune session**, **aucune requête du client**, et ne
lit rien de son navigateur. Il reçoit un fait constaté, et il en tire les
conséquences.

CE QU'IL FAIT, EN TROIS GESTES QUI VONT ENSEMBLE

1. le dossier commercial passe à `PAYÉE`, ce qui est terminal côté commerce ;
2. l'événement `PaiementEncaissé` est **déposé dans la boîte d'envoi**, dans la
   même transaction ;
3. le relais le publiera, et la saga d'ouverture s'en saisira.

⚠️ **Les trois premiers vont dans la même transaction, la publication non.** C'est
tout l'intérêt de la boîte d'envoi : le fait et l'intention de publier sont
atomiques, la publication est « au moins une fois ».

L'IDEMPOTENCE EST TENUE À DEUX ENDROITS, ET IL EN FAUT DEUX

Le prestataire rejoue ses rappels, parfois plusieurs jours après. La garde du
dossier commercial suffit à ne pas encaisser deux fois ; celle de la saga suffit
à ne pas ouvrir deux tenants. **Aucune des deux ne suffit seule** :

* sans la première, un rejeu déposerait un second événement, et la boîte
  grossirait de faits déjà traités ;
* sans la seconde, un événement publié deux fois par le relais — ce qui est son
  régime normal — ouvrirait deux tenants.

Le document de conception nomme ce piège : « Un même paiement encaissé deux fois
ouvre deux tenants. » Il est fermé des deux côtés.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.contextes.souscription.application.encaissement import Aboutissement
from app.contextes.souscription.domaine.dossier_commercial import (
    DossierCommercial,
    EtatDossier,
    TransitionDossierRefusee,
)
from app.contextes.souscription.domaine.proforma import Proforma
from app.orchestration.boite_d_envoi import EvenementSortant, deposer

__all__ = [
    "SuiteDeProforma",
    "NOM_EVENEMENT",
    "BoiteOuDeposer",
    "EncaissementRefuse",
    "ResultatEncaissementDuParcours",
    "encaisser_l_acceptation",
]

#: Le nom métier, tel que le document de conception l'écrit. Pas un nom
#: technique : c'est lui qui figurera dans les journaux qu'un exploitant lit à
#: trois heures du matin.
NOM_EVENEMENT = "PaiementEncaissé"


class EncaissementRefuse(ValueError):
    """L'encaissement ne peut pas être rattaché à cet engagement."""


class BoiteOuDeposer(Protocol):
    """Ce que ce module exige de la boîte d'envoi, et rien de plus."""

    def deposer(self, evenement: EvenementSortant) -> None: ...


class ResultatEncaissementDuParcours(BaseModel):
    """Le dossier après encaissement, et l'événement déposé.

    `None` pour l'événement quand l'encaissement était un rejeu : le dossier
    était déjà payé, rien n'a été déposé.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    dossier: DossierCommercial
    evenement: EvenementSortant | None = None

    @property
    def rejeu(self) -> bool:
        return self.evenement is None


def encaisser_l_acceptation(
    dossier: DossierCommercial,
    proforma: Proforma,
    *,
    boite: BoiteOuDeposer,
    slug: str,
    tenant: str,
    a_l_instant: datetime,
    identifiant_evenement: str,
    reference_externe: str = "",
) -> ResultatEncaissementDuParcours:
    """Encaisse, et dépose l'événement qui ouvrira le tenant.

    ─────────────────────────────────────────────────────────────────────────
    LA CLÉ DE L'ÉVÉNEMENT EST LA RÉFÉRENCE DU DOSSIER

    Pas l'identifiant du paiement, ni le numéro de la proforma. C'est cette clé
    que la saga d'ouverture emploie pour retrouver son exécution : deux rappels
    d'opérateur portant le même encaissement doivent tomber sur **la même**
    saga.

    Prendre le numéro de la proforma paraîtrait plus naturel et casserait le
    jour où une v2 est acceptée : la saga repartirait de zéro sur un tenant à
    moitié ouvert.

    ⚠️ LA CHARGE PORTE LE SLUG ET LE TENANT, PAS LE MONTANT

    Le provisionnement n'a que faire du montant, et un événement qui transporte
    des données dont personne n'a besoin finit par en transporter qu'on ne
    voulait pas voir circuler. La référence externe y est parce que le
    rapprochement comptable la cherchera.
    ─────────────────────────────────────────────────────────────────────────
    """
    if proforma.dossier != dossier.reference:
        raise EncaissementRefuse(
            f"proforma {proforma.numero} rattachée au dossier {proforma.dossier}, "
            f"encaissement présenté sur {dossier.reference}. Rapprocher un "
            "paiement du mauvais engagement ouvrirait le tenant d'un autre client."
        )
    if dossier.etat is EtatDossier.PAYEE:
        return ResultatEncaissementDuParcours(dossier=dossier)

    # ⚠️ **L'ADRESSE RETENUE FAIT FOI** (pas 68).
    #
    # La demande de règlement retient l'adresse du futur espace sur le dossier, et
    # c'est elle que le chemin notifié emploie. La confirmation manuelle, elle,
    # recevait une adresse dans sa requête **sans la confronter** : un client à qui
    # l'on avait annoncé « station-bonaberi » et qui réglait finalement en espèces
    # voyait son espace s'ouvrir à l'adresse tapée ce jour-là. Deux chemins, deux
    # espaces possibles pour un seul engagement.
    #
    # `retenir_le_slug` refuse une adresse différente de celle déjà retenue, et retient
    # celle-ci quand aucune ne l'était : le dossier dit ensuite laquelle a été ouverte.
    try:
        dossier = dossier.retenir_le_slug(slug)
    except TransitionDossierRefusee as refus:
        raise EncaissementRefuse(str(refus)) from refus

    paye = dossier.encaisser(a_l_instant)
    evenement = deposer(
        boite,
        identifiant_evenement,
        NOM_EVENEMENT,
        dossier.reference,
        _charge(proforma, slug=slug, tenant=tenant, reference_externe=reference_externe),
        a_l_instant,
    )
    return ResultatEncaissementDuParcours(dossier=paye, evenement=evenement)


def _charge(
    proforma: Proforma, *, slug: str, tenant: str, reference_externe: str
) -> dict[str, Any]:
    """Ce que l'événement transporte. Le strict nécessaire à l'ouverture.

    Voir l'en-tête : un événement qui transporte des données dont personne n'a
    besoin finit par en transporter qu'on ne voulait pas voir circuler. Les
    journaux, les files et les sauvegardes le recopient tous.
    """
    return {
        "tenant": tenant,
        "slug": slug,
        "proforma": proforma.numero,
        "version_proforma": proforma.version,
        "reference_externe": reference_externe,
    }


class SuiteDeProforma:
    """Ce qu'une proforma réglée déclenche : l'ouverture du tenant du client.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE CLASSE EXISTE

    Le parcours d'acquisition vendait sans encaisser : la route de confirmation
    faisait **saisir une référence externe à la main**, pendant que l'intégration
    du prestataire, éprouvée et commentée incident par incident, tournait à côté
    pour l'autre flux — celui des devis et souscriptions. Les deux n'avaient
    jamais été raccordés.

    Cette classe est le raccord. Elle est choisie d'après `paiement.nature`, par
    le même mécanisme qui choisit la suite d'une souscription : ni la notification
    entrante ni la réconciliation ne savent qu'elle existe.

    ⚠️ **ELLE NE LÈVE JAMAIS.** Une suite qui lèverait ferait remonter
    l'exception jusqu'à la réponse HTTP, donc un renvoi du prestataire, donc une
    boucle. Chaque échec est journalisé et rendu comme un aboutissement manqué :
    l'argent reste encaissé, et le dossier attend un humain.

    ⚠️ **ELLE NE DÉCIDE D'AUCUNE ADRESSE.** Le slug est lu sur le dossier, où il a
    été retenu **avant** le règlement, par un humain. En dériver un du nom du
    client au moment de la notification donnerait à la machine le seul choix que
    le projet lui refuse depuis le premier jour.
    ─────────────────────────────────────────────────────────────────────────────
    """

    def __init__(self, *, dossiers, proformas, boite, journal) -> None:
        """Tout est injecté, et rien n'est allé chercher.

        ⚠️ **La première version montait ses dépôts elle-même**, en important
        `routes_acquisition`. Le test d'architecture l'a refusée : la couche
        application ne dépend pas des adaptateurs. Il avait raison, et pas pour
        une raison de forme — ce module serait devenu inutilisable par tout
        appelant qui n'est pas une route, à commencer par la réconciliation.

        C'est le comptoir qui câble, parce qu'il est le seul endroit du contexte
        à connaître à la fois les souscriptions et le parcours.
        """
        self._dossiers = dossiers
        self._proformas = proformas
        self._boite = boite
        self._journal = journal

    def encaisser(self, paiement, a_l_instant: datetime):
        try:
            proforma = self._proformas.lire(paiement.reference_reglee)
            dossier = self._dossiers.lire(proforma.dossier)
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête : on ne lève pas
            return self._echec(
                paiement, f"{type(echec).__name__}: {echec}", a_l_instant
            )

        if not dossier.slug_retenu:
            return self._echec(
                paiement,
                "aucune adresse retenue pour ce dossier : le règlement a été "
                "notifié sans qu'un espace ait été préparé. L'encaissement est "
                "acquis ; l'ouverture attend un humain.",
                a_l_instant,
            )

        try:
            resultat = encaisser_l_acceptation(
                dossier,
                proforma,
                boite=self._boite,
                slug=dossier.slug_retenu,
                tenant=f"tnt-{dossier.slug_retenu}",
                a_l_instant=a_l_instant,
                # ⚠️ Dérivé du **dossier**, comme pour la confirmation manuelle :
                # les deux chemins doivent produire le même identifiant, sans quoi
                # un encaissement confirmé à la main puis notifié par l'opérateur
                # ouvrirait deux sagas pour un seul règlement.
                identifiant_evenement=f"enc-{dossier.reference}",
                reference_externe=paiement.reference_externe or "",
            )
        except Exception as echec:  # noqa: BLE001 — voir l'en-tête
            return self._echec(
                paiement, f"{type(echec).__name__}: {echec}", a_l_instant
            )

        self._dossiers.enregistrer(resultat.dossier)
        if resultat.rejeu:
            return Aboutissement(
                abouti=True,
                message=(
                    f"dossier {dossier.reference} déjà encaissé ; rejeu sans effet"
                ),
            )
        return Aboutissement(
            abouti=True,
            message=(
                f"règlement encaissé ; l'espace « {dossier.slug_retenu} » s'ouvre"
            ),
        )

    def refuser(self, paiement, motif: str, a_l_instant: datetime) -> None:
        """Le client n'a pas réglé. **Le dossier ne bouge pas.**

        ⚠️ Il reste `ACCEPTEE`, donc dans la file des impayées, que le balayage de
        relance remonte à un humain au bout du délai du référentiel. Le classer
        sans suite sur un refus de paiement ferait décider la machine à la place
        du centre : un client dont le code a expiré recommence, il n'a rien
        refusé.
        """
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.proforma_refusee",
            objet_type="proforma",
            objet_id=paiement.reference_reglee,
            apres={"motif": motif},
        )

    def _echec(self, paiement, motif: str, a_l_instant: datetime):
        self._journal.ajouter(
            horodatage=a_l_instant,
            acteur="systeme",
            action="paiement.ouverture_impossible",
            objet_type="proforma",
            objet_id=paiement.reference_reglee,
            apres={"motif": motif},
        )
        return Aboutissement(abouti=False, message=motif)
