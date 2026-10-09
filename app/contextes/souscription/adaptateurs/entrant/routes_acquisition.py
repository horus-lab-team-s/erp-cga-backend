"""API du parcours d'acquisition : du formulaire public au dossier qualifié.

─────────────────────────────────────────────────────────────────────────────────
UNE ROUTE PUBLIQUE, LE RESTE SOUS SESSION

`POST /acquisition/demandes` est ouverte. C'est le formulaire de la vitrine, et
exiger une session avant de laisser quelqu'un demander un rappel reviendrait à
demander à un visiteur de s'inscrire pour poser une question.

Tout le reste est la console du responsable, et suppose une session.

⚠️ **CE QUE LA ROUTE PUBLIQUE N'A PAS ENCORE**

Une limitation de débit par adresse et une épreuve de vérification. Un formulaire
ouvert sur internet est une porte à robots, et le document de conception le nomme
comme le piège de l'étape 1. L'intergiciel de limitation existe dans le socle ;
il n'est pas branché sur cette route, et l'oublier au déploiement exposerait la
base à un flot de demandes.

La détection de doublon protège du visiteur qui soumet deux fois. Elle ne protège
pas d'un robot qui change de numéro à chaque envoi.

CE QUE LES RÉPONSES NE DISENT PAS AU VISITEUR

La route publique rend la même chose qu'il y ait eu rattachement ou non. Lui
annoncer « vous avez déjà écrit » ne lui apporte rien et laisse croire à un refus.
Elle ne rend pas non plus la référence du dossier : c'est une donnée interne, et
la faire circuler dans une réponse publique ferait de la référence un identifiant
devinable.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
)
from sqlalchemy.exc import IntegrityError

from app.contextes.souscription.adaptateurs.sortant.annuaire_des_candidats import (
    monter_les_candidatures,
)
from app.contextes.souscription.adaptateurs.sortant.catalogue_modeles import (
    CatalogueDeModeles,
)
from app.contextes.souscription.adaptateurs.sortant.compositions_proforma import (
    RepertoireDesCompositions,
)
from app.contextes.souscription.adaptateurs.sortant.delais_de_veille import (
    charger_les_delais_de_veille,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DepotQualificationsMemoire,
    DossierIntrouvable,
    ProformaIntrouvable,
    RappelIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotDossiersSql,
    DepotProformasSql,
    DepotQualificationsSql,
    DepotRappelsSql,
)
from app.contextes.souscription.adaptateurs.sortant.document_proforma import (
    rendre_le_document,
)
from app.contextes.souscription.adaptateurs.sortant.grille_tarifaire import (
    charger_les_baremes,
    charger_les_regles_de_tarification,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
    proformas_memoire,
    vider_les_dossiers_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.motifs_de_classement import (
    MotifDeClassement,
    charger_les_motifs_de_classement,
)
from app.contextes.souscription.adaptateurs.sortant.plan_de_contact import (
    charger_le_plan_de_contact,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    QuestionnaireIntrouvable,
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.souscription.api import (
    BaremeIntrouvable,
    Canal,
    Candidature,
    Consentement,
    Debours,
    DemandeDeContact,
    DevisIntrouvable,
    DossierCommercial,
    EtatDevis,
    EtatDossier,
    NaturePaiement,
    Paiement,
    Proposition,
    Question,
    ReponseInvalide,
    SourceReponse,
    TarifIndisponible,
    TransitionDossierRefusee,
    affecter_le_dossier,
    bareme_pour,
    canal_a_employer,
    chiffrer,
    deposer_une_demande,
    nouvelle_cle_idempotence,
    ouvrir_une_qualification,
    reaffecter_le_dossier,
)
from app.contextes.souscription.application.encaissement_du_parcours import (
    EncaissementRefuse,
    encaisser_l_acceptation,
)
from app.contextes.souscription.domaine.composition_proforma import (
    CompositionIntrouvable,
)
from app.contextes.souscription.domaine.proforma import (
    LienDAcceptation,
    LienInvalide,
    Proforma,
    TarifArrete,
    accepter,
    emettre,
    empreinte,
    lien_pour,
    numero_suivant,
)
from app.contextes.souscription.domaine.rappels import RappelDejaFait
from app.contextes.tenants.api import (
    NOM_SAGA,
    NomsReservesIndisponibles,
    SlugInvalide,
    # ⚠️ Renommé à l'import : « substituees » seul ne dit pas de QUOI. Le nom
    # local porte le mot qui manque, et l'appel se lit sans remonter à l'import.
    manques_reels,
    ouverture_reellement_complete,
    valider_le_slug,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    boite_d_envoi,
    exiger,
    service_de_notification,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.infrastructure.depots_orchestration import DepotExecutionsSql
from app.orchestration.saga import EtatSaga
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import (
    CleInvalide,
    FichierAbsent,
    MagasinFichiersLocal,
)
from app.partage.recherche import (
    ReponseDeRecherche,
    RequeteTropCourte,
    ResultatDeRecherche,
    charger_les_reglages_de_recherche,
    pertinence,
    verifier_la_requete,
)
from app.partage.recherche import (
    # ⚠️ Renommée à l'import : la collecte a déjà une route `classer` (classer une
    # demande sans suite), et l'homonyme importée l'avait masquée. Même nom partout.
    classer as classer_les_resultats,
)
from app.partage.telephone import NumeroInvalide, normaliser_telephone

routeur = APIRouter(prefix="/acquisition", tags=["Acquisition"])

#: ⚠️ Un journal nommé, pour l'accusé d'acceptation : il ne lève jamais, et
#: son seul canal de signalement est celui-ci. Voir `_accuser_l_acceptation`.
_journal_acceptation = logging.getLogger(__name__)



@lru_cache
def _referentiel_d_acquisition() -> dict[str, Any]:
    """Les quatre paquets de configuration, lus une fois par processus.

    Mémoïsés parce qu'ils sont sur disque et ne changent qu'au déploiement ou par
    un geste d'exploitation. Les relire à chaque requête ferait quatre lectures de
    fichiers sur le chemin critique d'un formulaire public.

    ⚠️ La contrepartie est qu'un fichier modifié à chaud n'est pas repris. C'est
    assumé : le référentiel est versionné avec le code, et une reprise à chaud
    demanderait un rechargement explicite, qui n'existe pas encore.
    """
    # ⚠️ LE DOSSIER DU RÉFÉRENTIEL VIENT DE LA CONFIGURATION, JAMAIS DU DÉPÔT.
    #
    # Il était écrit en dur, `RACINE_DEPOT / "Docs" / "referentiel"`. Les tests
    # tournent depuis le dépôt, où ce chemin existe : ils passaient. L'image, elle,
    # range le référentiel dans `/application/donnees/referentiel` et le dit par
    # `CGA_DOSSIER_REFERENTIEL`. Le chemin en dur n'y menait à rien, et TOUTE la
    # surface d'acquisition répondait 500 — la demande de contact publique
    # comprise, c'est-à-dire la seule porte par laquelle un prospect demande à être
    # rappelé. Constaté sur la pile déployée.
    REFERENTIEL = configuration().dossier_referentiel
    return {
        "affectation": charger_la_grille_d_affectation(REFERENTIEL / "affectation"),
        "questionnaires": RepertoireDeQuestionnaires.depuis(
            REFERENTIEL / "qualification"
        ),
        "plan": charger_le_plan_de_contact(REFERENTIEL / "messagerie"),
        "modeles": CatalogueDeModeles.depuis(REFERENTIEL / "messagerie" / "modeles"),
        "baremes": charger_les_baremes(REFERENTIEL / "tarification"),
        "regles_tarifaires": charger_les_regles_de_tarification(
            REFERENTIEL / "tarification"
        ),
    }


def recharger_le_referentiel() -> None:
    """Repart d'une lecture neuve. Destiné aux tests et à l'exploitation."""
    _referentiel_d_acquisition.cache_clear()


#: ⚠️ **L'unique magasin, importé et non redéclaré.** En mémoire, ceci *est* la
#: persistance : deux instances seraient deux bases sans lien, et c'est arrivé.
#: Voir l'en-tête de `magasins_memoire`.
_dossiers_memoire = dossiers_memoire


def reinitialiser_dossiers() -> None:
    """Repart de magasins mémoire vierges. Destiné aux tests.

    ⚠️ Les qualifications sont vidées avec les dossiers, et il le faut : une
    qualification qui survivrait à son dossier ferait qu'un test reprendrait des
    réponses données par le test précédent, sur une référence recyclée.
    """
    vider_les_dossiers_memoire()
    _qualifications_memoire.cache_clear()


def _dossiers():
    """Le dépôt en vigueur — de la requête en SQL, du processus en mémoire.

    Reconstruit à chaque appel en SQL, parce qu'il porte la session de la requête.
    Le mémoïser conserverait une session déjà fermée, panne qui n'apparaîtrait
    qu'à la seconde requête.
    """
    session = session_de_travail()
    if session is None:
        return _dossiers_memoire()
    return DepotDossiersSql(session, courant())


def _reference(prefixe: str) -> str:
    return f"{prefixe}-{uuid.uuid4().hex[:16]}"


# ── Le formulaire public ─────────────────────────────────────────────────────


class DepotDeDemande(BaseModel):
    """Six champs, pas trente. Chaque champ de plus est un visiteur de moins."""

    nom: str = Field(min_length=2, max_length=120)
    telephone: str = Field(min_length=6)
    courriel: str | None = None
    #: ⚠️ **UN CODE DU CATALOGUE, ET RIEN D'AUTRE.** Voir le contrôle ci-dessous.
    service_souhaite: str = Field(min_length=1, max_length=40)
    message: str | None = None
    canal_prefere: Canal = Canal.APPEL
    #: La case à cocher, **non pré-cochée** côté vitrine. Le défaut est `False`
    #: ici aussi : un client qui envoie le formulaire sans le champ n'a pas
    #: consenti, et l'inverse ferait consentir par omission.
    consentement_whatsapp: bool = False
    version_du_texte: str = "consentement-whatsapp-v1"
    origine: str | None = None


def _exiger_un_service_du_catalogue(code: str) -> None:
    """Refuse un code inconnu du catalogue. Leve `ValueError`.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **LA PORTE D'ENTRÉE DE TOUT LE COMMERCE, ET ELLE ÉTAIT OUVERTE.**

    Le champ était une chaîne libre. Une demande `JURIDIQUE` entrait en 201,
    vérifié sur la pile : le site annonce une « assistance juridique » qui n'a
    aucun code, et rien ne l'empêchait d'arriver ici.

    Le dossier né d'un code inconnu est **sans issue** : pas de questionnaire,
    donc pas de qualification, pas de chiffrage, pas de proforma. Et il ne se
    voit pas, il ressemble à un dossier qui attend. La cliente, elle, recevait
    « Votre demande concernant JURIDIQUE nous est bien parvenue ».

    ⚠️ **POURQUOI ICI, ET NON EN VALIDATEUR DE CHAMP.**

    Écrit d'abord comme validateur du corps, il s'exécutait à l'analyse, **avant**
    le gestionnaire, donc hors de la reprise qui déballe les erreurs. Le visiteur
    recevait la trace brute de pydantic, liste de dictionnaires comprise :
    exactement ce que `test_messages_lisibles` interdit depuis le pas 51. Une
    garde posée pour protéger la porte cassait la promesse faite à cette porte.

    Appelé dans le gestionnaire, il rejoint le traitement du numéro de téléphone,
    et `message_lisible` en fait une phrase.

    ⚠️ Le refus porte sur ce qui **entre**, jamais sur ce qui est déjà en base :
    un code retiré du catalogue ne doit pas rendre illisibles les dossiers qui le
    portent.

    ⚠️ Le message nomme les **deux** : le code reçu et les codes admis. Un refus
    qui dit seulement « code invalide » oblige à ouvrir le code pour comprendre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.souscription.api import CATALOGUE

    admis = {service.code for service in CATALOGUE}
    if code not in admis:
        raise ValueError(
            f"service « {code} » inconnu du catalogue. "
            f"Les services proposés sont : {', '.join(sorted(admis))}."
        )


class AccuseDeDemande(BaseModel):
    """Ce que le visiteur reçoit. Volontairement pauvre — voir l'en-tête."""

    recue: bool = True
    #: Le canal sur lequel on le rappellera réellement, après repli.
    canal_de_rappel: Canal
    message: str


@routeur.post(
    "/demandes",
    status_code=201,
    summary="Déposer une demande de contact",
    responses={422: {"description": "Numéro inexploitable ou canal incohérent"}},
)
def deposer_une_demande_de_contact(corps: DepotDeDemande) -> AccuseDeDemande:
    """Publique. Le seul point d'entrée du parcours.

    Le canal de rappel annoncé est celui du **repli réel**, pas celui coché : si
    la messagerie n'est pas ouverte, le visiteur lit qu'on l'appellera, et c'est
    ce qui se produira. Lui promettre un canal qu'on n'exploite pas serait la
    manière la plus simple de le décevoir au premier contact.
    """
    referentiel = _referentiel_d_acquisition()
    instant = maintenant()
    try:
        # ⚠️ Avant tout le reste : un code inconnu ne doit pas créer de dossier.
        _exiger_un_service_du_catalogue(corps.service_souhaite)
        demande = DemandeDeContact(
            identifiant=_reference("dc"),
            deposee_le=instant,
            nom=corps.nom,
            telephone=corps.telephone,
            courriel=corps.courriel,
            service_souhaite=corps.service_souhaite,
            message=corps.message,
            canal_prefere=corps.canal_prefere,
            consentement=Consentement(
                accorde=corps.consentement_whatsapp,
                recueilli_le=instant,
                version_du_texte=corps.version_du_texte,
            ),
            origine=corps.origine,
        )
    except (NumeroInvalide, ValueError) as echec:
        raise HTTPException(status_code=422, detail=message_lisible(echec)) from echec

    deposer_une_demande(
        demande, _dossiers(), reference=_reference("dos"), boite=boite_d_envoi()
    )

    repli = canal_a_employer(
        demande.canal_prefere,
        referentiel["plan"],
        consentement_vaut=demande.consentement.vaut_maintenant,
        a_un_courriel=demande.courriel is not None,
        messagerie_prete=bool(referentiel["modeles"].envoyables()),
    )
    return AccuseDeDemande(
        canal_de_rappel=repli.canal,
        message="Votre demande est enregistrée. Un responsable vous recontacte.",
    )


@routeur.post(
    "/devis/{reference}/rappel",
    status_code=201,
    summary="Demander au cabinet d'arrêter le prix d'un devis",
    responses={
        404: {"description": "Devis inconnu"},
        409: {"description": "Ce devis se règle déjà en ligne, ou il est engagé"},
    },
)
def demander_le_rappel_d_un_devis(reference: str) -> AccuseDeDemande:
    """Publique. Le devis dont le prix reste à arrêter devient une demande de contact.

    ─────────────────────────────────────────────────────────────────────────────
    LA PROMESSE QUE L'ÉCRAN FAISAIT SANS LA TENIR

    Un devis dont un prix n'est pas encore arrêté par le gérant, ou dont une ligne
    se chiffre sur étude, ne se règle pas en ligne. L'application et la vitrine
    écrivaient alors « un responsable vous rappelle » ; **rien ne prévenait
    personne**. Le devis dormait, et le client attendait un appel qui ne venait pas.

    Cette route dépose la demande dans la file du responsable, exactement comme le
    formulaire de contact : affectation, échange, prix arrêté, proforma envoyée par
    courriel ou WhatsApp. C'est le début du parcours, pas un raccourci.

    ⚠️ TOUT VIENT DU DEVIS, RIEN DU CORPS DE LA REQUÊTE

    Le nom, le numéro et le courriel sont ceux que le prospect a saisis pour obtenir
    son devis. Les faire renvoyer par l'écran permettrait de déposer, sous la
    référence d'un devis, une demande au nom de quelqu'un d'autre.

    ⚠️ DEUX APPUIS NE FONT PAS DEUX DOSSIERS

    Le dépôt rattache toute demande du même numéro, dans la fenêtre anti-doublon, au
    dossier déjà ouvert (`doublon_parmi`). Le responsable voit que le client a
    insisté ; il n'a pas deux fiches à réconcilier.

    ⚠️ AUCUN CONSENTEMENT WHATSAPP N'EST DÉDUIT

    Le prospect a donné un numéro pour son devis, pas son accord pour être écrit sur
    WhatsApp. Le rappel se fait par appel, ou par courriel s'il en a laissé un.
    ─────────────────────────────────────────────────────────────────────────────
    """
    # Importé à l'appel, pour la même raison que plus bas : `routes_http` importe
    # déjà ce module.
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    try:
        devis = comptoir().devis.lire(reference)
    except DevisIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    if devis.etat is EtatDevis.ENGAGE:
        raise HTTPException(
            status_code=409,
            detail=f"le devis {reference} est déjà engagé : suivez votre souscription.",
        )
    if devis.complet and devis.prix_arretes:
        raise HTTPException(
            status_code=409,
            detail=(
                f"le devis {reference} a tous ses prix arrêtés : il se règle en ligne, "
                "sans attendre d'appel."
            ),
        )

    a_confirmer = [
        ligne for ligne in devis.lignes if ligne.montant is None or not ligne.prix_arrete
    ]
    prospect = devis.prospect
    instant = maintenant()
    demande = DemandeDeContact(
        identifiant=_reference("dc"),
        deposee_le=instant,
        nom=prospect.nom_complet
        + (f" ({prospect.denomination})" if prospect.denomination else ""),
        telephone=prospect.telephone,
        courriel=prospect.courriel,
        service_souhaite=(a_confirmer[0] if a_confirmer else devis.lignes[0]).service[:40],
        # ⚠️ Le message dit au responsable POURQUOI le client attend : la référence
        # du devis, et les prestations dont le prix est à arrêter.
        message=(
            f"Devis {reference} : prix à arrêter pour "
            + ", ".join(ligne.libelle for ligne in a_confirmer or devis.lignes)
            + "."
        ),
        canal_prefere=Canal.COURRIEL if prospect.courriel else Canal.APPEL,
        consentement=Consentement(
            accorde=False, recueilli_le=instant, version_du_texte="devis-sans-whatsapp"
        ),
        origine=f"devis {reference}",
    )
    deposer_une_demande(
        demande, _dossiers(), reference=_reference("dos"), boite=boite_d_envoi()
    )
    return AccuseDeDemande(
        canal_de_rappel=demande.canal_prefere,
        message=(
            "Votre demande est transmise au cabinet. Un responsable vous contacte au "
            f"{prospect.telephone} pour arrêter votre prix, puis vous envoie votre "
            "proposition."
        ),
    )


# ── La console du responsable ────────────────────────────────────────────────


class DossierResume(BaseModel):
    reference: str
    etat: EtatDossier
    nom: str
    telephone: str
    #: ⚠️ **CE QU'ELLE A DEMANDÉ**, jamais réécrit. Voir `service`.
    service_souhaite: str
    #: Ce sur quoi le cabinet travaille : le service retenu s'il y en a un, celui
    #: qu'elle a demandé sinon. **C'est ce champ que l'écran affiche en premier.**
    service: str
    #: `None` tant que personne n'a repris son choix. Non nul, l'écran doit
    #: montrer les deux : « elle a demandé X, nous traitons Y », sans quoi le
    #: collaborateur croit à une erreur de saisie.
    service_retenu: str | None = None
    motif_du_service_retenu: str | None = None
    responsable: str | None
    #: ⚠️ Le nom, lu à l'annuaire (pas 64). La console affiche une personne, pas
    #: « C-001 ». `None` quand le responsable n'est pas un compte du cabinet : une
    #: affectation simulée avec un effectif hypothétique garde son identifiant.
    responsable_nom: str | None = None
    depuis_le: datetime
    avancement: int
    demandes: int


def _noms_des_collaborateurs() -> dict[str, str]:
    """L'annuaire, lu une fois par requête : identifiant vers nom complet."""
    from app.contextes.transverse.api import atelier

    return {compte.identifiant: compte.nom_complet for compte in atelier().comptes.lister()}


def _resume(dossier, noms: dict[str, str] | None = None) -> DossierResume:
    noms = _noms_des_collaborateurs() if noms is None else noms
    return DossierResume(
        reference=dossier.reference,
        etat=dossier.etat,
        nom=dossier.demande.nom,
        telephone=dossier.demande.telephone,
        service_souhaite=dossier.demande.service_souhaite,
        service=dossier.service,
        service_retenu=dossier.service_retenu,
        motif_du_service_retenu=dossier.motif_du_service_retenu,
        responsable=dossier.responsable,
        responsable_nom=noms.get(dossier.responsable) if dossier.responsable else None,
        depuis_le=dossier.depuis_le,
        avancement=dossier.avancement,
        demandes=len(dossier.toutes_les_demandes),
    )


#: Les états dont un dossier ne revient pas. Ils sortent de la file « en cours »
#: — un dossier payé ou classé n'attend plus rien de personne.
ETATS_TERMINAUX = (EtatDossier.PAYEE, EtatDossier.SANS_SUITE)


@routeur.get("/dossiers", summary="Les dossiers commerciaux en cours")
def lister_les_dossiers(
    acces: AccesRequis,
    etat: EtatDossier | None = Query(default=None),
) -> list[DossierResume]:
    """Du plus ancien dans son état au plus récent : c'est celui qui attend depuis
    le plus longtemps qu'on traite d'abord.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ UN ÉTAT TERMINAL DEMANDÉ EXPLICITEMENT EST RENDU, ET IL NE L'ÉTAIT PAS.

    Sans argument, cette route rend la file **en cours** : `ouverts()` écarte les
    états terminaux, et c'est juste — un dossier payé n'attend plus rien de
    personne, et le laisser dans la file le ferait traiter deux fois.

    Mais `?etat=PAYEE` passait par le même chemin, et rendait donc toujours une
    liste **vide**. Un filtre qui ne peut jamais rien rendre ne dit pas « il n'y
    en a pas » : il ment, et l'on cherche le défaut ailleurs. J'y ai perdu du
    temps le 27 septembre, en cherchant pourquoi aucune création payée
    n'apparaissait nulle part.

    Demander un état terminal n'est pas parcourir la file : c'est chercher
    quelque chose de précis, et la route le rend. C'est ce qui permet au cabinet
    de retrouver une création payée — et de voir si son dossier de formalité
    s'est bien ouvert.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    noms = _noms_des_collaborateurs()
    depot = _dossiers()
    if etat in ETATS_TERMINAUX:
        dossiers = sorted(
            (d for d in depot.lister() if d.etat is etat),
            key=lambda d: d.depuis_le,
        )
    else:
        dossiers = depot.ouverts(etat=etat)
    return [_resume(d, noms) for d in dossiers]


# ── Ce qui dort, et ce qu'on en fait ─────────────────────────────────────────
#
# Trois routes qui manquaient toutes les trois, et pour la même raison : le
# parcours savait avancer, il ne savait pas se dégager. `classer_sans_suite` et
# `en_souffrance` existaient au domaine, écrites et testées, sans aucun appelant.
#
# ⚠️ La conséquence était mesurable. `charge_par_responsable` exclut les états
# terminaux en promettant qu'« un ancien collaborateur productif ne paraîtra pas
# surchargé pour toujours » : rien ne mettant jamais un dossier à `SANS_SUITE`,
# trois prospects muets pesaient encore trois dossiers huit mois plus tard, et
# l'affectation, qui choisit le moins chargé, punissait celui qui les avait reçus.


#: Le sous-dossier du référentiel où vivent les délais et le vocabulaire.
DOSSIER_ACQUISITION = "acquisition"


def _referentiel_de_veille():
    """Les délais, relus à chaque appel.

    ⚠️ Non mémoïsé, contrairement au questionnaire : la liste de ce qui dort est
    consultée quelques fois par jour, pas à chaque requête, et un délai que le
    centre vient de changer doit se voir tout de suite. C'est ce qu'on attend d'un
    réglage qu'on a mis au référentiel précisément pour pouvoir le toucher.
    """
    return charger_les_delais_de_veille(
        configuration().dossier_referentiel / DOSSIER_ACQUISITION
    )


def _motifs_de_classement() -> tuple[MotifDeClassement, ...]:
    return charger_les_motifs_de_classement(
        configuration().dossier_referentiel / DOSSIER_ACQUISITION
    )


class DossierEnSouffrance(BaseModel):
    """Un dossier immobile au-delà du délai de son état."""

    reference: str
    etat: EtatDossier
    nom: str
    responsable: str | None
    depuis_le: datetime
    immobile_depuis_heures: int
    delai_heures: int
    #: ⚠️ **Quand la veille l'a dit**, et `None` si elle ne l'a pas encore dit.
    #: Un dossier signalé il y a trois semaines et toujours là est une alerte que
    #: personne n'a traitée : c'est une information distincte de « il dort », et
    #: la masquer ferait relire la même liste tous les matins sans jamais voir
    #: laquelle des lignes a déjà été portée à la connaissance de quelqu'un.
    signale_le: datetime | None


@routeur.get(
    "/dossiers/en-souffrance",
    summary="Les dossiers immobiles au-delà du délai de leur état",
)
def lister_les_dossiers_en_souffrance(acces: AccesRequis) -> list[DossierEnSouffrance]:
    """Du plus en retard au moins en retard.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Cette route calcule, elle ne consulte pas un état stocké.** Le retard est
    une comparaison entre l'heure qu'il est et `depuis_le` : le figer dans une
    colonne le rendrait faux une seconde après l'écriture, et obligerait un
    travail de fond à le rafraîchir pour rien.

    ⚠️ **Elle ne dépend pas de la veille.** La veille dépose des alertes, cette
    route montre l'état réel. Un ordonnanceur arrêté ne rend pas cette liste
    fausse, il la rend seulement plus utile : c'est le seul endroit où l'on voit
    ce qui dort quand la mécanique de fond est en panne.

    ⚠️ **Le tri est fait ici et non par la base.** Le retard dépend des délais du
    référentiel, qui ne sont pas dans la base ; le faire trier par SQL supposerait
    d'y recopier les délais, donc de les avoir à deux endroits.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    delais = _referentiel_de_veille()
    instant = maintenant()
    depot = _dossiers()

    lignes: list[DossierEnSouffrance] = []
    for etat, delai in delais.items():
        for dossier in depot.ouverts(etat=etat):
            if not dossier.en_souffrance(instant, delais):
                continue
            lignes.append(
                DossierEnSouffrance(
                    reference=dossier.reference,
                    etat=dossier.etat,
                    nom=dossier.demande.nom,
                    responsable=dossier.responsable,
                    depuis_le=dossier.depuis_le,
                    immobile_depuis_heures=int(
                        (instant - dossier.depuis_le).total_seconds() // 3600
                    ),
                    delai_heures=int(delai.total_seconds() // 3600),
                    signale_le=dossier.signale_le,
                )
            )
    # Le retard **au-delà** du délai, et non l'immobilité brute : un dossier
    # affecté depuis 60 heures pour un délai de 48 est plus en retard qu'un
    # dossier en conversation depuis 100 heures pour un délai de 168, qui ne
    # l'est pas du tout. Trier sur l'immobilité mettrait le second en tête.
    lignes.sort(
        key=lambda ligne: ligne.immobile_depuis_heures - ligne.delai_heures,
        reverse=True,
    )
    return lignes


# ⚠️ **NE PAS DÉPLACER CETTE ROUTE APRÈS `/dossiers/{reference}`.**
#
# Elle y était, et elle était injoignable. FastAPI essaie les routes dans l'ordre
# de déclaration : `/dossiers/{reference}` capturait le segment littéral et rendait
# « aucun dossier commercial en-souffrance », un 404 assez crédible pour qu'on
# conclue que la veille ne trouve rien plutôt que qu'elle n'est jamais appelée.
#
# Une route masquée ne lève aucune erreur, ne manque à aucun test qui ne l'appelle
# pas, et se signale par une réponse plausible. C'est `test_acquisition_http.py`
# qui garde l'ordre, en appelant la route et en lisant ce qu'elle rend.


class ProformaDuDossier(BaseModel):
    """Une proforma, telle que la fiche d'un dossier la montre. **Jamais son lien.**"""

    numero: str
    version: int
    etat: str
    montant: str
    emise_le: str
    transmise_le: str | None


class FicheDuDossier(DossierCommercial):
    """Le dossier commercial, et ses proformas (pas 78).

    ⚠️ Cette route rendait `dict[str, Any]`. Son schéma OpenAPI ne décrivait donc aucun
    champ, et l'outil `outils/contrat_des_ecrans.py` ne pouvait pas vérifier ce que la
    console lit. Déclarer le modèle ne change pas la réponse au champ près : la capture
    avant et après le typage est identique.
    """

    proformas: list[ProformaDuDossier]
    #: Le nom du responsable, lu à l'annuaire, comme dans la liste (pas 64).
    #:
    #: ⚠️ La liste le rendait, la FICHE non : l'écran qui montre un dossier en détail
    #: n'avait que « C-007 » à afficher, quand la liste dont il vient disait
    #: « Patricia MOUKOURI ». Constaté en exerçant la route.
    responsable_nom: str | None = None


@routeur.get(
    "/dossiers/{reference}",
    summary="Un dossier commercial",
    responses={404: {"description": "Dossier inconnu"}},
)
def lire_le_dossier(acces: AccesRequis, reference: str) -> FicheDuDossier:
    exiger(acces, Permission.LIRE_PROSPECT)
    try:
        dossier = _dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    # ⚠️ Les proformas du dossier (pas 68) : sans elles, la fiche d'un dossier accepté
    # ne savait pas quel document faire régler. **Jamais le lien d'acceptation**, qui
    # n'est rendu qu'à l'émission.
    proformas = [
        {
            "numero": proforma.numero,
            "version": proforma.version,
            "etat": proforma.etat.value,
            "montant": str(proforma.tarif.montant),
            "emise_le": proforma.emise_le.isoformat(),
            "transmise_le": proforma.transmise_le.isoformat() if proforma.transmise_le else None,
        }
        for proforma in sorted(_proformas().par_dossier(reference), key=lambda p: p.version)
    ]
    noms = _noms_des_collaborateurs()
    return FicheDuDossier.model_validate(
        dossier.model_dump()
        | {
            "proformas": proformas,
            "responsable_nom": noms.get(dossier.responsable or ""),
        }
    )


class CandidatEntrant(BaseModel):
    responsable: str = Field(min_length=1)
    agence: str = ""
    competences: list[str] = Field(default_factory=list)
    competence_requise: str = ""
    dossiers_ouverts: int = 0
    charge_ponderee: Decimal = Decimal(0)
    disponible: bool = True


class DemandeDAffectation(BaseModel):
    """Les candidats, **facultatifs** : le serveur sait les monter lui-même.

    ─────────────────────────────────────────────────────────────────────────────
    Ils étaient obligatoires, et la route en était inutilisable. L'appelant devait
    fournir pour chaque collaborateur son nombre de dossiers ouverts et sa charge
    pondérée : une chose qu'aucune console ne peut savoir sans refaire côté client
    le travail du serveur.

    Absents, les candidatures sont montées depuis l'annuaire des collaborateurs et
    la charge lue sur les dossiers. C'est le cas normal.

    ⚠️ **Présents, ils sont employés tels quels**, et cela reste utile : simuler une
    affectation avec un effectif hypothétique — « si je recrute un chargé de
    formalités de plus, qui prendrait ce dossier ? » — est une question que la
    direction pose, et à laquelle l'annuaire réel ne peut pas répondre.
    ─────────────────────────────────────────────────────────────────────────────
    """

    candidats: list[CandidatEntrant] | None = None


class ResultatAffectationHttp(BaseModel):
    affecte: bool
    responsable: str | None = None
    motif: str | None = None
    penalite: Decimal | None = None
    empechements: list[str] = Field(default_factory=list)


@routeur.post(
    "/dossiers/{reference}/affectation",
    summary="Désigner un responsable",
    responses={
        404: {"description": "Dossier inconnu"},
        409: {"description": "Le dossier n'est pas dans un état qui permet d'affecter"},
    },
)
def affecter(
    acces: AccesRequis,
    reference: str,
    corps: DemandeDAffectation,
) -> ResultatAffectationHttp:
    """Désigne un responsable, ou **passe la main** si le dossier en a déjà un.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **UNE ABSENCE DE CANDIDAT CONVENABLE N'EST PAS UNE ERREUR.**

    La réponse est 200 avec `affecte: false` et les empêchements. Un 4xx ferait
    croire à une requête fautive, alors que c'est un fait métier : le dossier reste
    où il est, et la veille le remontera au terme du délai que le référentiel
    accorde à son état.

    ⚠️ Le délai n'est pas cité ici. Il a longtemps été annoncé comme « deux
    heures », valeur qu'aucun fichier ne portait. *Un commentaire chiffré vieillit,
    et personne ne le corrige parce que personne ne le lit* : `acquisition/
    veille.yaml` fait foi.

    DEUX GESTES SOUS UNE SEULE ROUTE, ET C'EST DÉLIBÉRÉ

    Un dossier `DÉPOSÉE` est **affecté** ; un dossier `AFFECTÉE` est **réaffecté**,
    ce qui écarte le titulaire actuel et consomme un des trois tours du dossier.
    L'appelant demande la même chose dans les deux cas — « désigne qui doit
    s'occuper de ça » — et lui faire choisir la route selon un état qu'il devrait
    lire d'abord serait lui demander de connaître le domaine à notre place.

    ⚠️ **C'est le domaine qui refuse, jamais cette route.** La limite de trois
    reprises et l'interdiction de réaffecter au même vivent dans `reaffecter`, et
    remontent ici en 409 avec leur message. Les recopier ici donnerait deux
    endroits où la règle est écrite, et un jour deux règles.

    La même bascule est faite par le travail de reprise, sans humain, au bout du
    délai du référentiel. Cette route est l'autre porte : celle du responsable de
    pôle qui décide de ne pas attendre.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.AFFECTER_DOSSIER)
    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    if corps.candidats is None:
        candidatures = _candidatures_du_cabinet(
            dossier.service, depot
        )
    else:
        candidatures = [
            Candidature(
                responsable=c.responsable,
                service=dossier.service,
                # ⚠️ **Vide, et non le message du prospect.** Cette ligne passait
                # `dossier.demande.message`, c'est-à-dire le texte libre du
                # formulaire, dans un champ que les règles comparent à des noms
                # d'agence. Un message contenant « je suis à Bonabéri » aurait fait
                # correspondre une agence par coïncidence de mots, et une
                # affectation se serait décidée là-dessus.
                #
                # Le formulaire public ne collecte aucune région : six champs,
                # délibérément. Le référentiel l'anticipe — « une région non
                # déclarée pénalise tout le monde également, l'effet est nul sur le
                # classement ». Le vide est franc ; la coïncidence ne l'est pas.
                region_demande="",
                agence_responsable=c.agence,
                competences=tuple(c.competences),
                competence_requise=c.competence_requise,
                dossiers_ouverts=c.dossiers_ouverts,
                charge_ponderee=c.charge_ponderee,
                disponible=c.disponible,
            )
            for c in corps.candidats
        ]

    # ⚠️ La bascule porte sur l'état, et non sur la présence d'un responsable.
    # Un dossier `EN_CONVERSATION` a un responsable et ne se réaffecte pas : il
    # faut d'abord le ramener en arrière, ce qui est un autre geste. Tester
    # `dossier.responsable is not None` aurait laissé passer ce cas-là, et
    # `reaffecter` aurait levé un refus de transition difficile à lire.
    passer_la_main = dossier.etat is EtatDossier.AFFECTEE
    conduire = reaffecter_le_dossier if passer_la_main else affecter_le_dossier
    try:
        resultat = conduire(
            dossier, candidatures, _referentiel_d_acquisition()["affectation"], maintenant()
        )
    except TransitionDossierRefusee as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec

    if resultat.affecte:
        depot.enregistrer(resultat.dossier)
    return ResultatAffectationHttp(
        affecte=resultat.affecte,
        responsable=resultat.choix.responsable if resultat.choix else None,
        motif=resultat.choix.motif if resultat.choix else None,
        penalite=resultat.choix.penalite if resultat.choix else None,
        empechements=list(resultat.empechements),
    )


# ── La qualification ─────────────────────────────────────────────────────────


class QuestionnairePublie(BaseModel):
    """Le questionnaire d'un service, dans l'ordre où la console le pose (pas 78 : typé)."""

    service: str
    version: str
    questions: list[Question]


@routeur.get(
    "/questionnaires/{service}",
    summary="Le questionnaire d'un service",
    responses={404: {"description": "Aucun questionnaire pour ce service"}},
)
def lire_le_questionnaire(acces: AccesRequis, service: str) -> QuestionnairePublie:
    """Ce que la console affiche au responsable pendant l'échange.

    Rendu depuis le référentiel, avec son ordre et sa version : c'est le
    questionnaire qui commande l'écran, jamais l'écran qui décide des questions.
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    try:
        questionnaire = _referentiel_d_acquisition()["questionnaires"].prendre(service)
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    return QuestionnairePublie(
        service=questionnaire.service,
        version=questionnaire.version,
        questions=list(questionnaire.ordonnees),
    )


class ReponseEntrante(BaseModel):
    code: str = Field(min_length=1)
    valeur: Any = None
    source: SourceReponse = SourceReponse.DECLAREE


class DemandeDeQualification(BaseModel):
    """Les réponses saisies pendant l'échange, et la note libre.

    ⚠️ Ce docstring disait « la qualification n'est pas encore persistée ». Elle
    l'est depuis que la route la reprend et la conserve ; la phrase partait pourtant
    dans le schéma OpenAPI. Corrigée au pas 65.
    """

    reponses: list[ReponseEntrante] = Field(default_factory=list)
    note: str = ""


class Avancement(BaseModel):
    repondues: int
    total: int


class EtatDeQualification(BaseModel):
    """Où en est la qualification d'un dossier (pas 78 : typé).

    ⚠️ Trois formes, un seul modèle. La lecture d'une qualification non commencée ne
    porte ni version, ni note, ni faits ; l'enregistrement ne dit pas « commencée ». Les
    champs absents d'une forme sont **non renseignés**, et les routes emploient
    `response_model_exclude_unset` : la réponse garde exactement ses champs d'avant,
    sans ajouter de `null` qu'un écran lirait comme une valeur.
    """

    dossier: str
    service: str
    version_questionnaire: str | None = None
    #: ⚠️ `bool` et non `bool | None` (pas 79) : une qualification est commencée ou non,
    #: jamais « inconnue ». Le défaut ne s'émet pas : les routes n'envoient que les champs
    #: renseignés, et l'enregistrement ne dit pas « commencée ».
    commencee: bool = False
    complete: bool
    manquantes: list[str]
    avancement: Avancement
    note: str | None = None
    faits: dict[str, str] | None = None


@routeur.post(
    "/dossiers/{reference}/qualification",
    summary="Éprouver une saisie de qualification",
    responses={
        404: {"description": "Dossier inconnu, ou service non qualifiable"},
        422: {"description": "Une réponse ne correspond pas à ce que la question attend"},
    },
    response_model_exclude_unset=True,
)
def qualifier(
    acces: AccesRequis,
    reference: str,
    corps: DemandeDeQualification,
) -> EtatDeQualification:
    """Valide chaque réponse au type de sa question, **la conserve**, et dit ce qui manque.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CETTE ROUTE JETAIT SON TRAVAIL.**

    Elle construisait la qualification, validait chaque réponse, rendait
    l'avancement et les faits, puis n'en gardait rien. Un responsable qui répondait
    à cinq questions sur douze et revenait le lendemain recommençait à zéro, sans
    qu'aucune erreur ne se produise.

    Elle **reprend** désormais la qualification en cours et lui ajoute les réponses
    du corps. C'est ce qui rend la qualification incrémentale, et c'est ce que le
    métier fait : on apprend au fil des échanges, rarement d'un coup.

    LE REFUS EST UN 422 QUI NOMME LA QUESTION

    Le message du domaine dit la question et ce qu'elle attendait. Un « données
    invalides » générique obligerait le responsable à deviner, avec le client au
    téléphone.

    ⚠️ **Un refus n'écrit rien.** Les réponses sont appliquées à un objet figé, et
    l'écriture n'a lieu qu'après la dernière : un lot dont la troisième réponse est
    fautive ne laisse pas les deux premières en base. Le responsable corrige et
    renvoie son lot entier, sans se demander ce qui est déjà passé.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        dossier = _dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        questionnaire = _referentiel_d_acquisition()["questionnaires"].prendre(
            dossier.service
        )
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    qualifications = _qualifications()
    # ⚠️ La reprise, et non une ouverture systématique : c'est tout le correctif.
    # Une qualification absente veut dire « pas encore commencée », qui est l'état
    # de tout dossier neuf.
    qualification = qualifications.trouver(reference) or ouvrir_une_qualification(
        reference, questionnaire
    )
    instant = maintenant()
    try:
        for reponse in corps.reponses:
            qualification = qualification.repondre(
                questionnaire,
                reponse.code,
                reponse.valeur,
                instant,
                source=reponse.source,
            )
        if corps.note:
            qualification = qualification.avec_note(corps.note)
    except ReponseInvalide as echec:
        raise HTTPException(status_code=422, detail=str(echec)) from echec

    # Après la dernière réponse seulement : voir l'en-tête, un lot fautif ne laisse
    # rien derrière lui.
    qualifications.enregistrer(qualification)
    _avancer_le_dossier(dossier, qualification, questionnaire, instant)

    repondues, total = qualification.avancement(questionnaire)
    return EtatDeQualification(
        dossier=reference,
        service=questionnaire.service,
        version_questionnaire=questionnaire.version,
        complete=qualification.complete(questionnaire),
        manquantes=list(qualification.manquantes(questionnaire)),
        avancement=Avancement(repondues=repondues, total=total),
        faits={code: str(valeur) for code, valeur in qualification.faits().items()},
    )


# ── L'état des canaux ────────────────────────────────────────────────────────


class EtatDUnCanal(BaseModel):
    canal: str
    actif: bool
    rang: int
    motif: str


class EtatDesCanaux(BaseModel):
    """Pas 90 : modèle déclaré. La route rendait un dictionnaire libre, que l'outil de
    contrat des écrans ne pouvait pas vérifier ; la réponse est inchangée."""

    actifs: list[str]
    messagerie_prete: bool
    detail: list[EtatDUnCanal]


@routeur.get("/canaux", summary="Les canaux de contact exploités")
def lister_les_canaux() -> EtatDesCanaux:
    """Publique, et utile à la vitrine : elle ne doit proposer au visiteur que
    des canaux que le centre exploite réellement. Lui en proposer un autre
    produirait une préférence impossible à honorer et une déception au premier
    rappel."""
    referentiel = _referentiel_d_acquisition()
    plan = referentiel["plan"]
    return EtatDesCanaux(
        actifs=[c.value for c in plan.actifs()],
        messagerie_prete=bool(referentiel["modeles"].envoyables()),
        detail=[
            EtatDUnCanal(canal=e.canal.value, actif=e.actif, rang=e.rang, motif=e.motif)
            for e in plan.ordonnes
        ],
    )


# ── Le chiffrage ─────────────────────────────────────────────────────────────


class DeboursEntrant(BaseModel):
    code: str = Field(min_length=1)
    libelle: str = Field(min_length=1)
    montant: Decimal = Field(ge=0)
    fondement: str = ""


class DemandeDeChiffrage(BaseModel):
    """Le score de charge et les débours. **Plus les faits.** (pas 65)

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LES FAITS VENAIENT DE LA REQUÊTE

    Ce modèle portait `faits`, avec ce motif : « la qualification n'est pas encore
    persistée ; le jour où sa table existe, la route les lira et ce corps
    disparaîtra ». Ce jour était venu, et personne ne l'avait fait. Le prix se
    calculait sur ce que la requête voulait bien envoyer : la recette elle-même
    qualifiait huit réponses, dont un capital d'un million et un chiffre d'affaires
    de 10 à 50 millions, puis chiffrait avec les deux premières seulement,
    `{"forme_juridique": "SARL", "associes": 2}`, et passait. Le capital et le chiffre
    d'affaires, qui font le prix, n'entraient pas dans le calcul.

    Les faits se lisent désormais à la qualification **enregistrée et complète**.
    Un corps qui les envoie est refusé.

    ⚠️ CE QUI RESTE DÉCLARÉ, ET POURQUOI

    `score_charge` : la matrice de charge existe au portefeuille, pour une
    entreprise suivie, et n'est pas branchée sur les faits d'un prospect. Le score
    reste donc déclaré, et il est **conservé avec les faits de la proforma** : un
    score minoré pour baisser le prix se lit sur le document émis. Question ouverte
    Q21.

    `debours` : des frais avancés pour le compte du client, que seul le responsable
    connaît.
    ─────────────────────────────────────────────────────────────────────────────
    """

    model_config = ConfigDict(extra="forbid")

    score_charge: int = Field(default=0, ge=0)
    #: Les frais avancés pour le compte du client. ⚠️ Ni ajustés par une règle,
    #: ni touchés par l'amplitude : négocier ne porte pas sur l'argent d'un tiers.
    debours: list[DeboursEntrant] = Field(default_factory=list)


class PropositionChiffree(Proposition):
    """La proposition, et ses deux totaux (pas 78 : typée).

    Les totaux sont des propriétés du domaine ; ils deviennent ici des champs calculés,
    pour que le schéma les décrive et que la réponse les porte, comme avant.
    """

    @computed_field
    @property
    def total_des_debours(self) -> Decimal:
        return super().total_des_debours

    @computed_field
    @property
    def total_de_reference(self) -> Decimal:
        return super().total_de_reference


@routeur.post(
    "/dossiers/{reference}/chiffrage",
    summary="Chiffrer une prestation",
    responses={
        404: {"description": "Dossier inconnu, ou service sans barème"},
        422: {"description": "Un fait de la qualification est mal typé"},
    },
)
def chiffrer_la_prestation(
    acces: AccesRequis, reference: str, corps: DemandeDeChiffrage
) -> PropositionChiffree:
    """Rend un intervalle, jamais un prix.

    ⚠️ **Une proposition n'engage personne** et ne publie aucun événement. Elle
    devient opposable à la validation, qui est un geste habilité et distinct :
    celui qui chiffre et celui qui engage peuvent être la même personne dans un
    petit centre, mais le système sait que ce sont deux rôles.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        dossier = _dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    proposition, _ = _proposition_du_dossier(dossier, corps.score_charge, corps.debours)
    # ⚠️ Le dossier avance **après** la preuve que la qualification est complète, et
    # jamais sur un dossier qui n'en est pas là : la proposition ci-dessus aurait levé.
    #
    # ⚠️ `EN_CONVERSATION` compte aussi, et **seulement parce que la proposition
    # ci-dessus a réussi**. Pour un service dont le gérant a arrêté le prix, il n'y
    # a pas d'étape « qualifiée » : rien n'a été qualifié. Un service sur étude
    # dans cet état n'arrive jamais ici — `_proposition_du_dossier` aurait déjà
    # rendu 409 en nommant les réponses manquantes.
    if dossier.etat in (EtatDossier.QUALIFIEE, EtatDossier.EN_CONVERSATION):
        try:
            _dossiers().enregistrer(dossier.chiffrer(maintenant()))
        except TransitionDossierRefusee as echec:
            raise HTTPException(status_code=409, detail=str(echec)) from echec
    return PropositionChiffree.model_validate(proposition.model_dump())


#: Les états où un dossier a une qualification complète et peut être chiffré.
ETATS_CHIFFRABLES = frozenset({EtatDossier.QUALIFIEE, EtatDossier.CHIFFREE})

#: Les états depuis lesquels un service **à prix arrêté** se chiffre.
#:
#: ─────────────────────────────────────────────────────────────────────────────
#: ⚠️ `EN_CONVERSATION` EN PLUS, ET C'EST LA CLÉ DU CORRECTIF.
#:
#: `QUALIFIEE` ne s'atteint que par la qualification, et la qualification exige un
#: questionnaire. Un service qui n'en a pas ne pouvait donc **jamais** devenir
#: chiffrable : le dossier montait jusqu'à `EN_CONVERSATION` et s'y arrêtait pour
#: toujours.
#:
#: Le faire passer par `QUALIFIEE` aurait été un mensonge : rien n'a été qualifié.
#: Pour un prix que le gérant a arrêté, « chiffrable » veut dire « l'échange a
#: commencé », et c'est exactement ce que dit `EN_CONVERSATION`.
#:
#: ⚠️ On n'y met PAS `AFFECTEE` : un responsable désigné n'a pas encore parlé au
#: client, et émettre une proposition avant le premier échange, c'est envoyer une
#: facture à quelqu'un qui a posé une question.
#: ─────────────────────────────────────────────────────────────────────────────
ETATS_CHIFFRABLES_A_PRIX_FERME = ETATS_CHIFFRABLES | {EtatDossier.EN_CONVERSATION}


def _prix_ferme_du_service(service: str, jour: date) -> Proposition | None:
    """La proposition d'un service dont le gérant a arrêté le prix, ou `None`.

    ⚠️ **`None` DÈS QU'IL Y A LE MOINDRE DOUTE**, et l'appelant retombe alors sur
    la qualification. Un service inconnu du catalogue, un tarif qui ne couvre pas
    la date, un tarif encore `A_VALIDER` : dans les trois cas, ce n'est pas un
    prix du cabinet, et il n'a rien à faire sur une facture.

    ⚠️ `version_bareme` porte l'identifiant de la décision, et non un numéro de
    barème : six mois plus tard, on doit pouvoir dire QUI a fixé ce prix et
    QUAND. C'est l'exigence que `Proposition` pose sur ce champ.
    """
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    catalogue = comptoir().services.charger(jour)
    trouve = next((s for s in catalogue if s.code == service), None)
    if trouve is None:
        return None
    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ UN SERVICE DÉCLINÉ EN FORMULES N'A PAS DE PRIX FERME, ET LE DEMANDER
    # LEVAIT UNE EXCEPTION NON REPRISE.
    #
    # `tarif_au` est documenté « le tarif d'un service **à barème unique** » et
    # refuse par `ValueError` si le service porte des formules. La reprise
    # ci-dessous n'attrapait que `TarifIndisponible` : le chiffrage d'une demande
    # d'ADHÉSION, dont le tarif se décline en trois tranches de chiffre
    # d'affaires, rendait donc **500** au responsable.
    #
    # Mesuré le 6 octobre 2026 en jouant les six services du catalogue bout en
    # bout. Le défaut ne se voyait sur aucun écran : l'adhésion est le service le
    # plus vendu du cabinet, et c'était le seul à décliner ses tarifs.
    #
    # ⚠️ `None` et non une reprise de l'exception, parce que c'est le sens de
    # cette fonction : au moindre doute, elle laisse l'appelant retomber sur la
    # qualification. Et ici il n'y a aucun doute — un prix qui dépend du chiffre
    # d'affaires n'est pas un prix ferme, c'est précisément ce que le
    # questionnaire sert à établir.
    # ─────────────────────────────────────────────────────────────────────────
    if trouve.formules:
        return None
    try:
        tarif = trouve.tarif_au(jour)
    except TarifIndisponible:
        return None
    if not tarif.fixe or tarif.montant is None:
        return None
    montant = Decimal(tarif.montant)
    return Proposition(
        service=service,
        version_bareme=f"tarif-fixe-{jour.isoformat()}",
        a_la_date=jour,
        base=montant,
        plancher=montant,
        reference=montant,
        plafond=montant,
    )


def _proposition_du_dossier(dossier, score_charge: int, debours: list[DeboursEntrant]):
    """La proposition tarifaire, calculée sur ce que **le dossier** sait. (pas 65)

    ─────────────────────────────────────────────────────────────────────────────
    UNE SEULE FONCTION, POUR LE CHIFFRAGE ET POUR L'ÉMISSION

    L'émission de la proforma recevait le plancher, la référence et le plafond du
    barème **dans la requête**. Or le motif n'est obligatoire qu'en dehors de cet
    intervalle : déclarer un plancher plus bas faisait passer un rabais sans motif.
    Les bornes sont désormais recalculées ici, au moment d'émettre, sur la même
    qualification que le chiffrage.

    ⚠️ Refusé en `409` tant que le dossier n'est pas qualifié, ou que sa qualification
    enregistrée est incomplète : un prix sur des faits absents ne se défend pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    referentiel = _referentiel_d_acquisition()
    jour = maintenant().date()

    # ─────────────────────────────────────────────────────────────────────────
    # ⚠️ UN PRIX ARRÊTÉ PAR LE GÉRANT NE SE QUALIFIE PAS : IL SE FACTURE.
    #
    # La qualification existe pour **calculer** un prix que personne ne connaît
    # d'avance. Elle était pourtant exigée de tous les services, y compris de
    # ceux dont le gérant a fixé le montant lui-même.
    #
    # Constaté le 29 septembre, sur la pile, pas à pas :
    #
    #     1. une visiteuse demande une FORMATION depuis le site  → 201
    #     2. le dossier entre dans la file du cabinet            → DEPOSEE
    #     3. le gérant arrête le prix à 75 000 F, motif à l'appui → 201
    #     4. le chargé de clientèle émet la proforma             → 404
    #
    #     « aucun questionnaire pour le service FORMATION »
    #
    # Le cabinet pouvait donc décider un prix et **ne pas pouvoir le facturer**.
    # Trois services du catalogue mouraient ainsi dans le tunnel, après avoir été
    # proposés sur le site public et avoir mobilisé un responsable.
    #
    # ⚠️ **ON NE FABRIQUE AUCUN PRIX.** Le montant vient de la décision du gérant,
    # datée et motivée, telle que `appliquer_les_fixations` la rejoue sur le
    # catalogue. Un tarif encore `A_VALIDER` — ceux de la maquette de la vitrine,
    # que personne au cabinet n'a arrêtés — ne passe PAS par ici : il retombe sur
    # le chemin de la qualification, et c'est ce qui empêche d'encaisser un prix
    # que personne n'a décidé.
    #
    # ⚠️ L'intervalle est réduit à un point : plancher = référence = plafond. Le
    # responsable garde le droit de s'en écarter, et le motif reste exigé comme
    # ailleurs — c'est `TarifArrete` qui l'impose, et cette fonction ne décide de
    # rien de plus.
    # ─────────────────────────────────────────────────────────────────────────
    ferme = _prix_ferme_du_service(dossier.service, jour)
    if ferme is not None:
        if dossier.etat not in ETATS_CHIFFRABLES_A_PRIX_FERME:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"le dossier est {dossier.etat.value} : un prix arrêté par le "
                    "gérant se facture une fois l'échange engagé avec le client."
                ),
            )
        return ferme, {"prix_arrete_par_le_gerant": str(ferme.reference)}

    # ⚠️ TRADUIT EN 404, COMME LE BARÈME QUINZE LIGNES PLUS BAS.
    #
    # Sans cette reprise, l'exception s'échappait et le client recevait
    # « Internal Server Error ». Constaté le 27 septembre en jouant le parcours
    # d'une création souscrite : le formulaire du site envoie `creation`, le
    # référentiel connaissait `creation-sarl`, et le chiffrage plantait au lieu
    # de
    # nommer le service introuvable.
    #
    # Le message levé est précis — il nomme le service demandé ET les services
    # qualifiables. C'est exactement ce dont a besoin celui qui doit corriger ;
    # le 500 le jetait.
    #
    # ⚠️ Les deux appelants en bénéficient : le chiffrage et l'émission de la
    # proforma. Les routes du questionnaire et de la qualification faisaient
    # déjà cette reprise, ce qui rendait l'écart d'autant plus difficile à voir.
    try:
        questionnaire = referentiel["questionnaires"].prendre(
            dossier.service
        )
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    qualification = _qualifications().trouver(dossier.reference)
    if qualification is None or not qualification.complete(questionnaire):
        manquantes = (
            list(qualification.manquantes(questionnaire))
            if qualification
            else [q.code for q in questionnaire.questions]
        )
        raise HTTPException(
            status_code=409,
            detail=f"qualification incomplète : il manque {', '.join(manquantes)}.",
        )
    # ⚠️ Après la qualification, et non avant : un dossier en conversation dont il
    # manque trois réponses doit s'entendre dire lesquelles, pas seulement son état.
    if dossier.etat not in ETATS_CHIFFRABLES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"le dossier est {dossier.etat.value} : il se chiffre une fois qualifié, "
                "sur les réponses enregistrées."
            ),
        )
    try:
        bareme = bareme_pour(referentiel["baremes"], dossier.service, jour)
    except BaremeIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    faits = dict(qualification.faits())
    proposition = chiffrer(
        faits,
        bareme=bareme,
        regles=referentiel["regles_tarifaires"],
        a_la_date=jour,
        score_charge=score_charge,
        debours=[
            Debours(code=d.code, libelle=d.libelle, montant=d.montant, fondement=d.fondement)
            for d in debours
        ],
    )
    # Ce que la proforma conservera : les faits de la qualification, et le score
    # déclaré à côté d'eux, pour qu'un score minoré se lise sur le document.
    faits_conserves = {code: str(valeur) for code, valeur in faits.items()}
    faits_conserves["score_charge"] = str(score_charge)
    return proposition, faits_conserves


# ── Le carnet des rappels ────────────────────────────────────────────────────


class RappelVu(BaseModel):
    """Un rappel tel que le responsable le lit. Sans contenu de conversation."""

    identifiant: str
    dossier: str
    motif: str
    cree_le: datetime
    fait_le: datetime | None = None
    fait_par: str | None = None


class Cloture(BaseModel):
    """⚠️ **Ne porte rien, et refuse tout.** (pas 63)

    Ce modèle portait `par`, le nom de qui avait appelé, écrit par l'appelant. Le
    docstring disait « se nommer est obligatoire : un carnet où l'on peut clore sans
    nom ne dit plus qui a parlé au client ». Le carnet où l'on écrit le nom d'un
    autre le dit encore moins. Qui clôt est le compte de la session, et un corps qui
    prétend le dire est refusé.
    """

    model_config = ConfigDict(extra="forbid")


@routeur.get(
    "/rappels",
    summary="Les rappels à passer, du plus ancien au plus récent",
    responses={403: {"description": "Habilitation insuffisante"}},
)
def rappels_en_attente(acces: AccesRequis) -> list[RappelVu]:
    """Ce que la machine a confié à un humain, et pourquoi.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **SANS CET ÉCRAN, LE REPLI PAR APPEL NE SERAIT PAS UN REPLI.**

    Il serait un silence : la relance serait comptée comme remise, et personne
    n'appellerait. Le plancher du plan de contact deviendrait un trou.

    C'est aussi pourquoi chaque rappel porte son **motif en clair**. Une liste de
    références de dossier obligerait le responsable à rouvrir chaque dossier pour
    comprendre ce qu'on attend de lui ; il cesserait de la lire, et le trou
    reviendrait par un autre chemin.

    Du plus ancien au plus récent : c'est le client qui attend depuis le plus
    longtemps qu'on rappelle en premier.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    return [
        RappelVu(**rappel.model_dump()) for rappel in _rappels().en_attente()
    ]


class ContactPris(BaseModel):
    """Ce que le responsable déclare après avoir joint le client."""

    #: Libre et court. Il n'est pas conservé au fil — il n'y en a pas encore —
    #: mais il oblige à écrire ce qui a été dit, et l'audit le porte.
    commentaire: str = Field(default="", max_length=300)


class DossierApresContact(BaseModel):
    reference: str
    etat: str
    premier_echange_le: datetime | None


class ServiceARetenir(BaseModel):
    """Le service que le cabinet retient, et pourquoi."""

    #: ⚠️ Un code du catalogue, contrôlé comme au dépôt.
    service: str = Field(min_length=1, max_length=40)
    #: ⚠️ **Obligatoire.** C'est la seule trace de la raison, et elle se relit au
    #: litige comme à la revue. Le domaine le refuse vide.
    motif: str = Field(min_length=3, max_length=500)


class DossierApresReprise(BaseModel):
    reference: str
    etat: str
    service_souhaite: str
    service: str
    motif_du_service_retenu: str | None


@routeur.post(
    "/dossiers/{reference}/service",
    summary="Retenir un autre service que celui demandé",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
        409: {"description": "Le dossier est trop avancé, ou le service est déjà celui-là"},
        422: {"description": "Service inconnu du catalogue"},
    },
)
def retenir_un_autre_service(
    acces: AccesRequis, reference: str, corps: ServiceARetenir
) -> DossierApresReprise:
    """Corrige le service après l'échange, sans effacer ce qu'elle a demandé.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE GESTE N'EXISTAIT PAS, ET C'ÉTAIT UN CUL-DE-SAC.**

    La visiteuse coche une case sur un formulaire public, **avant tout échange**.
    C'est précisément ce que le responsable découvre en l'appelant : elle a coché
    « prestation ponctuelle » et il lui faut une adhésion.

    Sans ce geste, le dossier était coincé. Le questionnaire est celui du mauvais
    service, le chiffrage aussi, et la seule sortie était le classement sans
    suite — qui perd l'échange et oblige la cliente à tout redéposer.

    ⚠️ **LA DEMANDE N'EST PAS RÉÉCRITE.** Le domaine porte la demande « sans la
    modifier jamais » : c'est la trace de ce qu'elle a dit. Le cabinet inscrit à
    côté ce qu'il retient, et la fiche montre les deux. Effacer son choix ferait
    disparaître le fait qu'elle s'est trompée de case, qui est une information
    utile au cabinet comme au site : trois clientes qui se trompent de la même
    manière disent quelque chose du formulaire.

    ⚠️ **LE CODE EST CONTRÔLÉ COMME AU DÉPÔT.** Sans quoi cette route rouvrirait
    par l'arrière la porte qu'on vient de fermer côté public.

    ⚠️ **`QUALIFIER_PROSPECT`**, la permission de celui qui mène l'échange. Ce
    n'est pas une correction de saisie : c'est une décision commerciale prise
    après avoir parlé au client.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        _exiger_un_service_du_catalogue(corps.service)
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        repris = dossier.retenir_le_service(
            corps.service, motif=corps.motif, a_l_instant=maintenant()
        )
    except TransitionDossierRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    depot.enregistrer(repris)
    return DossierApresReprise(
        reference=repris.reference,
        etat=repris.etat.value,
        service_souhaite=repris.demande.service_souhaite,
        service=repris.service,
        motif_du_service_retenu=repris.motif_du_service_retenu,
    )


@routeur.post(
    "/dossiers/{reference}/premier-contact",
    summary="Déclarer que le client a été joint",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
    },
)
def declarer_le_premier_contact(
    acces: AccesRequis, reference: str, corps: ContactPris
) -> DossierApresContact:
    """Le geste qui fait passer un dossier de `AFFECTÉE` à `EN_CONVERSATION`.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ CE GESTE N'AVAIT AUCUNE ROUTE, ET DEUX CHOSES EN DÉPENDAIENT.

    `premier_contact` existait au domaine, `enregistrer_un_appel` au cas d'usage,
    et **rien ne les appelait depuis l'extérieur** : le seul chemin vers
    `EN_CONVERSATION` passait par l'enregistrement d'une qualification.

    **Un.** `premier_echange_le` mesure « la réactivité du cabinet ». Un
    responsable qui appelait son client sans pouvoir le qualifier — parce que le
    client n'était pas prêt, parce qu'il fallait un devis d'abord — laissait le
    dossier `AFFECTÉE`, et la veille des vingt-quatre heures le réaffectait à un
    collègue qui rappelait le même client.

    **Deux.** `QUALIFIÉE` exige un questionnaire. `FORMATION`, `DOMICILIATION` et
    `PONCTUEL` n'en ont pas : leurs dossiers ne pouvaient **jamais** quitter
    `AFFECTÉE`. Le site public les proposait, un responsable leur était désigné,
    et ils mouraient là. Vérifié pas à pas sur la pile le 29 septembre.

    ⚠️ **IL EST REJOUABLE, ET NE MENT PAS.** Un dossier déjà en conversation ou
    plus avancé rend `200` sans rien changer : le responsable qui clique deux fois
    n'a pas commis de faute, et lever ici ferait ressembler un double clic à un
    incident. La date du **premier** échange ne se réécrit pas.

    ⚠️ **IL NE SAUTE AUCUNE ÉTAPE.** Un dossier `DÉPOSÉE`, sans responsable, est
    refusé par le domaine : déclarer un contact sur un dossier que personne ne
    tient laisserait un échange sans auteur.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    if dossier.etat is EtatDossier.AFFECTEE:
        dossier = dossier.premier_contact(maintenant())
        depot.enregistrer(dossier)
    return DossierApresContact(
        reference=dossier.reference,
        etat=dossier.etat.value,
        premier_echange_le=dossier.premier_echange_le,
    )


@routeur.post(
    "/rappels/{identifiant}/fait",
    summary="Clore un rappel après avoir appelé",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Aucun rappel sous cet identifiant"},
        409: {"description": "Rappel déjà clos"},
    },
)
def clore_un_rappel(identifiant: str, corps: Cloture, acces: AccesRequis) -> RappelVu:
    """Marque le rappel comme passé. **Refuse le second appel.**

    ⚠️ `409` et non `200` sur un rappel déjà clos. Répondre `200` ferait qu'un
    second collaborateur croirait avoir pris le contact alors que le premier l'avait
    pris : deux appels au même client, à quelques minutes, sur le même sujet.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    depot = _rappels()
    try:
        rappel = depot.lire(identifiant)
    except RappelIntrouvable as absent:
        raise HTTPException(status_code=404, detail=str(absent)) from absent
    instant = maintenant()
    try:
        clos = rappel.fait(acces.compte, instant)
    except RappelDejaFait as deja:
        raise HTTPException(status_code=409, detail=str(deja)) from deja
    depot.enregistrer(clos)
    _consigner_le_premier_contact(clos.dossier, instant)
    return RappelVu(**clos.model_dump())


def _consigner_le_premier_contact(reference: str, instant: datetime) -> None:
    """Un rappel clos est un appel passé : le dossier avance avec lui.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ CE GESTE NE TOUCHAIT PAS AU DOSSIER, ET CELA COÛTAIT DEUX FOIS.

    **Un.** Un responsable qui appelait vraiment son client, puis cochait le
    rappel, laissait le dossier en `AFFECTÉE`. La veille des vingt-quatre heures
    le réaffectait donc à quelqu'un d'autre — qui rappelait le même client, sur le
    même sujet, quelques heures plus tard. `premier_echange_le` mesure « la
    réactivité du cabinet » ; il n'était renseigné que par la qualification.

    **Deux.** `QUALIFIÉE` ne s'atteint que par la qualification, et la
    qualification exige un questionnaire. Un service qui n'en a pas — `FORMATION`,
    `DOMICILIATION`, `PONCTUEL` — ne pouvait donc **jamais** quitter `AFFECTÉE`,
    ni être chiffré, ni être vendu. Le site les proposait pourtant.

    ⚠️ **AUCUNE EXCEPTION N'EST LEVÉE ICI.** Le rappel EST clos, et il doit le
    rester : refuser la requête parce que le dossier a déjà avancé ferait
    rappeler le client une seconde fois. `enregistrer_un_appel` le dit déjà à sa
    façon — « le dossier n'avance pas s'il n'est pas à AFFECTÉE », sans forcer
    aucune transition que le domaine refuse.

    ⚠️ Un dossier introuvable se tait aussi : le carnet des rappels survit à un
    dossier classé, et perdre la clôture d'un appel réel serait pire que le
    silence.
    ─────────────────────────────────────────────────────────────────────────────
    """
    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable:
        return
    if dossier.etat is not EtatDossier.AFFECTEE:
        return
    depot.enregistrer(dossier.premier_contact(instant))


def _rappels():
    """Le carnet, durable si la base est là.

    ⚠️ Le magasin mémoire est **celui de l'abonné**, et non un second : deux caches
    seraient deux carnets, l'abonné écrirait dans l'un et cet écran lirait l'autre.
    Le responsable ne verrait jamais rien, sans qu'aucune erreur ne se produise.
    """
    from app.contextes.souscription.adaptateurs.entrant.abonne_de_relance import (
        _rappels_memoire,
    )

    session = session_de_travail()
    if session is None:
        return _rappels_memoire()
    return DepotRappelsSql(session, courant())


@routeur.get(
    "/dossiers/{reference}/qualification",
    summary="Où en est la qualification de ce dossier",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
    },
    response_model_exclude_unset=True,
)
def lire_la_qualification(acces: AccesRequis, reference: str) -> EtatDeQualification:
    """Ce qu'on a déjà appris, et ce qui manque encore.

    ⚠️ **Sans cette route, la qualification conservée serait invisible.** Le
    responsable qui reprend un dossier a besoin de savoir où il en est avant de
    rappeler le client ; sans elle, il ne pourrait le découvrir qu'en renvoyant des
    réponses, c'est-à-dire en écrivant pour lire.

    ⚠️ L'avancement se mesure contre le questionnaire de la **version employée**,
    conservée avec les réponses. Le comparer à la version du jour rendrait
    « incomplètes » toutes les qualifications closes dès qu'une question est
    ajoutée.
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    try:
        dossier = _dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    qualification = _qualifications().trouver(reference)
    # ⚠️ TRAITÉ EN 404, COMME LES TROIS AUTRES APPELS. C'était le dernier qui
    # laissait l'exception s'échapper, et il tombait en **500** : la fiche du
    # dossier ne s'ouvrait plus du tout pour un service sans questionnaire —
    # `FORMATION`, `DOMICILIATION`, ou un dossier déposé sous un ancien code.
    # Le collaborateur voyait une page d'erreur au lieu du dossier de son client.
    try:
        questionnaire = _referentiel_d_acquisition()["questionnaires"].prendre(
            dossier.service
        )
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    if qualification is None:
        # Pas encore commencée : l'état de tout dossier neuf. Un 404 laisserait
        # croire que le dossier n'existe pas, alors qu'il attend seulement d'être
        # qualifié.
        return EtatDeQualification(
            dossier=reference,
            service=questionnaire.service,
            commencee=False,
            complete=False,
            manquantes=[q.code for q in questionnaire.questions],
            avancement=Avancement(repondues=0, total=len(questionnaire.questions)),
        )

    repondues, total = qualification.avancement(questionnaire)
    return EtatDeQualification(
        dossier=reference,
        service=qualification.service,
        version_questionnaire=qualification.version_questionnaire,
        commencee=True,
        complete=qualification.complete(questionnaire),
        manquantes=list(qualification.manquantes(questionnaire)),
        avancement=Avancement(repondues=repondues, total=total),
        note=qualification.note,
        faits={code: str(valeur) for code, valeur in qualification.faits().items()},
    )


def _qualifications():
    """Le dépôt des qualifications, durable si la base est là."""
    session = session_de_travail()
    if session is None:
        return _qualifications_memoire()
    return DepotQualificationsSql(session, courant())


@lru_cache
def _qualifications_memoire() -> DepotQualificationsMemoire:
    return DepotQualificationsMemoire()


def _candidatures_du_cabinet(service: str, dossiers) -> list[Candidature]:
    """Les collaborateurs en mesure de prendre ce dossier, et ce qu'ils portent déjà.

    ⚠️ **La charge est lue au moment de l'affectation**, jamais mémorisée. Deux
    dossiers déposés à une minute d'intervalle doivent voir des charges différentes,
    sans quoi ils iraient tous deux au même collaborateur : c'est exactement le
    déséquilibre que le critère de charge existe pour éviter.
    """
    from app.contextes.transverse.api import atelier

    boutique = atelier()
    return monter_les_candidatures(
        comptes=boutique.comptes,
        habilitations=boutique.habilitations,
        charge=dossiers.charge_par_responsable(),
        service=service,
        a_la_date=maintenant().date(),
    )


# ── La proforma : émission, transmission, acceptation ────────────────────────


class TarifAArreter(BaseModel):
    """Ce que le responsable arrête, à partir de la proposition du chiffrage.

    ⚠️ **Le montant est arrêté par un humain, jamais recopié du chiffrage.** Le
    moteur propose un intervalle ; c'est un collaborateur habilité qui décide, et
    le motif devient obligatoire hors de l'intervalle. Recopier la référence par
    défaut ferait disparaître la décision derrière un automatisme, et le jour d'un
    litige personne ne saurait qui a fixé le prix.
    """

    montant: Decimal = Field(gt=0)
    #: ⚠️ Les mêmes entrées que le chiffrage : l'intervalle est **recalculé** à
    #: l'émission, jamais reçu (pas 65). Plancher, référence, plafond et version du
    #: barème ne se déclarent plus.
    score_charge: int = Field(default=0, ge=0)
    debours: list[DeboursEntrant] = Field(default_factory=list)
    #: Obligatoire hors de l'intervalle. Le domaine le refuse sinon.
    motif: str | None = None
    #: ⚠️ Envoyer la proposition au client par courriel, **au moment de l'émission**.
    #:
    #: C'est le seul instant où le lien d'acceptation existe en clair (voir la route).
    #: Le serveur l'écrit directement dans le courriel du client : le lien ne passe
    #: ni par l'écran du responsable, ni par un copier-coller. Faux par défaut, pour
    #: que les appels existants gardent leur comportement exact.
    envoyer_par_courriel: bool = False
    #: ⚠️ **LA COMPOSITION DU DOCUMENT**, choisie par le responsable au moment où il
    #: arrête le prix. C'est le « sélectionner les services » du parcours : le même
    #: montant n'a pas le même détail selon que le suivi sur douze mois est inclus.
    #:
    #: Absente, le document PDF n'est pas produit et l'empreinte reste calculée sur
    #: le résumé textuel, comme pour toutes les proformas émises avant le pas 148.
    #: Les codes disponibles sont ceux de `Docs/referentiel/tarification/`.
    composition: str | None = None

    # ⚠️ **`valide_par` n'est plus reçu** (pas 63), et tout champ inconnu est refusé.
    # Le corps disait qui engageait le cabinet sur ce prix ; le contrôle interne lit
    # `chiffre_par != valide_par` pour dire si la séparation des tâches est respectée,
    # et écrire « direction » ici simulait un contrôle que personne n'avait fait.
    model_config = ConfigDict(extra="forbid")


class ProformaEmise(BaseModel):
    numero: str
    version: int
    etat: str
    montant: Decimal
    empreinte_document: str
    #: ⚠️ LE SCEAU SEUL, malgré son nom. Conservé tel quel : deux routes le
    #: rendent et la vitrine le lit. Voir `lien_client`, qui porte l'adresse.
    lien_acceptation: str | None = None
    #: L'adresse complète que la cliente ouvre, prête à coller.
    #:
    #: ⚠️ AJOUTÉE LE 28 SEPTEMBRE POUR LA VOIE WHATSAPP. La console devait sinon
    #: recomposer l'adresse à partir du sceau, de la version et de l'expiration :
    #: deux recettes pour une même adresse, qui auraient divergé au premier
    #: paramètre renommé — et la cliente aurait reçu un lien vers une page
    #: d'erreur. Elle est composée là où le courriel la compose.
    lien_client: str | None = None
    #: Le téléphone du prospect, au format international, pour ouvrir WhatsApp.
    #: ⚠️ Rendu seulement à l'émission, à celui qui détient déjà la fiche.
    telephone: str | None = None
    expire_le: datetime | None = None
    #: L'intervalle recalculé à l'émission (pas 65), pour que la console dise où le
    #: montant arrêté se situe.
    plancher: Decimal | None = None
    reference: Decimal | None = None
    plafond: Decimal | None = None
    #: Les faits sur lesquels le prix repose, score de charge déclaré compris (pas 65).
    faits: dict[str, str] = Field(default_factory=dict)
    #: Qui a chiffré, et qui a engagé le cabinet (pas 63). Rendus pour que la console
    #: dise si la séparation des tâches est respectée, au lieu de le laisser croire.
    chiffre_par: str
    valide_par: str
    separation_respectee: bool
    #: Ce qu'est devenu l'envoi par courriel demandé à l'émission, s'il l'a été :
    #:   ENVOYE        le courriel est parti, et la proforma est marquée transmise
    #:   SANS_ADRESSE  la demande de contact ne porte aucun courriel
    #:   REFUSE        le service de courriel a refusé l'envoi (à retenter autrement)
    #: ⚠️ Un échec d'envoi n'annule PAS l'émission : la proforma est numérotée et
    #: figée, le responsable la transmet alors par un autre canal.
    courriel: str | None = None
    #: L'adresse, masquée, à laquelle le courriel est parti : de quoi confirmer au
    #: responsable sans réafficher l'adresse entière.
    courriel_masque: str | None = None
    transmise_le: datetime | None = None


class Acceptee(BaseModel):
    numero: str
    version: int
    montant: Decimal
    acceptee_le: datetime
    identite_declaree: str


class ProformaConsultee(BaseModel):
    """Ce que le client lit avant d'accepter. **Rien de ce qui est interne au cabinet.**

    Ni l'intervalle du barème, ni qui a chiffré, ni les faits de la qualification : le
    client accepte un prix et ce qui le compose, pas la marge de négociation du cabinet.
    """

    numero: str
    version: int
    service: str
    etat: str
    montant: Decimal
    lignes: list[dict[str, str]]
    debours: list[dict[str, str]]
    expire_le: datetime
    acceptee: bool


class DemandeDAcceptation(BaseModel):
    """Ce que le client envoie en acceptant. **Publique, donc minimale.**"""

    sceau: str = Field(min_length=16)
    expire_le: datetime
    version: int = Field(ge=1)
    #: ⚠️ Déclarée, jamais vérifiée : on n'a pas de pièce d'identité, et prétendre
    #: le contraire serait une fausse garantie. Elle sert au litige.
    identite_declaree: str = Field(min_length=2, max_length=120)


# ── Le document de la proforma ───────────────────────────────────────────────
#
# ⚠️ **L'EMPREINTE PORTE LE PDF, ET NON PLUS UN RÉSUMÉ.** Jusqu'au pas 148, le
# document n'existait pas : `empreinte_document` hachait la chaîne
# « numéro|dossier|montant|instant ». Elle prouvait que ces quatre valeurs
# n'avaient pas bougé, ce qui n'est pas rien, mais elle ne prouvait rien du
# papier que la cliente lit et sur lequel elle s'engage.
#
# Le PDF produit ici est **déterministe** : deux rendus des mêmes données donnent
# le même octet, et donc la même empreinte. C'est ce qui rend `document_intact`
# vérifiable, et c'est pourquoi le rendu passe `invariant=1` à reportlab.
#
# ⚠️ **LES PROFORMAS DÉJÀ ÉMISES NE BOUGENT PAS.** Leur empreinte reste celle du
# résumé, et c'est la seule réponse honnête : recalculer donnerait une empreinte
# qui ne correspond à aucun document jamais envoyé. Sans composition choisie, le
# comportement d'avant est donc conservé tel quel.


def magasin_des_proformas() -> MagasinFichiersLocal:
    """Le magasin où dort le PDF d'une proforma.

    Le même magasin adressé par contenu que la collecte, promu au partage pour
    que la Souscription y accède sans qu'une arête « souscription lit collecte »
    soit ajoutée au graphe pour un besoin de stockage.

    ⚠️ **La clé du fichier EST son empreinte**, donc `empreinte_document`. Ce
    n'est pas une coïncidence qu'on exploite, c'est la propriété du magasin :
    retrouver le document d'une proforma ne demande aucune table de
    correspondance, et un document rangé sous une autre clé que son contenu est
    impossible à produire.

    ⚠️ **Le locataire est celui de la requête**, et non la constante par défaut
    que la collecte emploie encore. `app/partage/locataire.py` dit pourquoi : un
    défaut sert les données d'un cabinet à un autre sans que rien n'échoue. Le
    chemin étant neuf, il n'a aucune raison de naître avec la dette.
    """
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


@lru_cache
def _compositions() -> RepertoireDesCompositions:
    """Le référentiel des compositions, lu une fois par processus.

    ⚠️ **LE CHEMIN VIENT DE LA CONFIGURATION, JAMAIS DU DÉPÔT.** Écrit d'abord
    en dur sur `RACINE_DEPOT / "Docs" / "referentiel"`, il marchait en local et
    rendait une liste **vide** en conteneur, sans erreur : l'image range les
    référentiels sous `/application/donnees/referentiel`. Le responsable n'aurait
    eu aucun document à proposer, et rien ne lui aurait dit pourquoi.

    Vu en interrogeant la pile réelle, pas la suite de tests : celle-ci tourne
    depuis le dépôt, où les deux chemins coïncident.
    """
    return RepertoireDesCompositions.depuis(
        configuration().dossier_referentiel / "tarification"
    )


def _document_a_emettre(
    code_composition: str | None,
    *,
    dossier,
    numero: str,
    reference: str,
    tarif: TarifArrete,
    version_bareme: str,
    instant: datetime,
) -> tuple[bytes, str, str]:
    """Le contenu à hacher, et ce qui identifie le modèle qui l'a produit.

    Rend le triplet `(contenu, modèle, version du modèle)`. Avec une composition,
    le contenu est le PDF lui-même, rangé au passage ; sans, le résumé d'avant.

    ⚠️ **Le rangement se fait ici, avant l'émission**, et non après
    l'enregistrement : une proforma enregistrée dont le document manque serait
    une proforma qu'on ne peut plus montrer, et l'incohérence ne se découvrirait
    qu'au moment où la cliente clique. Le magasin étant adressé par contenu,
    ranger un document dont l'émission échoue ensuite ne coûte que trois kilo
    octets orphelins, ce qui est infiniment préférable.
    """
    if not code_composition:
        return (
            f"{numero}|{reference}|{tarif.montant}|{instant.isoformat()}".encode(),
            dossier.service,
            version_bareme,
        )

    try:
        composition = _compositions().prendre(code_composition, instant.date())
    except CompositionIntrouvable as absente:
        raise HTTPException(status_code=422, detail=str(absente)) from absente

    pdf = rendre_le_document(
        composition,
        numero=numero,
        version=1,
        emise_le=instant,
        client=dossier.demande.nom,
        # ⚠️ Le total arrêté par le responsable, et non celui de la composition.
        # Il a le droit de s'en écarter, motif à l'appui ; le document doit alors
        # porter les deux, sans quoi la cliente signerait un total que le détail
        # sous ses yeux ne justifie pas.
        total_arrete=tarif.montant,
    )
    magasin_des_proformas().deposer(
        empreinte(pdf), pdf, type_mime="application/pdf"
    )
    return pdf, composition.code, composition.statut


class RubriqueOfferte(BaseModel):
    """Une rubrique telle que l'écran la montre au responsable."""

    numero: str
    intitule: str
    lignes: list[dict[str, str]]
    sous_total: Decimal


class CompositionOfferte(BaseModel):
    """Une composition proposable à l'émission."""

    code: str
    service: str
    forme_juridique: str
    titre: str
    libelle: str
    total: Decimal
    #: ⚠️ `TRANSCRIT` tant que la direction n'a pas confirmé les montants.
    #: L'écran doit le montrer : un responsable qui envoie une proforma issue
    #: d'une transcription non validée doit le savoir avant, pas après.
    statut: str
    rubriques: list[RubriqueOfferte]


@routeur.get(
    "/compositions-proforma",
    summary="Les compositions de document proposables à l'émission",
    responses={403: {"description": "Habilitation insuffisante"}},
)
def lister_les_compositions(
    acces: AccesRequis, service: str | None = Query(default=None)
) -> list[CompositionOfferte]:
    """Le catalogue des documents, filtré sur le service quand on le précise.

    ⚠️ Cette route existe pour la même raison que celle des motifs de
    classement : une liste recopiée dans l'écran diverge au premier document
    ajouté, et le formulaire proposerait alors des codes que le serveur refuse.

    ⚠️ **LE DÉTAIL EST RENDU, PAS SEULEMENT LE CODE.** Le responsable choisit un
    document dont il doit voir les rubriques avant de l'envoyer à une cliente.
    Un menu déroulant de codes nus l'obligerait à ouvrir le référentiel à côté.
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    aujourdhui = maintenant().date()
    return [
        CompositionOfferte(
            code=composition.code,
            service=composition.service,
            forme_juridique=composition.forme_juridique,
            titre=composition.titre,
            libelle=composition.libelle,
            total=composition.total,
            statut=composition.statut,
            rubriques=[
                RubriqueOfferte(
                    numero=rubrique.numero,
                    intitule=rubrique.intitule,
                    lignes=[
                        {"libelle": ligne.libelle, "montant": str(ligne.montant)}
                        for ligne in rubrique.lignes
                    ],
                    sous_total=rubrique.sous_total,
                )
                for rubrique in composition.rubriques
            ],
        )
        for composition in _compositions().toutes()
        # ⚠️ Seulement celles en vigueur aujourd'hui : proposer une composition
        # périmée ferait émettre un document aux montants de l'an dernier.
        if composition.couvre(aujourdhui)
        and (service is None or composition.service == service)
    ]


@routeur.post(
    "/dossiers/{reference}/proforma",
    summary="Émettre la proforma d'un dossier chiffré",
    status_code=201,
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Dossier inconnu"},
        409: {"description": "Le dossier n'est pas au stade du chiffrage"},
        422: {"description": "Tarif hors intervalle sans motif"},
    },
)
def emettre_la_proforma(
    acces: AccesRequis, reference: str, corps: TarifAArreter
) -> ProformaEmise:
    """Fige le document, le numérote dans la série, et rend son lien d'acceptation.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE GESTE ÉTAIT LE SEUL DU PARCOURS SANS ROUTE.**

    Le domaine existe depuis le pas 10, éprouvé ; la surface HTTP manquait. La
    recette de bout en bout devait passer par le cas d'usage, ce qui veut dire
    qu'aucune console n'aurait pu émettre une proforma.

    LE NUMÉRO EST TIRÉ DE LA SÉRIE, ET LE CONFLIT EST POSSIBLE

    `dernier_numero` peut mentir : entre la lecture et l'écriture, une autre requête
    a pu émettre. C'est la contrainte d'unicité qui arbitre, et le refus rend `409`
    plutôt que `500` : le responsable renvoie sa demande, et la seconde tentative
    prend le numéro suivant.

    Verrouiller la série entière sérialiserait toutes les émissions du cabinet pour
    une garantie identique.

    ⚠️ **LE LIEN N'EST RENDU QU'ICI.** Il porte un sceau à usage unique ; le
    remettre à chaque lecture de la proforma multiplierait les chemins par lesquels
    un engagement contractuel peut fuiter.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    instant = maintenant()
    config = configuration()
    proposition, faits_conserves = _proposition_du_dossier(
        dossier, corps.score_charge, corps.debours
    )
    try:
        tarif = TarifArrete(
            montant=corps.montant,
            plancher=proposition.plancher,
            reference=proposition.reference,
            plafond=proposition.plafond,
            version_bareme=proposition.version_bareme,
            # ⚠️ Le compte qui chiffre est celui de la session, jamais un champ du
            # corps. Le laisser déclarer permettrait d'attribuer un tarif à un
            # collègue, et le jour d'un litige la trace désignerait la mauvaise
            # personne.
            chiffre_par=acces.compte,
            # ⚠️ Le même compte, et c'est la vérité : aucun geste distinct de
            # validation n'existe encore. `separation_respectee` vaut donc `False`, et
            # le contrôle interne le verra, au lieu de lire une validation déclarée.
            # Le jour où une validation par un second collaborateur existera, elle
            # sera sa propre route, sous sa propre session.
            valide_par=acces.compte,
            arrete_le=instant,
            motif=corps.motif,
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    proformas = _proformas()
    numero = numero_suivant(
        proformas.dernier_numero(config.serie_proforma, instant.year),
        serie=config.serie_proforma,
        annee=instant.year,
    )
    contenu, modele, version_modele = _document_a_emettre(
        corps.composition,
        dossier=dossier,
        numero=numero,
        reference=reference,
        tarif=tarif,
        version_bareme=proposition.version_bareme,
        instant=instant,
    )

    try:
        proforma = emettre(
            numero=numero,
            dossier=reference,
            service=dossier.service,
            tarif=tarif,
            contenu=contenu,
            modele=modele,
            version_modele=version_modele,
            a_l_instant=instant,
            # ⚠️ Ces trois champs existaient au domaine et n'étaient jamais remplis :
            # la proforma émise ne gardait ni les lignes du calcul, ni les débours, ni
            # les faits sur lesquels le prix reposait (pas 65).
            lignes=proposition.lignes,
            debours=proposition.debours,
            faits=faits_conserves,
        )
        avance = dossier.emettre_la_proforma(instant)
    except TransitionDossierRefusee as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    lien = _lien_de(proforma)
    try:
        proformas.enregistrer(proforma)
        depot.enregistrer(avance)
    except IntegrityError as conflit:
        raise HTTPException(
            status_code=409,
            detail=(
                f"le numéro {numero} vient d'être pris par une autre émission. "
                "Renvoyer la demande : la suivante prendra le numéro d'après."
            ),
        ) from conflit

    courriel, courriel_masque = None, None
    if corps.envoyer_par_courriel:
        courriel, courriel_masque = _envoyer_la_proforma_par_courriel(
            proforma, dossier.demande, lien
        )
        if courriel == "ENVOYE":
            # ⚠️ Le courriel est parti : c'est une transmission, et c'est elle qui
            # arme la relance. Sans cette ligne, le client aurait sa proposition et
            # personne ne le relancerait jamais.
            proforma = proforma.transmise(instant)
            proformas.enregistrer(proforma)

    return ProformaEmise(
        numero=proforma.numero,
        version=proforma.version,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        empreinte_document=proforma.empreinte_document,
        courriel=courriel,
        courriel_masque=courriel_masque,
        transmise_le=proforma.transmise_le,
        lien_acceptation=lien.sceau,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        telephone=dossier.demande.telephone,
        expire_le=lien.expire_le,
        plancher=proforma.tarif.plancher,
        reference=proforma.tarif.reference,
        plafond=proforma.tarif.plafond,
        faits=proforma.faits,
        chiffre_par=proforma.tarif.chiffre_par,
        valide_par=proforma.tarif.valide_par,
        separation_respectee=proforma.tarif.separation_respectee,
    )


def adresse_de_la_proforma(numero: str, version: int, expire_le: datetime, sceau: str) -> str:
    """L'adresse complète que le client ouvre, sur la vitrine.

    ⚠️ Les mêmes paramètres courts que la page de la vitrine lit (`v`, `e`, `s`) :
    changer l'un sans l'autre donnerait au client un lien qui ouvre une page d'erreur.
    La date est encodée, parce que le `+` d'un fuseau horaire deviendrait une espace.
    """
    base = configuration().adresse_publique_vitrine.rstrip("/")
    requete = urlencode({"v": version, "e": expire_le.isoformat(), "s": sceau})
    return f"{base}/fr/proforma/{quote(numero)}?{requete}"


def _envoyer_la_proforma_par_courriel(
    proforma: Proforma, demande: DemandeDeContact, lien: LienDAcceptation
) -> tuple[str, str | None]:
    """Écrit au client, et dit ce qui s'est passé. **Ne lève jamais.**

    Le service de notification rend un booléen et ne lève pas (voir son en-tête) :
    l'émission, elle, est déjà écrite en base. Faire échouer la requête ici ferait
    croire au responsable que la proforma n'existe pas, alors qu'elle a pris son
    numéro dans la série.
    """
    if not demande.courriel:
        return "SANS_ADRESSE", None
    parti = service_de_notification().envoyer(
        "proforma.envoi",
        destinataire=demande.courriel,
        contexte={
            "nom": demande.nom,
            "proforma": proforma.numero,
            # « 1 250 000 » : l'espace fine insécable des montants français.
            "montant": f"{proforma.tarif.montant:,.0f}".replace(",", "\u202f"),
            "lien": adresse_de_la_proforma(
                lien.numero, lien.version, lien.expire_le, lien.sceau
            ),
            "expire_le": lien.expire_le.strftime("%d/%m/%Y"),
        },
    )
    nom, _, domaine = demande.courriel.partition("@")
    masque = f"{nom[:1]}***@{domaine}"
    return ("ENVOYE" if parti else "REFUSE"), masque


class LienDeLaProforma(BaseModel):
    """De quoi renvoyer une proposition au client, sans rien changer au dossier."""

    numero: str
    #: ⚠️ Un entier, comme sur `ProformaEmise` : une proforma corrigée change de
    #: version, et l'adresse porte laquelle. Deux types pour un même champ
    #: feraient diverger les deux écrans qui le lisent.
    version: int
    etat: str
    montant: Decimal
    #: L'adresse complète que le client ouvrira. Composée par le serveur, comme
    #: le courriel la compose. Voir `adresse_de_la_proforma`.
    lien_client: str
    expire_le: datetime
    #: Pour composer le message WhatsApp du cabinet. Jamais masqué ici : cette
    #: route exige `QUALIFIER_PROSPECT`, donc son appelant traite déjà ce dossier.
    telephone: str
    nom: str
    #: `False` quand le client n'a pas autorisé la messagerie à sa demande.
    whatsapp_autorise: bool


@routeur.get(
    "/proformas/{numero}/lien",
    summary="Retrouver le lien d'une proforma, pour le renvoyer au client",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
    },
)
def lien_de_la_proforma(acces: AccesRequis, numero: str) -> LienDeLaProforma:
    """Le lien du client, relu. **Aucun effet de bord, et c'est tout l'objet.**

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE ROUTE EXISTE

    Le lien d'acceptation n'était rendu qu'à **l'émission**. La console le gardait
    dans l'état d'un composant, et le perdait au premier rechargement de page :
    le bouton « Envoyer sur WhatsApp » disparaissait alors pour toujours.

    Constaté le 29 septembre, sur sept dossiers réels en `PROFORMA_EMISE` : aucun
    n'affichait le bouton. Le responsable qui revenait le lendemain sur un dossier
    n'avait plus aucun moyen de renvoyer la proposition autrement que par
    courriel.

    ⚠️ **POURQUOI PAS SUR LA FICHE DU DOSSIER.** Elle n'exige que `LIRE_PROSPECT`,
    et son en-tête le dit : « jamais le lien d'acceptation ». Ce lien est un
    porteur — qui l'a, peut accepter. L'élargir à tous les lecteurs de prospects
    serait une extension silencieuse de ce que le sceau protège.

    ⚠️ **POURQUOI PAS `POST /transmission`.** Elle rend déjà le lien, et la console
    aurait pu s'en servir. Mais elle **date l'envoi**, et c'est cette date qui arme
    la relance : la rappeler pour relire un lien remettrait le compteur à zéro à
    chaque consultation, et le client ne serait jamais relancé. Pire, elle
    obligerait à déclarer « j'ai envoyé le lien » **avant** de l'avoir.

    Une lecture est une lecture. Elle ne note rien.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        proforma = _proformas().lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    demande = _dossiers().lire(proforma.dossier).demande
    lien = _lien_de(proforma)
    return LienDeLaProforma(
        numero=proforma.numero,
        version=proforma.version,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        expire_le=lien.expire_le,
        telephone=demande.telephone,
        nom=demande.nom,
        whatsapp_autorise=demande.consentement.vaut_maintenant,
    )


@routeur.post(
    "/proformas/{numero}/transmission",
    summary="Marquer la proforma transmise au client",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
        409: {"description": "État incompatible"},
    },
)
def transmettre(acces: AccesRequis, numero: str) -> ProformaEmise:
    """Note que le document est parti. **C'est cette date qui arme la relance.**

    ⚠️ Sans elle, le balayage ne voit rien : `a_relancer` cherche les transmises
    dont la date est renseignée. Une proforma émise et jamais marquée transmise
    n'est relancée par personne, et le dossier dort.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    proformas = _proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    try:
        transmise = proforma.transmise(maintenant())
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus

    proformas.enregistrer(transmise)
    # ⚠️ LE LIEN EST RENDU ICI AUSSI, et c'est une correction du 27 septembre.
    #
    # Il n'était rendu qu'à l'émission. Un client qui perd son courriel n'avait
    # plus de chemin vers sa proforma, et le cabinet n'avait d'autre recours que
    # d'émettre une v2 d'un document que personne n'avait contesté — ce qui
    # change le numéro, invalide l'ancien lien, et brouille le suivi.
    #
    # Le rendre ici est sans risque : cette route demande déjà
    # `QUALIFIER_PROSPECT`, et le lien ne dit rien que son porteur ne doive voir.
    lien = _lien_de(transmise)
    return ProformaEmise(
        numero=transmise.numero,
        version=transmise.version,
        etat=transmise.etat.value,
        montant=transmise.tarif.montant,
        empreinte_document=transmise.empreinte_document,
        lien_acceptation=lien.sceau,
        lien_client=adresse_de_la_proforma(
            lien.numero, lien.version, lien.expire_le, lien.sceau
        ),
        expire_le=lien.expire_le,
        transmise_le=transmise.transmise_le,
        chiffre_par=transmise.tarif.chiffre_par,
        valide_par=transmise.tarif.valide_par,
        separation_respectee=transmise.tarif.separation_respectee,
    )


def _lien_de(proforma: Proforma) -> LienDAcceptation:
    """Le lien d'acceptation d'une proforma, **recalculé** et non relu.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ LE LIEN N'EST PAS PERSISTÉ, ET C'EST UN CHOIX QUI SE TIENT.

    Il ne contient rien que la proforma ne porte déjà : son numéro, sa version,
    et une date d'expiration qui vaut `emise_le + N jours`. Le sceau en est la
    signature. Le ranger en base ajouterait une donnée dérivée à tenir
    cohérente, pour rien.

    ⚠️ MAIS IL ÉTAIT RENDU UNE SEULE FOIS, ET C'ÉTAIT LE DÉFAUT.

    Constaté le 27 septembre en jouant le parcours d'une création souscrite :
    l'émission rendait le sceau, la transmission ne le rendait pas, et rien ne
    permettait de le retrouver. Un collaborateur dont le client a perdu son
    courriel ne pouvait pas lui renvoyer son lien — il fallait émettre une
    nouvelle version d'un document que personne n'avait contesté.

    Le calcul vit donc ici, en un seul endroit, et les deux routes s'en servent.
    ─────────────────────────────────────────────────────────────────────────────
    """
    return lien_pour(
        proforma,
        expire_le=proforma.emise_le
        + timedelta(days=configuration().validite_lien_acceptation_jours),
        secret=_secret_des_liens(),
    )


def _lien_presente(numero: str, version: int, expire_le: datetime, sceau: str) -> LienDAcceptation:
    """Le lien tel que le client le présente, ou un refus **identique à un sceau faux**.

    ⚠️ Un sceau malformé levait une erreur de validation non rattrapée, donc une erreur
    500 : une réponse distincte de celle d'un sceau faux, et une page d'erreur pour un
    visiteur. Il reçoit désormais le même refus qu'un sceau bien formé mais faux.
    """
    try:
        return LienDAcceptation(numero=numero, version=version, expire_le=expire_le, sceau=sceau)
    except ValidationError as refus:
        raise HTTPException(
            status_code=422,
            detail=(
                f"lien de {numero} : sceau invalide. Le lien a été modifié, ou il vient "
                "d'un autre environnement."
            ),
        ) from refus


@routeur.get(
    "/proformas/{numero}/consultation",
    summary="Le client lit sa proforma par son lien signé",
    responses={
        404: {"description": "Proforma inconnue"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def consulter_la_proforma(
    numero: str,
    version: int = Query(ge=1),
    expire_le: datetime = Query(),
    sceau: str = Query(min_length=16),
) -> ProformaConsultee:
    """⚠️ **Publique, comme l'acceptation, et pour la même raison.** (pas 67)

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI CETTE ROUTE EXISTE

    L'acceptation était publique et scellée, mais **aucune route ne montrait au client
    ce qu'il acceptait**. Un engagement signé sur un prix qu'on n'a pas vu ne se défend
    pas, ni devant le client, ni devant un juge.

    ⚠️ LE SCEAU D'ABORD, ICI AUSSI

    Le même contrôle que l'acceptation, dans le même ordre : sans lien valable, un
    numéro existant et un numéro inventé reçoivent la même réponse. Lire ne consomme
    rien : le client peut rouvrir sa proforma autant de fois qu'il veut avant de
    l'accepter.

    ⚠️ UNE PROFORMA REMPLACÉE SE LIT, ET LE DIT

    Une nouvelle version prend un **nouveau numéro** et marque l'ancien document
    `REMPLACEE`. Le lien de l'ancienne lit donc l'ancienne, dont l'état le dit : l'écran
    du client ne propose pas de l'accepter, et le domaine le refuserait. Un contrôle de
    version écrit ici au premier jet ne pouvait jamais se déclencher, et une mutation
    l'a montré en survivant ; il a été retiré plutôt que gardé pour la forme.
    ─────────────────────────────────────────────────────────────────────────────
    """
    lien = _lien_presente(numero, version, expire_le, sceau)
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        proforma = _proformas().lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    return ProformaConsultee(
        numero=proforma.numero,
        version=proforma.version,
        service=proforma.service,
        etat=proforma.etat.value,
        montant=proforma.tarif.montant,
        lignes=[
            {"libelle": ligne.libelle, "montant": str(ligne.montant)} for ligne in proforma.lignes
        ],
        debours=[
            {"libelle": d.libelle, "montant": str(d.montant)} for d in proforma.debours
        ],
        expire_le=expire_le,
        acceptee=proforma.etat.value == "ACCEPTEE",
    )


def _le_document(proforma: Proforma) -> Response:
    """Sert le PDF d'une proforma, après avoir vérifié qu'il est intact.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **L'INTÉGRITÉ EST VÉRIFIÉE À CHAQUE LECTURE**, comme le domaine l'exige
    (`Proforma.document_intact`). Ce n'est pas une précaution de principe : le
    document est ce sur quoi la cliente s'engage, et servir un fichier altéré
    sans le dire vaudrait mieux ne rien servir du tout.

    Une altération rend `409` et non `500` : le serveur fonctionne, c'est le
    document qui ne correspond plus. Un `500` enverrait chercher une panne
    d'infrastructure là où il y a un fichier à remplacer.

    ⚠️ **UNE PROFORMA D'AVANT LE PAS 148 N'A PAS DE DOCUMENT.** Son empreinte
    porte un résumé textuel, jamais rangé au magasin. Elle rend donc `404`, et
    c'est la vérité : il n'y a pas de PDF à montrer. Le détail chiffré reste
    lisible par `GET /proformas/{numero}/consultation`.
    ─────────────────────────────────────────────────────────────────────────────
    """
    magasin = magasin_des_proformas()
    try:
        contenu = magasin.lire(proforma.empreinte_document)
    except (CleInvalide, FichierAbsent) as absent:
        raise HTTPException(
            status_code=404,
            detail=(
                f"aucun document pour {proforma.numero}. Les proformas émises avant "
                "la mise en service du document PDF n'en ont pas ; leur détail se lit "
                "par la consultation."
            ),
        ) from absent

    if not proforma.document_intact(contenu):
        raise HTTPException(
            status_code=409,
            detail=(
                f"le document de {proforma.numero} ne correspond plus à son empreinte. "
                "Il a été altéré depuis l'émission et ne peut pas être servi."
            ),
        )

    return Response(
        content=contenu,
        media_type="application/pdf",
        headers={
            # `inline` et non `attachment` : la cliente doit **lire** sa proposition
            # avant d'accepter, et un téléchargement forcé mettrait un fichier entre
            # elle et sa décision. Le risque d'exécution qui justifie `attachment`
            # sur un justificatif déposé n'existe pas ici : ce PDF, c'est nous qui
            # l'avons produit.
            "Content-Disposition": (
                f'inline; filename="proforma-{proforma.numero}.pdf"'
            ),
            "X-Content-Type-Options": "nosniff",
            # Un engagement contractuel nominatif n'a rien à faire dans un cache
            # partagé.
            "Cache-Control": "private, no-store",
        },
    )


@routeur.get(
    "/proformas/{numero}/document",
    summary="Le client lit le PDF de sa proforma par son lien signé",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Le document"},
        404: {"description": "Proforma inconnue, ou sans document"},
        409: {"description": "Le document ne correspond plus à son empreinte"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def lire_le_document_de_la_proforma(
    numero: str,
    version: int = Query(ge=1),
    expire_le: datetime = Query(),
    sceau: str = Query(min_length=16),
) -> Response:
    """⚠️ **Publique et scellée**, exactement comme la consultation.

    Le même contrôle, dans le même ordre, et pour la même raison : sans lien
    valable, un numéro existant et un numéro inventé reçoivent la même réponse.
    Lire ne consomme rien ; la cliente rouvre sa proforma autant qu'elle veut.
    """
    lien = _lien_presente(numero, version, expire_le, sceau)
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        return _le_document(_proformas().lire(numero))
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


@routeur.get(
    "/proformas/{numero}/document-interne",
    summary="Le cabinet relit le PDF d'une proforma qu'il a émise",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Le document"},
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue, ou sans document"},
        409: {"description": "Le document ne correspond plus à son empreinte"},
    },
)
def relire_le_document_de_la_proforma(acces: AccesRequis, numero: str) -> Response:
    """Le même document, sous session, sans sceau.

    ⚠️ **Une route distincte, et non la précédente sans sceau.** Le lien du
    client expire, et c'est voulu ; le cabinet, lui, doit pouvoir ressortir la
    proforma de l'an dernier le jour d'un litige. Assouplir l'expiration de la
    route publique pour couvrir ce besoin aurait allongé la vie du lien qui
    circule par courriel, ce qui est exactement l'inverse de ce qu'on veut.
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    try:
        return _le_document(_proformas().lire(numero))
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


@routeur.post(
    "/proformas/{numero}/acceptation",
    summary="Le client accepte sa proforma par son lien signé",
    responses={
        404: {"description": "Proforma inconnue"},
        409: {"description": "Cette proforma ne peut plus être acceptée"},
        422: {"description": "Lien invalide, expiré, ou d'une autre version"},
    },
)
def accepter_la_proforma(
    numero: str, corps: DemandeDAcceptation, requete: Request
) -> Acceptee:
    """⚠️ **Publique, et elle doit l'être.**

    ─────────────────────────────────────────────────────────────────────────────
    Un client n'a pas de compte sur la plateforme, et lui en imposer un pour
    accepter un devis ferait perdre la moitié des acceptations. Le lien signé
    **est** l'authentification : scellé sur le numéro, la version et l'échéance,
    à usage unique.

    ⚠️ Le sceau est vérifié **avant** la proforma. Une vérification qui lirait
    d'abord le document permettrait de sonder l'existence d'un numéro sans posséder
    de lien, ce qui est précisément ce qu'on refuse ailleurs par des `404`.

    L'ORIGINE EST CONSERVÉE, ET ELLE NE SERT PAS AU CONTRÔLE D'ACCÈS

    Elle est notée pour le litige : « d'où est venue l'acceptation ». S'en servir
    pour autoriser reviendrait à faire d'une adresse réseau une identité, ce qu'elle
    n'est pas.
    ─────────────────────────────────────────────────────────────────────────────
    """
    lien = _lien_presente(numero, corps.version, corps.expire_le, corps.sceau)
    # ⚠️ **Le sceau d'abord, réellement** (pas 67). Ce docstring l'affirmait, et le code
    # lisait la proforma avant : un numéro inexistant répondait 404, un numéro existant
    # au sceau faux répondait 422. Les numéros étant séquentiels, n'importe qui
    # énumérait les proformas du cabinet sans posséder un seul lien. Le sceau porte sur
    # le numéro, la version et l'échéance : il se vérifie sans rien lire.
    try:
        lien.exiger_valide(maintenant(), secret=_secret_des_liens())
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus

    proformas = _proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        # ⚠️ Trois valeurs, et le lien en fait partie : il est rendu **employé**,
        # à usage unique. Le conserver permettrait de le rejouer, et une acceptation
        # rejouée est une seconde signature sur le même engagement.
        acceptee, _lien_employe, acceptation = accepter(
            proforma,
            lien,
            identite_declaree=corps.identite_declaree,
            a_l_instant=maintenant(),
            secret=_secret_des_liens(),
            origine=requete.client.host if requete.client else "",
        )
    except LienInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except ValueError as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus

    proformas.enregistrer(acceptee)
    # ⚠️ **Le dossier suit.** L'acceptation d'un client est le geste qui prouve
    # l'engagement : le laisser à `PROFORMA_ÉMISE` rendrait l'encaissement
    # impossible, et le dossier resterait éternellement en attente d'une réponse
    # qui est déjà arrivée.
    #
    # Une transition impossible est ignorée plutôt que levée : le client a bien
    # accepté, et lui rendre une erreur pour une raison d'état interne ferait
    # perdre une signature contractuelle.
    dossiers = _dossiers()
    try:
        dossier = dossiers.lire(acceptee.dossier)
        if dossier.etat is EtatDossier.PROFORMA_EMISE:
            dossiers.enregistrer(dossier.accepter(maintenant()))
    except (DossierIntrouvable, TransitionDossierRefusee):
        pass

    _accuser_l_acceptation(acceptee, acceptation)
    return Acceptee(
        numero=acceptation.proforma,
        version=acceptation.version,
        montant=acceptation.montant,
        acceptee_le=acceptation.acceptee_le,
        identite_declaree=acceptation.identite_declaree,
    )


def _accuser_l_acceptation(proforma, acceptation) -> None:
    """La trace écrite de ce que la cliente vient d'engager.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ ELLE ENGAGEAIT SON ENTREPRISE ET NE RECEVAIT RIEN.

    La page le dit en toutes lettres — « mon accord engage l'entreprise que je
    représente » — et le domaine le confirme : une proforma acceptée vaut
    contrat. Vérifié sur la pile le 29 septembre, en acceptant réellement : la
    boîte de la cliente est **inchangée** après le clic. Ni le montant accepté,
    ni la date, ni le nom déclaré. Le cabinet, lui, a tout.

    ⚠️ **IL NE LÈVE JAMAIS.** Le client a accepté ; l'accord est enregistré, le
    dossier a suivi, et rendre une erreur parce qu'un serveur de messagerie
    n'a pas répondu ferait **perdre une signature contractuelle** — c'est le
    raisonnement déjà tenu quinze lignes plus haut pour la transition du dossier.

    ⚠️ **SANS ADRESSE, RIEN**, et sans bruit : l'adresse est facultative au
    formulaire public, et le rappel se fait alors au téléphone.
    ─────────────────────────────────────────────────────────────────────────────
    """
    try:
        demande = _dossiers().lire(proforma.dossier).demande
        courriel = (demande.courriel or "").strip()
        if not courriel:
            return
        service_de_notification().envoyer(
            "proforma.acceptation",
            destinataire=courriel,
            contexte={
                "prenom": demande.nom,
                "numero": acceptation.proforma,
                "montant": f"{acceptation.montant:,.0f}".replace(",", "\u202f"),
                "acceptee_le": acceptation.acceptee_le.strftime("%d/%m/%Y à %Hh%M"),
                "identite": acceptation.identite_declaree,
            },
        )
    except Exception:  # noqa: BLE001 — voir l'en-tête : jamais au prix de l'accord
        _journal_acceptation.exception(
            "Accusé d'acceptation NON envoyé pour %s. L'accord EST enregistré ; "
            "seule la trace écrite manque au client.",
            acceptation.proforma,
        )


def _proformas():
    """Le dépôt des proformas, durable si la base est là."""
    session = session_de_travail()
    if session is None:
        return _proformas_memoire()
    return DepotProformasSql(session, courant())


#: ⚠️ **L'unique magasin, importé et non redéclaré.** Voir `magasins_memoire` :
#: la route écrivait ici, le balayage de relance lisait ailleurs, et il ne
#: trouvait jamais rien à relancer.
_proformas_memoire = proformas_memoire


def _secret_des_liens() -> str:
    """Le secret qui scelle les liens d'acceptation.

    ⚠️ **La clé de chiffrement du cabinet, et non un secret dédié.** Un second
    secret serait un second réglage à poser, à conserver et à faire tourner, pour
    une garantie identique : les deux protègent des engagements du même cabinet.

    En production, `Configuration` refuse de démarrer sans clé. Hors production, le
    repli est explicite et **le sceau n'y protège rien** : c'est assumé, et le
    registre des services signale déjà l'absence de clé.
    """
    return configuration().cle_chiffrement or "secret-de-developpement"


def _avancer_le_dossier(dossier, qualification, questionnaire, instant) -> None:
    """Fait suivre à l'état du dossier ce que le geste vient de prouver.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **UNE MACHINE À ÉTATS QU'AUCUNE ROUTE NE PEUT CONDUIRE EST DÉCORATIVE.**

    Le dossier a huit états et des transitions vérifiées depuis le pas 2. Aucune
    route ne les faisait avancer : un dossier affecté restait `AFFECTÉE` quoi qu'on
    fasse, et l'émission d'une proforma — qui exige `CHIFFRÉE` — était donc
    **impossible par l'API**. La recette de bout en bout l'a montré au premier essai.

    LA RÈGLE : L'ÉTAT SUIT CE QUE LE GESTE PROUVE, PAS CE QU'IL ESPÈRE

    Enregistrer une réponse **déclarée** par le prospect prouve qu'on lui a parlé :
    le dossier passe en conversation. C'est le sens de cet état, et rien d'autre ne
    le déclenche aujourd'hui.

    ⚠️ **Une qualification incomplète ne rend pas le dossier `QUALIFIÉE`.** C'est la
    garde qui compte : avancer sur la première réponse ferait franchir l'étape à un
    dossier dont il manque neuf questions sur onze, et le chiffrage porterait sur
    des faits absents. L'état suit l'**achèvement**, pas l'activité.

    RIEN N'EST FORCÉ, ET UNE TRANSITION IMPOSSIBLE EST SILENCIEUSE ICI

    Un dossier déjà chiffré qu'on requalifie ne recule pas : les transitions
    interdites sont ignorées plutôt que levées. La qualification a bien été
    enregistrée, et refuser la requête pour une raison d'état ferait perdre le
    travail du responsable au moment où il corrige une réponse.
    ─────────────────────────────────────────────────────────────────────────────
    """
    depot = _dossiers()
    courant_ = dossier
    if courant_.etat is EtatDossier.AFFECTEE:
        courant_ = courant_.premier_contact(instant)
    if (
        courant_.etat is EtatDossier.EN_CONVERSATION
        and qualification.complete(questionnaire)
    ):
        courant_ = courant_.qualifier(instant)
    if courant_ is not dossier:
        depot.enregistrer(courant_)


# ── Ce que l'ouverture a RÉELLEMENT fait ─────────────────────────────────────


class OuvertureDeLEspace(BaseModel):
    """L'état réel de l'espace ouvert au client, après encaissement.

    ⚠️ **Cette route existe parce que le produit disait le contraire de ce qu'il
    savait.**

    La saga d'ouverture franchit sept étapes, dont `ADMINISTRATEUR_CREE` — « le
    compte administrateur existe, avec son jeton d'activation à usage unique ».
    Quand l'infrastructure manque, une étape est **substituée** : elle est
    traversée sans rien faire, et le domaine l'inscrit noir sur blanc. Le code du
    contexte tenants l'écrit lui-même :

        « Une saga peut se terminer avec des étapes substituées : le tenant
        existe, son sous-domaine répond, et il n'a ni schéma, ni stockage, ni
        compte administrateur. **Annoncer au client un espace ouvert dans ce cas
        serait un mensonge que sa première connexion découvrirait.** »

    L'information était calculée, stockée, et **exposée nulle part**. La console
    annonçait « l'espace s'ouvre » dans tous les cas, et personne ne pouvait
    savoir sans interroger la base que le client n'avait ni compte ni lien.

    ⚠️ **`utilisable` n'est pas `terminee`.** C'est toute la distinction : une
    ouverture terminée avec trois étapes substituées est terminée, et l'espace
    n'est pas utilisable.
    """

    dossier: str
    #: `False` tant que l'encaissement n'a pas eu lieu, ou que le relais n'a pas
    #: encore publié l'événement : la saga est asynchrone, et son absence à cet
    #: instant n'est pas un échec.
    demarree: bool
    terminee: bool
    #: ⚠️ Vrai seulement si AUCUNE étape n'a été substituée. C'est ce qu'il faut
    #: lire avant d'annoncer quoi que ce soit au client.
    utilisable: bool
    #: Les étapes qui empêchent RÉELLEMENT le client d'entrer, nommées. Vide
    #: quand tout est en place. ⚠️ Les étapes sans objet — voir
    #: `ETAPES_SANS_OBJET` — n'y figurent pas : les citer ferait croire à un
    #: défaut là où la conception a changé.
    etapes_substituees: list[str]
    slug: str | None
    #: Le motif du dernier échec, quand la saga en a eu un.
    dernier_echec: str | None


@routeur.get(
    "/dossiers/{reference}/ouverture",
    summary="Ce que l'ouverture de l'espace a réellement fait",
    responses={403: {"description": "Habilitation insuffisante"}},
)
def lire_l_ouverture(acces: AccesRequis, reference: str) -> OuvertureDeLEspace:
    """L'état réel de l'espace, pour que l'écran cesse de l'annoncer à l'aveugle.

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **AUCUNE ERREUR SI LA SAGA N'A PAS ENCORE TOURNÉ.**

    L'ouverture est déclenchée par un événement publié par le relais, dont la
    cadence est de cinq secondes. Entre la confirmation de l'encaissement et le
    premier tour du relais, la saga n'existe pas — et ce n'est pas un défaut.
    Rendre 404 ferait afficher une erreur au collaborateur qui vient de réussir
    son geste ; on rend donc `demarree: false`, que l'écran sait dire.

    ⚠️ **LA CLÉ DE LA SAGA EST LA RÉFÉRENCE DU DOSSIER**, parce que c'est elle
    que l'encaissement pose comme clé de l'événement. La chercher par le numéro
    de proforma obligerait à parcourir les contextes de toutes les exécutions.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.LIRE_PROSPECT)

    session = session_de_travail()
    if session is None:
        # Hors base — les essais en mémoire — il n'y a pas d'exécution à lire.
        return OuvertureDeLEspace(
            dossier=reference,
            demarree=False,
            terminee=False,
            utilisable=False,
            etapes_substituees=[],
            slug=None,
            dernier_echec=None,
        )

    execution = DepotExecutionsSql(session, courant()).trouver(NOM_SAGA, reference)
    if execution is None:
        return OuvertureDeLEspace(
            dossier=reference,
            demarree=False,
            terminee=False,
            utilisable=False,
            etapes_substituees=[],
            slug=None,
            dernier_echec=None,
        )

    contexte = execution.contexte
    # ⚠️ Les manques RÉELS, et non toutes les étapes substituées : `SCHEMA_CREE`
    # l'est sur toutes les installations depuis que la décision D3 a été
    # révisée, et la citer ferait croire à un défaut au chargé de clientèle.
    substituees_ = list(manques_reels(contexte))
    terminee = execution.etat is EtatSaga.TERMINEE
    return OuvertureDeLEspace(
        dossier=reference,
        demarree=True,
        terminee=terminee,
        # ⚠️ `ouverture_reellement_complete` est la fonction du DOMAINE : la
        # recopier ici en ferait une seconde définition, qui divergerait.
        utilisable=terminee and ouverture_reellement_complete(contexte),
        etapes_substituees=substituees_,
        slug=str(contexte.get("slug") or "") or None,
        dernier_echec=execution.dernier_echec,
    )


# ── L'encaissement, dernier maillon du parcours ──────────────────────────────


class DemandeDeReglement(BaseModel):
    """Ce qu'un collaborateur décide avant de solliciter le téléphone du client.

    ⚠️ **Le slug est choisi ici, et c'est ce qui rend l'encaissement automatisable.**
    C'est une adresse publique, que le client lira, dictera au téléphone et verra
    sur ses documents ; elle est choisie par un humain, jamais dérivée du nom.

    Tant que l'encaissement était saisi à la main, ce choix pouvait attendre la
    saisie. Dès lors que l'opérateur notifie le règlement tout seul, plus personne
    n'est là pour choisir : le slug est donc retenu **avant** que le débit ne soit
    demandé.
    """

    slug: str = Field(min_length=3, max_length=40)
    #: Le numéro à débiter. Vide, on emploie celui de la demande d'origine, qui
    #: est celui que le client a lui-même donné.
    telephone: str = ""


class ReglementDemande(BaseModel):
    dossier: str
    proforma: str
    slug: str
    #: Le numéro réellement sollicité, sous sa forme canonique.
    telephone: str
    montant: Decimal
    paiement: str
    #: Ce que le prestataire a répondu à l'initiation. ⚠️ Ce n'est **pas** une
    #: preuve de paiement : le client n'a pas encore saisi son code.
    accepte: bool
    message: str


@routeur.post(
    "/proformas/{numero}/reglement",
    summary="Demander au client de régler sa proforma",
    status_code=202,
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
        409: {"description": "Le dossier n'est pas au stade de l'acceptation"},
        422: {"description": "Sous-domaine inattribuable, ou numéro inexploitable"},
    },
)
def demander_le_reglement(
    acces: AccesRequis, numero: str, corps: DemandeDeReglement
) -> ReglementDemande:
    """Pousse la demande de débit sur le téléphone du client.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUE CETTE ROUTE FAIT, ET CE QU'ELLE NE FAIT PAS

    Elle retient l'adresse du futur espace, inscrit un paiement **de nature
    proforma**, et demande le débit au prestataire. Elle **n'encaisse rien** : le
    client n'a pas encore saisi son code, et l'opérateur pousse un menu sur son
    téléphone.

    La réponse est donc **202**, et `accepte` ne dit que « la demande est partie ».

    ⚠️ **LA CLÉ D'IDEMPOTENCE EST TIRÉE ICI, ET C'EST LA PIÈCE MAÎTRESSE.**

    C'est un UUID, le nôtre, transmis au prestataire comme identifiant de produit.
    Il n'est jamais publié, ni imprimé sur la proforma, ni envoyé au client. C'est
    lui, et lui seul, qui permettra de rattacher la notification entrante au bon
    règlement.

    POURQUOI L'ENCAISSEMENT PEUT DÉSORMAIS ÊTRE NOTIFIÉ

    La route d'encaissement voisine portait cet argument, et il était juste :
    *« déclencher sur une notification non signée reviendrait à laisser un inconnu
    provisionner de l'infrastructure en devinant un numéro de proforma »*.

    Le risque nommé était réel parce que rien n'était initié de notre côté : une
    notification arrivait sur un numéro de proforma, qui est imprimé sur un
    document et que le client cite au téléphone.

    ⚠️ **Il disparaît dès lors que le paiement est initié ici.** Une notification
    ne produit un effet que si elle se rapproche d'un paiement **que nous avons
    créé**, sur une clé que nous avons tirée, pour un montant que nous avons fixé
    — et le montant est vérifié, un écart faisant rejeter l'encaissement. Deviner
    un numéro de proforma ne suffit plus à rien.

    Le prestataire ne signant toujours pas ses appels, la vérification de
    l'identifiant marchand et le rapprochement restent ce qui fait foi, comme pour
    les abonnements depuis le premier jour.

    ⚠️ **LA CONFIRMATION MANUELLE RESTE**, et ce n'est pas une transition : un
    client qui règle en espèces au guichet ou par virement n'emprunte aucun
    téléphone. Les deux chemins aboutissent au même geste de domaine, avec le même
    identifiant d'événement, donc à une seule saga.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.GERER_COMPTES)

    try:
        valider_le_slug(corps.slug)
    except SlugInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except NomsReservesIndisponibles as indisponible:
        # ⚠️ 503 et non 422 : le client n'a rien fait de mal, et son slug n'est
        # peut-être pas réservé. C'est la plateforme qui ne sait pas répondre, et un
        # slug attribué ne se reprend pas. Voir `valider_le_slug`.
        raise HTTPException(status_code=503, detail=str(indisponible)) from indisponible

    proformas = _proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    depot = _dossiers()
    try:
        dossier = depot.lire(proforma.dossier)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    # ⚠️ L'état est vérifié **ici**, avant de solliciter le téléphone du client.
    # Demander un débit sur un dossier qui n'a pas accepté ferait sonner le
    # téléphone de quelqu'un qui ne s'est engagé à rien.
    if dossier.etat is not EtatDossier.ACCEPTEE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"dossier {dossier.reference} à l'état {dossier.etat} : le règlement "
                "se demande après l'acceptation de la proforma, et pas avant."
            ),
        )

    try:
        retenu = dossier.retenir_le_slug(corps.slug)
    except TransitionDossierRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    try:
        telephone = normaliser_telephone(
            corps.telephone or dossier.demande.telephone
        )
    except NumeroInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus

    # ⚠️ Importé à l'appel : `routes_http` importe déjà ce module pour la suite
    # du parcours, et l'importer en tête refermerait le cycle.
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    boutique = comptoir()
    paiement = Paiement(
        identifiant=f"pay-{uuid.uuid4().hex[:16]}",
        nature=NaturePaiement.PROFORMA,
        reference_reglee=proforma.numero,
        cle_idempotence=nouvelle_cle_idempotence(),
        montant=proforma.tarif.montant,
        telephone=telephone,
        initie_le=maintenant(),
    )

    # ⚠️ **On écrit avant d'appeler**, comme l'engagement d'une souscription. Si
    # l'appel part et que le processus tombe avant d'avoir enregistré, le client
    # est débité pour un paiement dont nous n'avons aucune trace : la notification
    # ne se rapprocherait de rien et finirait en `paiement.non_affecte`.
    depot.enregistrer(retenu)
    boutique.paiements.enregistrer(paiement)

    initiation = boutique.fournisseur.initier(
        montant=paiement.montant,
        telephone=paiement.telephone,
        cle_idempotence=paiement.cle_idempotence,
        libelle=f"{proforma.service} — {proforma.numero}",
    )
    if initiation.reference_externe:
        boutique.paiements.enregistrer(
            paiement.avec_reference(initiation.reference_externe)
        )

    return ReglementDemande(
        dossier=dossier.reference,
        proforma=proforma.numero,
        slug=corps.slug,
        telephone=paiement.telephone,
        montant=paiement.montant,
        paiement=paiement.identifiant,
        accepte=initiation.accepte,
        message=initiation.message,
    )


class DemandeDEncaissement(BaseModel):
    """Ce qu'un collaborateur confirme après rapprochement.

    ⚠️ **Le slug est choisi par un humain, jamais dérivé du nom.** C'est une adresse
    publique, que le client lira, dictera au téléphone et verra sur ses documents.
    Le dériver de « Station Bonabéri & Fils SARL » produirait quelque chose
    d'illisible, et le changer plus tard casserait les liens déjà distribués.
    """

    slug: str = Field(min_length=3, max_length=40)
    #: La référence du prestataire de paiement. ⚠️ Conservée pour le rapprochement
    #: comptable, jamais employée comme preuve : c'est le rapprochement qui fait foi.
    reference_externe: str = Field(default="", max_length=120)


class EncaissementConfirme(BaseModel):
    dossier: str
    etat: str
    tenant: str | None = None
    slug: str | None = None
    #: `True` quand l'encaissement était un rejeu : le dossier était déjà payé, et
    #: aucun second événement n'a été déposé.
    rejeu: bool = False


@routeur.post(
    "/proformas/{numero}/encaissement",
    summary="Confirmer l'encaissement d'une proforma acceptée",
    responses={
        403: {"description": "Habilitation insuffisante"},
        404: {"description": "Proforma inconnue"},
        409: {"description": "Le dossier n'est pas au stade de l'acceptation"},
        422: {"description": "Sous-domaine inattribuable"},
    },
)
def encaisser(
    acces: AccesRequis, numero: str, corps: DemandeDEncaissement
) -> EncaissementConfirme:
    """Le geste qui ouvre l'espace du client. **Confirmé par un humain.**

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **CE N'ÉTAIT PAS UN CROCHET DE PRESTATAIRE, ET LA RAISON A CHANGÉ.**

    Cette route portait l'argument suivant, et il était juste : le fournisseur **ne
    signe pas** ses notifications, or ce geste ouvre un tenant, donc *« le
    déclencher sur une notification non signée reviendrait à laisser un inconnu
    provisionner de l'infrastructure en devinant un numéro de proforma »*.

    Le risque nommé était réel **parce que rien n'était initié de notre côté** :
    une notification serait arrivée sur un numéro de proforma, qui est imprimé sur
    un document et que le client cite au téléphone.

    Il a disparu avec `POST /proformas/{numero}/reglement`, qui crée le paiement
    avant de solliciter le téléphone du client. Une notification ne produit plus
    d'effet que si elle se rapproche d'un paiement **que nous avons créé**, sur une
    clé d'idempotence que nous avons tirée et jamais publiée, pour un montant que
    nous avons fixé et qui est vérifié. Deviner un numéro de proforma ne suffit
    plus à rien.

    ⚠️ **CETTE ROUTE RESTE, ET CE N'EST PAS UNE TRANSITION.** Un client qui règle
    en espèces au guichet ou par virement n'emprunte aucun téléphone. Les deux
    chemins aboutissent au même geste de domaine, avec le **même identifiant
    d'événement**, donc à une seule saga : confirmer à la main un règlement déjà
    notifié n'ouvre pas un second tenant.

    ⚠️ **REJOUABLE SANS DOMMAGE.** Un dossier déjà payé ne dépose pas un second
    événement. Deux collaborateurs qui confirment le même encaissement à une minute
    d'intervalle n'ouvrent pas deux tenants, et la réponse le dit par `rejeu`.

    LE SOUS-DOMAINE EST VALIDÉ ICI, PAS À L'OUVERTURE

    La saga le réserve, mais elle tourne **plus tard**, dans un tour d'ordonnanceur,
    et son refus n'arriverait à personne : l'événement partirait en quarantaine et
    le client attendrait. Refuser tout de suite met l'erreur devant les yeux de
    celui qui peut la corriger.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.GERER_COMPTES)
    proformas = _proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        valider_le_slug(corps.slug)
    except SlugInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except NomsReservesIndisponibles as indisponible:
        # ⚠️ 503 et non 422 : le client n'a rien fait de mal, et son slug n'est
        # peut-être pas réservé. C'est la plateforme qui ne sait pas répondre, et un
        # slug attribué ne se reprend pas. Voir `valider_le_slug`.
        raise HTTPException(status_code=503, detail=str(indisponible)) from indisponible

    depot = _dossiers()
    try:
        dossier = depot.lire(proforma.dossier)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        resultat = encaisser_l_acceptation(
            dossier,
            proforma,
            boite=_boite_du_parcours(),
            slug=corps.slug,
            tenant=f"tnt-{corps.slug}",
            a_l_instant=maintenant(),
            # ⚠️ L'identifiant dérive du **dossier**, non d'un tirage : le relais
            # garantit « au moins une fois », et deux confirmations du même
            # encaissement doivent porter le même événement.
            identifiant_evenement=f"enc-{dossier.reference}",
            reference_externe=corps.reference_externe,
        )
    except EncaissementRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except TransitionDossierRefusee as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec

    if not resultat.rejeu:
        depot.enregistrer(resultat.dossier)

    return EncaissementConfirme(
        dossier=resultat.dossier.reference,
        etat=resultat.dossier.etat.value,
        tenant=None if resultat.rejeu else f"tnt-{corps.slug}",
        slug=None if resultat.rejeu else corps.slug,
        rejeu=resultat.rejeu,
    )


def _boite_du_parcours():
    """La boîte d'envoi, par la **surface publique** du Transverse.

    ⚠️ La première rédaction importait l'atelier d'orchestration directement, et le
    test d'architecture l'a refusée : on n'entre chez un autre contexte que par sa
    surface déclarée. Il avait raison, et le remède a été d'exposer la boîte plutôt
    que de la reconstruire — deux fabriques seraient deux boîtes en mémoire, l'une
    déposerait et l'autre publierait.
    """
    from app.contextes.transverse.api import boite_d_envoi

    return boite_d_envoi()


@routeur.get(
    "/motifs-de-classement",
    summary="Le vocabulaire des motifs de classement sans suite",
)
def lister_les_motifs_de_classement(acces: AccesRequis) -> list[MotifDeClassement]:
    """Dans l'ordre du référentiel, qui est celui de la liste déroulante.

    ⚠️ Cette route existe pour que l'écran n'ait pas à recopier la liste. Une
    liste recopiée côté client diverge au premier motif ajouté, et le formulaire
    proposerait alors des codes que le serveur refuse.
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    return list(_motifs_de_classement())


class DemandeDeClassement(BaseModel):
    """Pourquoi ce dossier quitte le parcours."""

    #: Un code du vocabulaire, jamais du texte libre. Voir
    #: `motifs-de-classement.yaml` pour la raison.
    motif: str = Field(min_length=1)
    #: Le complément, obligatoire pour les motifs qui ne disent rien seuls.
    precision: str = ""


@routeur.post(
    "/dossiers/{reference}/sans-suite",
    summary="Classer un dossier sans suite",
    responses={
        404: {"description": "Dossier inconnu"},
        409: {"description": "Un dossier accepté ou payé ne se classe pas"},
        422: {"description": "Motif hors vocabulaire, ou précision manquante"},
    },
)
def classer_sans_suite(
    acces: AccesRequis, reference: str, demande: DemandeDeClassement
) -> DossierResume:
    """Le dossier quitte le parcours. Il n'est pas supprimé.

    ─────────────────────────────────────────────────────────────────────────────
    POURQUOI `QUALIFIER_PROSPECT` ET NON `AFFECTER_DOSSIER`

    C'est celui qui a parlé au prospect qui sait pourquoi il s'arrête. Exiger un
    responsable de pôle pour chaque prospect perdu produirait exactement ce que
    cette route existe pour corriger : personne ne classerait, et les dossiers
    morts continueraient de peser sur la charge de quelqu'un.

    Le rôle `ADMINISTRATEUR` porte `LIRE_PROSPECT` sans `QUALIFIER_PROSPECT`, sous
    ce motif déjà écrit : « remplir un questionnaire suppose d'avoir parlé au
    client ». Classer sans suite le suppose autant.

    ⚠️ **Le geste est tracé, et c'est ce qui le rend sûr.** Le motif et sa
    précision sont écrits au dossier, avec la date. Un collaborateur qui classerait
    ses dossiers pour alléger sa charge laisserait une liste de motifs que la
    direction lit.

    CE QUE LA MACHINE NE FAIT PAS À LA PLACE DE L'HUMAIN

    Rien ici n'est automatique. La veille signale, elle ne classe pas : *l'état
    suit ce que le geste prouve, pas ce qu'il espère.* Un prospect qui ne répond
    pas n'a rien refusé, et le silence ne prouve que l'absence d'échange.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.QUALIFIER_PROSPECT)

    motifs = {m.code: m for m in _motifs_de_classement()}
    choisi = motifs.get(demande.motif)
    if choisi is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"motif « {demande.motif} » inconnu. Les motifs du référentiel sont : "
                f"{', '.join(motifs)}."
            ),
        )
    if choisi.precision_requise and not demande.precision.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                f"le motif « {choisi.code} » exige une précision. Sans elle, "
                "il ne dit rien de ce qu'on pourra en faire."
            ),
        )

    depot = _dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    # ⚠️ Le motif écrit au dossier porte le code **et** la précision, séparés,
    # dans un seul champ texte que le domaine porte déjà. Promouvoir le code en
    # colonne serait la bonne façon de le compter, et ce sera une migration le
    # jour où le centre voudra ce compte : aujourd'hui, aucun écran ne le
    # demande, et une colonne qu'on ne requête pas est une colonne qui diverge.
    trace = choisi.code
    if demande.precision.strip():
        trace = f"{choisi.code} : {demande.precision.strip()}"

    try:
        classe = dossier.classer_sans_suite(maintenant(), motif=trace)
    except TransitionDossierRefusee as echec:
        raise HTTPException(status_code=409, detail=str(echec)) from echec
    depot.enregistrer(classe)
    return _resume(classe)


# ── La recherche globale (pas 93) ────────────────────────────────────────────


@routeur.get(
    "/recherche",
    summary="Chercher une demande ou un prospect en cours",
    responses={422: {"description": "Requête trop courte pour les réglages de recherche"}},
)
def chercher_un_dossier_commercial(
    acces: AccesRequis,
    q: str = Query(min_length=1, max_length=100, description="Référence, nom, téléphone…"),
) -> ReponseDeRecherche:
    """Par référence, téléphone ou courriel (identifiants), par nom (libellé), parmi les
    dossiers **ouverts** : ceux que la console des demandes affiche.

    ⚠️ Le téléphone est comparé **normalisé** quand la requête en est un : un prospect
    rappelle depuis « 699 11 22 33 », et le dossier porte « +237699112233 ».
    """
    exiger(acces, Permission.LIRE_PROSPECT)
    reglages = charger_les_reglages_de_recherche(configuration().dossier_referentiel)
    try:
        verifier_la_requete(q, reglages)
    except RequeteTropCourte as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    try:
        requete = normaliser_telephone(q)
    except NumeroInvalide:
        requete = q
    resultats = []
    for dossier in _dossiers().ouverts():
        demande = dossier.demande
        score = pertinence(
            requete,
            identifiants=(dossier.reference, demande.telephone, demande.courriel),
            libelles=(demande.nom,),
            longueur_minimale=reglages.longueur_minimale,
        )
        if score is None:
            continue
        resultats.append(
            ResultatDeRecherche(
                nature="Demande",
                identifiant=dossier.reference,
                titre=demande.nom,
                detail=f"{dossier.service} · {dossier.etat.value}",
                lien=f"/acquisition/{dossier.reference}",
                pertinence=score,
            )
        )
    return classer_les_resultats("souscription", resultats, reglages)
