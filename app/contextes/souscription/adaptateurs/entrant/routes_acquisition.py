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

⚠️ 16 octobre 2026 : la proforma (émission, transmission, acceptation, document) est
sortie dans `routes_proforma.py`, sous les mêmes chemins ; ce fichier dépassait 4 000 lignes.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
)

from app.contextes.souscription.adaptateurs.entrant.sources import (
    boite_du_parcours,
    depot_dossiers,
    depot_proformas,
    depot_qualifications,
    qualifications_memoire,
    referentiel_d_acquisition,
)
from app.contextes.souscription.adaptateurs.sortant.annuaire_des_candidats import (
    monter_les_candidatures,
)
from app.contextes.souscription.adaptateurs.sortant.delais_de_veille import (
    charger_les_delais_de_veille,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    DossierIntrouvable,
    ProformaIntrouvable,
    RappelIntrouvable,
)
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotRappelsSql,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
    vider_les_dossiers_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.motifs_de_classement import (
    MotifDeClassement,
    charger_les_motifs_de_classement,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    QuestionnaireIntrouvable,
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
    PieceJointe,
    PieceRefusee,
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
    preremplir,
    reaffecter_le_dossier,
)
from app.contextes.souscription.application.encaissement_du_parcours import (
    EncaissementRefuse,
    encaisser_l_acceptation,
    ouvre_un_locataire,
)
from app.contextes.souscription.domaine.proforma import (
    EtatProforma,
    Proforma,
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
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.infrastructure.depots_orchestration import DepotExecutionsSql
from app.orchestration.saga import EtatSaga
from app.partage.en_tetes import disposition_en_piece_jointe
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import MagasinFichiersLocal, detecter_le_type
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
from app.partage.sceau import SceauInvalide, dechiffrer_la_reference, jeton_opaque
from app.partage.telephone import NumeroInvalide, normaliser_telephone

routeur = APIRouter(prefix="/acquisition", tags=["Acquisition"])

#: ⚠️ Un journal nommé, pour l'accusé d'acceptation : il ne lève jamais, et
#: son seul canal de signalement est celui-ci. Voir `_accuser_l_acceptation`.
_journal_acceptation = logging.getLogger(__name__)





def recharger_le_referentiel() -> None:
    """Repart d'une lecture neuve. Destiné aux tests et à l'exploitation."""
    referentiel_d_acquisition.cache_clear()


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
    qualifications_memoire.cache_clear()




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
    #: Les réponses aux questions que le site a posées, par code. Facultatives,
    #: toutes. Voir `Question.visiteur`.
    declarations: dict[str, str] = Field(default_factory=dict)


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


def _preremplir_la_qualification(
    reference: str, demande: DemandeDeContact, instant: datetime
) -> None:
    """Ouvre la qualification du dossier avec ce que le visiteur a déclaré.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **RIEN ICI NE PEUT FAIRE ÉCHOUER LE DÉPÔT.**

    La demande est déjà rangée quand cette fonction est appelée. Un service sans
    questionnaire, une réponse qui ne se convertit pas, une question retirée du
    référentiel : le visiteur a fait son geste, et le cabinet a sa demande. Ce
    qui n'a pas pu être versé est **consigné**, parce qu'un préremplissage qui
    se tairait sur ses échecs ferait croire au responsable que le client n'avait
    rien dit.

    ⚠️ **LE DOSSIER NE CHANGE PAS D'ÉTAT.** Une qualification remplie par le
    formulaire n'est pas un échange : le compte à rebours de la réaffectation
    court toujours, et c'est le premier contact du responsable qui l'arrête.
    ─────────────────────────────────────────────────────────────────────────
    """
    if not demande.declarations:
        return
    try:
        questionnaire = referentiel_d_acquisition()["questionnaires"].prendre(
            demande.service_souhaite
        )
    except QuestionnaireIntrouvable:
        return
    qualifications = depot_qualifications()
    avant = qualifications.trouver(reference) or ouvrir_une_qualification(
        reference, questionnaire
    )
    apres, ecartees = preremplir(avant, questionnaire, demande.declarations, instant)
    if apres is not avant:
        qualifications.enregistrer(apres)
    if ecartees:
        _journal_acceptation.warning(
            "dossier %s : %d déclaration(s) du visiteur non versée(s) à la "
            "qualification (%s). Elles restent lisibles sur la demande.",
            reference,
            len(ecartees),
            ", ".join(ecartees),
        )


class AccuseDeDemande(BaseModel):
    """Ce que le visiteur reçoit. Volontairement pauvre — voir l'en-tête."""

    recue: bool = True
    #: Le canal sur lequel on le rappellera réellement, après repli.
    canal_de_rappel: Canal
    message: str
    #: 19 octobre 2026 (point 22) : de quoi joindre des pièces à la demande, depuis le
    #: formulaire, juste après l'envoi. ⚠️ OPAQUE : il porte la référence du dossier
    #: chiffrée, et la référence elle-même ne circule toujours pas.
    jeton_de_pieces: str | None = None


#: L'usage du sceau qui ouvre l'ajout de pièces à une demande. Voir `partage/sceau`.
_USAGE_PIECES = "pieces-de-demande"
#: Ce qu'une pièce jointe à une demande peut peser, et ce qu'elle peut être.
_TAILLE_MAXIMALE_PIECE = 10 * 1024 * 1024
_TYPES_DE_PIECE = frozenset({"application/pdf", "image/jpeg", "image/png"})


def _secret_des_pieces() -> str:
    return configuration().cle_chiffrement or "secret-de-developpement"


def _magasin_des_pieces() -> MagasinFichiersLocal:
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


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
    referentiel = referentiel_d_acquisition()
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
            declarations=corps.declarations,
        )
    except (NumeroInvalide, ValueError) as echec:
        raise HTTPException(status_code=422, detail=message_lisible(echec)) from echec

    depot = deposer_une_demande(
        demande, depot_dossiers(), reference=_reference("dos"), boite=boite_d_envoi()
    )
    _preremplir_la_qualification(depot.dossier.reference, demande, instant)

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
        jeton_de_pieces=jeton_opaque(
            depot.dossier.reference, secret=_secret_des_pieces(), usage=_USAGE_PIECES
        ),
    )


class PieceAccueillie(BaseModel):
    nom: str
    taille: int
    pieces: int


@routeur.post(
    "/demandes/pieces",
    status_code=201,
    summary="Joindre une pièce à sa demande (publique, par le jeton de l'accusé)",
    responses={
        404: {"description": "Demande inconnue, ou jeton faux : le même refus"},
        409: {"description": "La demande n'accepte plus de pièce"},
        413: {"description": "Pièce trop lourde"},
        415: {"description": "Ni PDF, ni image"},
    },
)
async def joindre_une_piece(
    jeton: str = Form(..., max_length=256),
    fichier: UploadFile = File(...),
) -> PieceAccueillie:
    """Publique, comme le dépôt de la demande qu'elle complète (point 22).

    ⚠️ La porte ne s'ouvre qu'avec le jeton rendu dans l'accusé, propre à cette demande
    et à cet usage : il ne vaut ni pour une autre demande, ni pour le suivi d'une
    création. Taille bornée à la lecture, type lu dans le contenu et non dans le nom.
    """
    # ⚠️ Seule la LECTURE du corps attend sur la boucle ; déchiffrer, hacher, écrire sur
    # disque et en base part dans un fil, sans quoi un envoi de 10 Mo bloquerait le
    # serveur entier (voir `tests/test_boucle_non_bloquee.py`).
    contenu = await fichier.read(_TAILLE_MAXIMALE_PIECE + 1)
    return await run_in_threadpool(_joindre_une_piece, jeton, contenu, fichier.filename)


def _joindre_une_piece(jeton: str, contenu: bytes, nom_du_fichier: str | None) -> PieceAccueillie:
    try:
        reference = dechiffrer_la_reference(
            jeton, secret=_secret_des_pieces(), usage=_USAGE_PIECES
        )
        dossier = depot_dossiers().lire(reference)
    except (SceauInvalide, DossierIntrouvable) as refus:
        raise HTTPException(
            status_code=404,
            detail="Ce lien ne s'ouvre pas : rechargez le formulaire, ou écrivez au cabinet.",
        ) from refus
    if len(contenu) > _TAILLE_MAXIMALE_PIECE:
        raise HTTPException(status_code=413, detail="Une pièce pèse au plus 10 Mo.")
    type_mime = detecter_le_type(contenu)
    if type_mime not in _TYPES_DE_PIECE:
        raise HTTPException(status_code=415, detail="Joignez un PDF ou une image (JPEG, PNG).")
    empreinte = hashlib.sha256(contenu).hexdigest()
    nom = "".join(c for c in (nom_du_fichier or "piece") if c.isprintable())[:200].strip()
    try:
        complete = dossier.joindre(
            PieceJointe(
                empreinte=empreinte,
                nom=nom or "piece",
                type_mime=type_mime,
                taille=len(contenu),
                recue_le=maintenant(),
            )
        )
    except PieceRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    _magasin_des_pieces().deposer(empreinte, contenu, type_mime=type_mime)
    if complete is not dossier:
        depot_dossiers().enregistrer(complete)
    return PieceAccueillie(nom=nom, taille=len(contenu), pieces=len(complete.pieces_jointes))


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
        demande, depot_dossiers(), reference=_reference("dos"), boite=boite_d_envoi()
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
    depot = depot_dossiers()
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
    depot = depot_dossiers()

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
        dossier = depot_dossiers().lire(reference)
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
        for proforma in sorted(depot_proformas().par_dossier(reference), key=lambda p: p.version)
    ]
    noms = _noms_des_collaborateurs()
    return FicheDuDossier.model_validate(
        dossier.model_dump()
        | {
            "proformas": proformas,
            "responsable_nom": noms.get(dossier.responsable or ""),
        }
    )


@routeur.get(
    "/dossiers/{reference}/pieces/{empreinte}",
    summary="Une pièce jointe par le visiteur à sa demande",
    responses={404: {"description": "Dossier ou pièce inconnus"}},
)
def lire_une_piece_jointe(acces: AccesRequis, reference: str, empreinte: str) -> Response:
    exiger(acces, Permission.LIRE_PROSPECT)
    try:
        dossier = depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    piece = next((p for p in dossier.pieces_jointes if p.empreinte == empreinte), None)
    if piece is None:
        raise HTTPException(status_code=404, detail="Pièce inconnue pour ce dossier.")
    return Response(
        content=_magasin_des_pieces().lire(empreinte),
        media_type=piece.type_mime,
        headers={"Content-Disposition": disposition_en_piece_jointe(piece.nom)},
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
    depot = depot_dossiers()
    try:
        dossier = depot.lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    if corps.candidats is None:
        candidatures = candidatures_du_cabinet(
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
            dossier, candidatures, referentiel_d_acquisition()["affectation"], maintenant()
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
        questionnaire = referentiel_d_acquisition()["questionnaires"].prendre(service)
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec
    return QuestionnairePublie(
        service=questionnaire.service,
        version=questionnaire.version,
        questions=list(questionnaire.ordonnees),
    )


class ValeurProposee(BaseModel):
    valeur: str
    libelle: str


class QuestionAuVisiteur(BaseModel):
    """Une question telle que le site la pose : rien de ce qui sert au juriste."""

    code: str
    libelle: str
    type: str
    valeurs: list[ValeurProposee] = Field(default_factory=list)
    unite: str | None = None
    aide: str = ""


class QuestionnaireDuVisiteur(BaseModel):
    service: str
    questions: list[QuestionAuVisiteur]


@routeur.get(
    "/questionnaires/{service}/visiteur",
    summary="Les questions que le site pose au visiteur pour ce service",
)
def lire_les_questions_du_visiteur(service: str) -> QuestionnaireDuVisiteur:
    """Publique. Ce que le site demande avant même qu'un responsable rappelle.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **PUBLIQUE, DONC PAUVRE.** Elle ne rend ni l'aide destinée au responsable,
    ni le rang, ni le caractère obligatoire, ni les questions internes. L'aide
    du juriste dit comment le cabinet raisonne et chiffre ; elle n'a rien à
    faire dans une réponse que tout le monde peut lire.

    ⚠️ **UN SERVICE SANS QUESTIONNAIRE REND UNE LISTE VIDE, PAS 404.** Le site
    appelle cette route pour chaque fiche de service ; une erreur ferait
    afficher un défaut au visiteur là où il n'y a simplement rien à demander.
    ─────────────────────────────────────────────────────────────────────────
    """
    try:
        questionnaire = referentiel_d_acquisition()["questionnaires"].prendre(service)
    except QuestionnaireIntrouvable:
        return QuestionnaireDuVisiteur(service=service, questions=[])
    return QuestionnaireDuVisiteur(
        service=questionnaire.service,
        questions=[
            QuestionAuVisiteur(
                code=q.code,
                libelle=q.libelle_visiteur or q.libelle,
                type=q.type.value,
                valeurs=[
                    ValeurProposee(
                        valeur=v, libelle=q.libelles_valeurs.get(v, v)
                    )
                    for v in (q.valeurs or ())
                ],
                unite=q.unite,
                aide=q.aide_visiteur,
            )
            for q in questionnaire.pour_le_visiteur
        ],
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
        dossier = depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        questionnaire = referentiel_d_acquisition()["questionnaires"].prendre(
            dossier.service
        )
    except QuestionnaireIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    qualifications = depot_qualifications()
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
    referentiel = referentiel_d_acquisition()
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
        dossier = depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    proposition, _ = proposition_du_dossier(dossier, corps.score_charge, corps.debours)
    # ⚠️ Le dossier avance **après** la preuve que la qualification est complète, et
    # jamais sur un dossier qui n'en est pas là : la proposition ci-dessus aurait levé.
    #
    # ⚠️ `EN_CONVERSATION` compte aussi, et **seulement parce que la proposition
    # ci-dessus a réussi**. Pour un service dont le gérant a arrêté le prix, il n'y
    # a pas d'étape « qualifiée » : rien n'a été qualifié. Un service sur étude
    # dans cet état n'arrive jamais ici — `proposition_du_dossier` aurait déjà
    # rendu 409 en nommant les réponses manquantes.
    if dossier.etat in (EtatDossier.QUALIFIEE, EtatDossier.EN_CONVERSATION):
        try:
            depot_dossiers().enregistrer(dossier.chiffrer(maintenant()))
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


def proposition_du_dossier(dossier, score_charge: int, debours: list[DeboursEntrant]):
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
    referentiel = referentiel_d_acquisition()
    jour = aujourd_hui()

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
    qualification = depot_qualifications().trouver(dossier.reference)
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

    depot = depot_dossiers()
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
    depot = depot_dossiers()
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
    depot = depot_dossiers()
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
        rappels_memoire,
    )

    session = session_de_travail()
    if session is None:
        return rappels_memoire()
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
        dossier = depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    qualification = depot_qualifications().trouver(reference)
    # ⚠️ TRAITÉ EN 404, COMME LES TROIS AUTRES APPELS. C'était le dernier qui
    # laissait l'exception s'échapper, et il tombait en **500** : la fiche du
    # dossier ne s'ouvrait plus du tout pour un service sans questionnaire —
    # `FORMATION`, `DOMICILIATION`, ou un dossier déposé sous un ancien code.
    # Le collaborateur voyait une page d'erreur au lieu du dossier de son client.
    try:
        questionnaire = referentiel_d_acquisition()["questionnaires"].prendre(
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


def candidatures_du_cabinet(service: str, dossiers) -> list[Candidature]:
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
        a_la_date=aujourd_hui(),
    )


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
    depot = depot_dossiers()
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
    #: ⚠️ **Cette vente n'ouvre pas d'espace, et ne l'ouvrira jamais.** Sans ce
    #: champ, `demarree: false` se lisait « pas encore », et la fiche d'une
    #: création d'entreprise affichait un sablier pour toujours : le produit
    #: promettait une ouverture que sa propre règle exclut.
    sans_objet: bool = False


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

    # ⚠️ Lu sur le service DEMANDÉ, qui est connu dès le dépôt : la console pose
    # la question avant l'encaissement, pour savoir s'il y a une adresse à
    # demander.
    try:
        sans_objet = not ouvre_un_locataire(
            depot_dossiers().lire(reference).demande.service_souhaite
        )
    except DossierIntrouvable:
        sans_objet = False

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
            sans_objet=sans_objet,
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
            sans_objet=sans_objet,
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
        sans_objet=False,
    )


def _adresse_voulue(proforma: Proforma, slug: str | None) -> str | None:
    """L'adresse de l'espace à ouvrir, ou `None` si cette vente n'en ouvre pas.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **ON NE DEMANDE UNE ADRESSE QUE S'IL Y A UN ESPACE À ADRESSER.**

    Les deux routes de règlement l'exigeaient pour toute vente. Depuis le
    27 septembre, seule l'offre de plateforme ouvre un locataire : pour une
    création d'entreprise, le collaborateur choisissait donc une adresse qui ne
    menait nulle part, et la fiche l'affichait ensuite comme « l'espace » du
    client.

    Vente sans espace : l'adresse reçue est **ignorée**, pas refusée. Un client
    plus ancien de l'API, qui l'envoie encore, ne doit pas voir échouer un
    encaissement pour un champ devenu sans objet.

    Vente avec espace : la règle d'origine tient entière, et l'adresse est
    validée ici, devant celui qui peut la corriger — voir `encaisser`.
    ─────────────────────────────────────────────────────────────────────────
    """
    if not ouvre_un_locataire(proforma.service):
        return None
    if slug is None or len(slug.strip()) < 3:
        raise HTTPException(
            status_code=422,
            detail=(
                f"le service {proforma.service} ouvre un espace de plateforme : "
                "son adresse est requise, sur trois caractères au moins."
            ),
        )
    try:
        valider_le_slug(slug)
    except SlugInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except NomsReservesIndisponibles as indisponible:
        # ⚠️ 503 et non 422 : le client n'a rien fait de mal, et son slug n'est
        # peut-être pas réservé. C'est la plateforme qui ne sait pas répondre, et un
        # slug attribué ne se reprend pas. Voir `valider_le_slug`.
        raise HTTPException(status_code=503, detail=str(indisponible)) from indisponible
    return slug


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

    #: ⚠️ Exigée **seulement** quand le service vendu ouvre un espace de
    #: plateforme. Pour une prestation, il n'y a rien à adresser : une adresse
    #: reçue est ignorée, et son absence n'est pas une faute. Voir `_adresse_voulue`.
    slug: str | None = Field(default=None, max_length=40)
    #: Le numéro à débiter. Vide, on emploie celui de la demande d'origine, qui
    #: est celui que le client a lui-même donné.
    telephone: str = ""


class ReglementDemande(BaseModel):
    dossier: str
    proforma: str
    #: `None` quand la vente n'ouvre pas d'espace.
    slug: str | None
    #: Le numéro réellement sollicité, sous sa forme canonique.
    telephone: str
    montant: Decimal
    paiement: str
    #: Ce que le prestataire a répondu à l'initiation. ⚠️ Ce n'est **pas** une
    #: preuve de paiement : le client n'a pas encore saisi son code.
    accepte: bool
    message: str


def solliciter_le_telephone(
    proforma: Proforma,
    dossier: DossierCommercial,
    depot,
    slug: str | None,
    telephone_demande: str,
) -> ReglementDemande:
    """Crée le paiement et demande le débit sur le téléphone du client.

    ⚠️ **UN SEUL CORPS POUR DEUX PORTES** (14 octobre 2026). Le règlement se
    demande depuis la console, par celui qui encaisse, et désormais depuis la
    page de la proposition, par le client lui-même. Deux copies de ce qui suit
    auraient divergé : l'une aurait reçu la correction « écrire avant d'appeler »
    et l'autre non, et c'est le client qui paie seul qui aurait été débité sans
    trace.
    """
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

    # ⚠️ Validée ici et non en tête de route : savoir si une adresse est due
    # suppose d'avoir lu la proforma, donc le service vendu.
    adresse = _adresse_voulue(proforma, slug)
    try:
        retenu = dossier.retenir_le_slug(adresse) if adresse else dossier
    except TransitionDossierRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus

    try:
        telephone = normaliser_telephone(
            telephone_demande or dossier.demande.telephone
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
        libelle=f"{proforma.service} · {proforma.numero}",
    )
    if initiation.reference_externe:
        boutique.paiements.enregistrer(
            paiement.avec_reference(initiation.reference_externe)
        )

    return ReglementDemande(
        dossier=dossier.reference,
        proforma=proforma.numero,
        slug=adresse,
        telephone=paiement.telephone,
        montant=paiement.montant,
        paiement=paiement.identifiant,
        accepte=initiation.accepte,
        message=initiation.message,
    )


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
    # ⚠️ `ENCAISSER`, et non plus `GERER_COMPTES` (14 octobre 2026) : c'est la
    # responsable de caisse qui constate un règlement. L'administrateur garde ce
    # droit ; voir `Permission.ENCAISSER`.
    exiger(acces, Permission.ENCAISSER)

    proformas = depot_proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    depot = depot_dossiers()
    try:
        dossier = depot.lire(proforma.dossier)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    return solliciter_le_telephone(proforma, dossier, depot, corps.slug, corps.telephone)


class ReglementDeCaisse(BaseModel):
    """Une ligne de la caisse : un règlement à constater, ou déjà constaté.

    ⚠️ **PAUVRE À DESSEIN.** La caissière a besoin de savoir qui doit combien et
    pour quelle proposition. Elle n'a besoin ni de la qualification, ni du
    chiffrage, ni des échanges : son rôle ne lit pas les prospects, et cette
    ligne ne les lui donne pas par la bande.
    """

    dossier: str
    client: str
    telephone: str
    service: str
    proforma: str
    montant: Decimal
    #: `A_ENCAISSER` tant que le client a accepté sans régler, `ENCAISSE` ensuite.
    etat: str
    payee_le: datetime | None = None
    #: Vrai seulement pour l'offre qui ouvre un espace : une adresse est alors due.
    ouvre_un_espace: bool = False


@routeur.get(
    "/caisse",
    summary="Les règlements à constater, et ceux qui l'ont été",
    responses={403: {"description": "Habilitation insuffisante"}},
)
def lire_la_caisse(acces: AccesRequis) -> list[ReglementDeCaisse]:
    """Ce que la responsable de caisse voit en ouvrant sa journée.

    ─────────────────────────────────────────────────────────────────────────
    ⚠️ **L'ÉTAT CHANGE TOUT SEUL, ET C'EST LE RAPPROCHEMENT.** Une ligne passe de
    `A_ENCAISSER` à `ENCAISSE` dès que le règlement est constaté, quel qu'en soit
    le chemin : la caissière au guichet, ou l'opérateur de mobile money qui
    notifie. Elle ne coche rien pour un paiement en ligne ; elle le voit réglé.

    Les deux chemins aboutissent au même geste de domaine, avec le même
    identifiant d'événement : un règlement notifié puis constaté à la main ne
    s'encaisse pas deux fois.

    ⚠️ D'abord ce qui est dû, du plus ancien au plus récent : c'est la file. Les
    règlements constatés suivent, du plus récent au plus ancien : c'est le
    journal de la journée.
    ─────────────────────────────────────────────────────────────────────────
    """
    exiger(acces, Permission.ENCAISSER)
    # 18 octobre 2026 : la matrice peut retirer cette famille de la vue de la caisse.
    exiger(acces, Permission.VOIR_VERSEMENTS_COMMERCE)
    proformas = depot_proformas()
    dus: list[tuple[datetime, ReglementDeCaisse]] = []
    regles: list[tuple[datetime, ReglementDeCaisse]] = []
    for dossier in depot_dossiers().lister():
        if dossier.etat not in (EtatDossier.ACCEPTEE, EtatDossier.PAYEE):
            continue
        # ⚠️ C'est l'ÉTAT DU DOSSIER qui dit qu'un règlement est dû, pas celui
        # de la proforma. Un dossier accepté par téléphone, ou repris d'un
        # ancien parcours, porte une proforma encore « transmise » : ne retenir
        # que les « acceptées » le faisait disparaître de la caisse, alors que
        # le client attend au guichet. On prend la proposition vivante la plus
        # récente, l'acceptée d'abord.
        vivantes = [
            p for p in proformas.par_dossier(dossier.reference)
            if p.etat not in (EtatProforma.REMPLACEE, EtatProforma.ANNULEE)
        ]
        if not vivantes:
            continue
        proforma = max(
            vivantes, key=lambda p: (p.etat is EtatProforma.ACCEPTEE, p.version)
        )
        ligne = ReglementDeCaisse(
            dossier=dossier.reference,
            client=dossier.demande.nom,
            telephone=dossier.demande.telephone,
            service=proforma.service,
            proforma=proforma.numero,
            montant=proforma.tarif.montant,
            etat="ENCAISSE" if dossier.etat is EtatDossier.PAYEE else "A_ENCAISSER",
            payee_le=dossier.payee_le,
            ouvre_un_espace=ouvre_un_locataire(proforma.service),
        )
        if dossier.etat is EtatDossier.PAYEE:
            regles.append((dossier.payee_le or dossier.depuis_le, ligne))
        else:
            dus.append((dossier.depuis_le, ligne))
    dus.sort(key=lambda couple: couple[0])
    regles.sort(key=lambda couple: couple[0], reverse=True)
    return [ligne for _, ligne in dus] + [ligne for _, ligne in regles]


class DemandeDEncaissement(BaseModel):
    """Ce qu'un collaborateur confirme après rapprochement.

    ⚠️ **Le slug est choisi par un humain, jamais dérivé du nom.** C'est une adresse
    publique, que le client lira, dictera au téléphone et verra sur ses documents.
    Le dériver de « Station Bonabéri & Fils SARL » produirait quelque chose
    d'illisible, et le changer plus tard casserait les liens déjà distribués.
    """

    #: ⚠️ Exigée **seulement** quand le service vendu ouvre un espace de
    #: plateforme. Pour une prestation, il n'y a rien à adresser : une adresse
    #: reçue est ignorée, et son absence n'est pas une faute. Voir `_adresse_voulue`.
    slug: str | None = Field(default=None, max_length=40)
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
    # ⚠️ `ENCAISSER`, et non plus `GERER_COMPTES` (14 octobre 2026) : c'est la
    # responsable de caisse qui constate un règlement. L'administrateur garde ce
    # droit ; voir `Permission.ENCAISSER`.
    exiger(acces, Permission.ENCAISSER)
    proformas = depot_proformas()
    try:
        proforma = proformas.lire(numero)
    except ProformaIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    adresse = _adresse_voulue(proforma, corps.slug)

    depot = depot_dossiers()
    try:
        dossier = depot.lire(proforma.dossier)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec

    try:
        resultat = encaisser_l_acceptation(
            dossier,
            proforma,
            boite=boite_du_parcours(),
            slug=adresse or "",
            tenant=f"tnt-{adresse}" if adresse else "",
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
        tenant=None if resultat.rejeu or not adresse else f"tnt-{adresse}",
        slug=None if resultat.rejeu else adresse,
        rejeu=resultat.rejeu,
    )




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

    depot = depot_dossiers()
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
    for dossier in depot_dossiers().ouverts():
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
