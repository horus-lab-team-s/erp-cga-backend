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
from app.contextes.souscription.domaine.demande_de_contact import DemandeDeContact
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
    "SERVICES_OUVRANT_UN_LOCATAIRE",
    "ouvre_un_locataire",
    "BoiteOuDeposer",
    "EncaissementRefuse",
    "ResultatEncaissementDuParcours",
    "encaisser_l_acceptation",
]

#: Le nom métier, tel que le document de conception l'écrit. Pas un nom
#: technique : c'est lui qui figurera dans les journaux qu'un exploitant lit à
#: trois heures du matin.
NOM_EVENEMENT = "PaiementEncaissé"

#: Les services dont la vente ouvre un LOCATAIRE de la plateforme.
#:
#: ─────────────────────────────────────────────────────────────────────────────
#: ⚠️ VIDE, ET C'EST UNE DÉCISION DU CABINET DU 27 SEPTEMBRE.
#:
#: Toute proforma payée ouvrait un locataire — `tnt-{slug}` — quel que soit le
#: service vendu. Une entrepreneuse qui achetait la création de sa SARL recevait
#: donc **son propre espace de plateforme**, isolé du cabinet, pendant que son
#: entreprise entrait au portefeuille du cabinet. Les deux dans des locataires
#: différents : même en activant son compte, elle serait entrée dans un espace
#: vide.
#:
#: Une cliente qui achète une prestation est une **adhérente du cabinet**. Son
#: compte vit là où vit son dossier. Un locataire, lui, se vend à un AUTRE
#: centre de gestion qui achète la plateforme — cette offre n'est pas au
#: catalogue, et cet ensemble est donc vide aujourd'hui.
#:
#: ⚠️ `PLATEFORME` NOMME CETTE OFFRE, ET ELLE N'EST PAS AU CATALOGUE.
#:
#: Aucun prospect ne peut donc l'acheter aujourd'hui : le catalogue publie cinq
#: services, et celui-ci n'en fait pas partie. Le code est écrit ici pour que le
#: mécanisme d'ouverture garde ses cas — un mécanisme câblé que plus aucun cas
#: ne traverse pourrit en silence, et se découvre cassé le jour où l'on en a
#: besoin.
#:
#: ⚠️ Le jour où le cabinet vend la plateforme, le service s'ajoute au catalogue
#: **sous ce code exact**. C'est la leçon du 27 septembre : trois vocabulaires
#: pour une même prestation avaient rendu le tunnel commercial sans issue.
#: ─────────────────────────────────────────────────────────────────────────────
SERVICES_OUVRANT_UN_LOCATAIRE: frozenset[str] = frozenset({"PLATEFORME"})


def ouvre_un_locataire(service: str) -> bool:
    """Cette vente ouvre-t-elle un espace de plateforme ?

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **UNE SEULE RÉPONSE, POSÉE EN UN SEUL ENDROIT.**

    Le 13 octobre 2026, la règle ci-dessus était appliquée par l'événement et
    ignorée par tout le reste du tunnel. Pour une création d'entreprise, la
    route d'encaissement exigeait encore une « adresse de l'espace », la console
    promettait que « la confirmation ouvre l'espace du client », et la fiche
    affichait ensuite, pour toujours, un sablier « l'ouverture n'a pas encore
    démarré ». Le serveur avait raison de ne rien ouvrir ; c'est le parcours qui
    annonçait le contraire.

    Les routes, la notification du prestataire, la route d'état et la console
    lisent désormais cette fonction, ou ce qu'elle a rendu.
    ─────────────────────────────────────────────────────────────────────────
    """
    return service in SERVICES_OUVRANT_UN_LOCATAIRE


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
    # ⚠️ L'adresse n'est retenue que si la vente ouvre un espace. Pour une
    # prestation, il n'y a pas d'espace à adresser : en retenir une ferait
    # afficher « espace « … » » sur un dossier qui n'en aura jamais.
    if ouvre_un_locataire(proforma.service):
        if not slug:
            raise EncaissementRefuse(
                f"proforma {proforma.numero} : le service {proforma.service} ouvre "
                "un espace de plateforme, et aucune adresse n'a été choisie pour lui."
            )
        try:
            dossier = dossier.retenir_le_slug(slug)
        except TransitionDossierRefusee as refus:
            raise EncaissementRefuse(str(refus)) from refus
    else:
        slug, tenant = "", ""

    paye = dossier.encaisser(a_l_instant)
    evenement = deposer(
        boite,
        identifiant_evenement,
        NOM_EVENEMENT,
        dossier.reference,
        _charge(
            proforma,
            slug=slug,
            tenant=tenant,
            reference_externe=reference_externe,
            titulaire=dossier.demande,
        ),
        a_l_instant,
    )
    return ResultatEncaissementDuParcours(dossier=paye, evenement=evenement)


def _charge(
    proforma: Proforma,
    *,
    slug: str,
    tenant: str,
    reference_externe: str,
    titulaire: DemandeDeContact | None = None,
) -> dict[str, Any]:
    """Ce que l'événement transporte. Le strict nécessaire à l'ouverture.

    Voir l'en-tête : un événement qui transporte des données dont personne n'a
    besoin finit par en transporter qu'on ne voulait pas voir circuler. Les
    journaux, les files et les sauvegardes le recopient tous.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **L'ADRESSE DU TITULAIRE EST NÉCESSAIRE, ET ELLE MANQUAIT.**

    La septième étape de l'ouverture crée le compte administrateur du client et
    lui envoie son lien d'activation. Sans son adresse, elle ne peut faire ni
    l'un ni l'autre : le tenant s'ouvrait, et le client restait dehors.

    C'est le strict nécessaire, et rien de plus : **pas le téléphone**, qui ne
    sert pas à créer un compte, et surtout **pas le montant ni le détail de la
    proforma**. Un événement est recopié dans les journaux, les files et les
    sauvegardes ; ce qu'on y met y reste longtemps.

    ⚠️ Le nom et le prénom sont transportés parce qu'un courriel qui commence par
    « Bonjour jp.nkoa » se lit comme une machine. L'adresse seule suffirait à
    créer le compte ; elle ne suffit pas à écrire au client.
    ─────────────────────────────────────────────────────────────────────────────
    """
    charge: dict[str, Any] = {
        "tenant": tenant,
        "slug": slug,
        "proforma": proforma.numero,
        "version_proforma": proforma.version,
        "reference_externe": reference_externe,
        # ⚠️ LE SERVICE, PARCE QUE L'ENCAISSEMENT NE DÉCLENCHE PAS LA MÊME CHOSE
        # SELON CE QUI A ÉTÉ VENDU.
        #
        # Une adhésion réglée ouvre un espace, et c'est tout. Une CRÉATION
        # réglée doit en outre ouvrir un dossier de formalité : le client a payé
        # pour que sa société soit immatriculée, pas pour recevoir un accès.
        # Sans ce champ, l'abonné qui ouvre ce dossier devrait relire la
        # Souscription — que le graphe des contextes lui interdit, et à juste
        # titre : c'est le rôle de l'événement de porter ce qu'il faut savoir.
        "service": proforma.service,
        # ⚠️ DIT EXPLICITEMENT, ET NON DÉDUIT DE L'ABSENCE DE SLUG.
        #
        # L'abonné qui ouvre le locataire lève sur une charge incomplète, et il a
        # raison : un champ manquant est un défaut, pas une consigne. « Cette
        # vente n'ouvre pas de locataire » est une consigne, et elle s'écrit.
        "ouvre_un_locataire": ouvre_un_locataire(proforma.service),
    }
    # ⚠️ LES FAITS FONDATEURS, ET EUX SEULS.
    #
    # La qualification en recueille une douzaine ; cinq seulement servent à
    # ouvrir un dossier de formalité, et ce sont ceux-là qui voyagent. Les
    # tranches de chiffre d'affaires, le nombre de salariés ou les pièces déjà
    # réunies ont servi à CHIFFRER : ils appartiennent à la proforma, qui les
    # conserve, et n'ont rien à faire dans un événement recopié dans les
    # journaux, les files et les sauvegardes.
    #
    # ⚠️ Aucun n'est fabriqué : absent de la qualification, absent de l'événement.
    # L'abonné dira franchement ce qui lui manque plutôt que d'ouvrir un dossier
    # au nom inventé.
    fondateurs = {
        cle: proforma.faits[cle]
        for cle in (
            "denomination_souhaitee",
            "forme_juridique",
            "capital_social",
            "activite",
            "siege",
        )
        if cle in proforma.faits
    }
    if fondateurs:
        charge["faits_fondateurs"] = fondateurs
    if titulaire is not None:
        charge["courriel_titulaire"] = titulaire.courriel or ""
        charge["nom_titulaire"] = titulaire.nom
        # ⚠️ `prenom` n'existe pas sur une demande de contact : le nom y est
        # entier. On ne fabrique donc pas de prénom, et le gabarit se rabat sur
        # le nom — mieux vaut « Bonjour NKOA » qu'un prénom inventé.
        #
        # ⚠️ LE TÉLÉPHONE, AJOUTÉ LE 27 SEPTEMBRE, ET LA RAISON A CHANGÉ.
        #
        # Il était écarté ici avec ce motif : « il ne sert pas à créer un
        # compte ». C'était juste tant que l'encaissement n'ouvrait qu'un espace.
        # Depuis qu'il ouvre aussi un dossier de formalité, le fondateur de ce
        # dossier porte un téléphone — c'est par là que le greffe et le cabinet
        # le joignent pendant l'immatriculation. Le motif d'hier ne tient plus ;
        # la règle, elle, n'a pas bougé : rien qui ne serve.
        charge["telephone_titulaire"] = titulaire.telephone
    return charge


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

        ouvre = ouvre_un_locataire(proforma.service)
        if ouvre and not dossier.slug_retenu:
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
                slug=dossier.slug_retenu or "",
                tenant=f"tnt-{dossier.slug_retenu}" if dossier.slug_retenu else "",
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
                if ouvre
                else "règlement encaissé ; cette vente n'ouvre pas d'espace"
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
