"""API du contexte C · Collecte de pièces.

Ces routes servent l'écran E03 — la boîte de réception — et son prolongement
naturel, l'écran de complétude d'un dossier.

**Une décision d'exposition mérite d'être signalée.** La liste des pièces ne rend
pas le verdict de conformité, seulement la *référence* du rapport. L'écran fait un
second appel au contexte D pour l'obtenir. Recopier le verdict ici le ferait
diverger du rapport dès la première réévaluation, et c'est la copie périmée qui
s'afficherait — sur l'écran même où le comptable décide s'il faut réclamer une
facture rectificative.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contextes.collecte.adaptateurs.sortant.depots_memoire import (
    PieceIntrouvable,
)
from app.contextes.collecte.adaptateurs.sortant.depots_sql import (
    DepotDemandesSql,
    DepotPiecesSql,
)
from app.contextes.collecte.adaptateurs.sortant.magasin_local import (
    CleInvalide,
    FichierAbsent,
    MagasinFichiersLocal,
    detecter_le_type,
)
from app.contextes.collecte.adaptateurs.sortant.magasins_memoire import (
    demandes_en_memoire,
    pieces_en_memoire,
)
from app.contextes.collecte.api import (
    CanalDepot,
    CompletudeDossier,
    DemandePiece,
    DepotRefuse,
    EtatPiece,
    PieceJustificative,
    RelancePiece,
    ResultatReception,
    SuspicionDoublon,
    TransitionRefusee,
    TypePiece,
    detecter_doublons,
    empreinte,
    evaluer_completude,
    receptionner,
    relances_du_jour,
)
from app.contextes.collecte.application.demandes_de_piece import (
    DemandeIntrouvable,
    DemandeRefusee,
    classer_une_demande,
    demander_une_rectificative,
    repondre_a_une_demande,
    satisfaire_une_demande,
    tracer_une_relance,
)
from app.contextes.collecte.adaptateurs.sortant.reponses_adherent_yaml import (
    charger_les_reponses_de_l_adherent,
)
from app.contextes.collecte.application.lecture import lire_une_piece
from app.contextes.collecte.domaine.demandes import NatureDeReponse
from app.contextes.collecte.domaine.espace_adherent import (
    ReglagesDesReponses,
    StatutPourLAdherent,
    VueDesJustificatifs,
    texte_de_la_reponse,
    vue_des_justificatifs,
)
from app.contextes.collecte.application.traitement import classer_une_piece
from app.contextes.collecte.domaine.ports import DepotDemandes, DepotPieces
from app.contextes.transverse.api import (
    LOCATAIRE_PAR_DEFAUT,
    AccesRequis,
    EtatCompte,
    Permission,
    Role,
    atelier,
    exiger,
    exiger_dossier,
    habilitations_actives,
    restreindre,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.formats import montant_fcfa
from app.partage.horloge import maintenant
from app.partage.locataire import courant
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

routeur = APIRouter(prefix="/collecte", tags=["Collecte"])


# ⚠️ Le magasin mémoire du contexte propriétaire, et non une construction locale :
# voir `magasins_memoire.py` de ce contexte, et le pas 52.
_pieces_memoire = pieces_en_memoire
_demandes_memoire = demandes_en_memoire


class LignePiece(BaseModel):
    """Une ligne de la boîte de réception.

    Elle porte les indicateurs **calculés** — ancienneté, délai de transmission,
    retard de remise — plutôt que de laisser l'écran les recalculer. Ce sont des
    règles métier : un front qui soustrairait deux dates lui-même finirait par
    diverger du backend, et personne ne saurait lequel des deux a raison.
    """

    identifiant: str
    entreprise: str
    canal: CanalDepot
    etat: EtatPiece
    type: str

    depose_le: date
    recue_le: date
    jours_de_transmission: int
    anciennete: int
    retard_de_remise: int | None

    reference_document: str | None
    date_document: date | None
    montant_ttc: float | None
    emetteur: str | None
    nom_fichier: str | None

    reference_rapport: str | None
    reference_ecriture: str | None
    traitee: bool
    identifiee: bool
    commentaire: str | None


def _ligne(piece: PieceJustificative, a_la_date: date) -> LignePiece:
    return LignePiece(
        identifiant=piece.identifiant,
        entreprise=piece.entreprise,
        canal=piece.canal,
        etat=piece.etat,
        type=piece.type,
        depose_le=piece.depose_le,
        recue_le=piece.recue_le.date(),
        jours_de_transmission=piece.jours_de_transmission(),
        anciennete=piece.anciennete(a_la_date),
        retard_de_remise=piece.retard_de_remise(),
        reference_document=piece.reference_document,
        date_document=piece.date_document,
        montant_ttc=float(piece.montant_ttc) if piece.montant_ttc is not None else None,
        emetteur=piece.emetteur,
        nom_fichier=piece.nom_fichier,
        reference_rapport=piece.reference_rapport,
        reference_ecriture=piece.reference_ecriture,
        traitee=piece.traitee,
        identifiee=piece.identifiee,
        commentaire=piece.commentaire,
    )


@routeur.get("/pieces", summary="La boîte de réception")
def lister_pieces(
    acces: AccesRequis,
    a_la_date: date = Query(
        ..., description="Date de référence pour les anciennetés et les retards"
    ),
    entreprise: str | None = Query(None, description="NIU du dossier"),
    canal: CanalDepot | None = Query(None),
    etat: EtatPiece | None = Query(None),
    en_souffrance: bool = Query(
        False, description="Ne rend que les pièces reçues et non encore traitées"
    ),
) -> list[LignePiece]:
    exiger(acces, Permission.LIRE_PIECE)
    if entreprise is not None:
        # Le filtre explicite est contrôlé **avant** d'être appliqué : sans cela,
        # demander le NIU d'un dossier hors portefeuille rendrait une liste vide,
        # et l'absence de résultat se distinguerait mal du refus. Le 404 dit ce
        # qu'il en est — de son point de vue, ce dossier n'existe pas.
        exiger_dossier(acces, Permission.LIRE_PIECE, entreprise)
        pieces = depot_pieces().du_dossier(entreprise)
    else:
        pieces = restreindre(acces, depot_pieces().toutes(), lambda p: p.entreprise)
    if canal is not None:
        pieces = [p for p in pieces if p.canal is canal]
    if etat is not None:
        pieces = [p for p in pieces if p.etat is etat]
    if en_souffrance:
        pieces = [p for p in pieces if p.en_attente_de_traitement]
    return [_ligne(piece, a_la_date) for piece in pieces]


class FichePiece(BaseModel):
    """Une pièce et sa confrontation au reste du dossier."""

    piece: PieceJustificative
    suspicions: list[SuspicionDoublon]


class Arbitrage(BaseModel):
    """Un doublon à trancher : la pièce qui arrive, et celle qu'elle redouble."""

    piece: str
    suspicion: SuspicionDoublon


@routeur.get(
    "/pieces/{identifiant}",
    summary="Une pièce, avec ses doublons éventuels",
)
def lire_piece(acces: AccesRequis, identifiant: str) -> FichePiece:
    """Rend la pièce et la confrontation au reste du dossier.

    Les deux ensemble, en un appel : afficher une pièce sans dire qu'elle a un
    jumeau au dossier serait la montrer sous son jour le plus rassurant, et c'est
    précisément le cas où il ne faut pas l'être.
    """
    introuvable = f"pièce {identifiant} introuvable."
    try:
        piece = depot_pieces().lire(identifiant)
    except PieceIntrouvable as absence:
        raise HTTPException(status_code=404, detail=introuvable) from absence

    # Pas 88 : même réponse hors périmètre qu'inconnue. Voir `exiger_dossier`.
    exiger_dossier(acces, Permission.LIRE_PIECE, piece.entreprise, introuvable=introuvable)
    return FichePiece(
        piece=piece,
        suspicions=detecter_doublons(piece, depot_pieces().du_dossier(piece.entreprise)),
    )


class DepotDePiece(BaseModel):
    """Ce qu'un déposant peut légitimement déclarer. **Et rien d'autre.**

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ CE MODÈLE REMPLACE L'ENTITÉ DU DOMAINE COMME CORPS DE REQUÊTE

    La route acceptait `PieceJustificative` telle quelle. Un adhérent — le
    **client**, pas le cabinet — pouvait donc poster une pièce en déclarant
    lui-même :

    * `etat: COMPTABILISEE` et une `reference_ecriture` pointant sur une écriture
      qui n'est pas la sienne. La complétude et le score de risque lisent l'état :
      une pièce manquante se cachait en se déclarant comptabilisée ;
    * une `empreinte` fabriquée, alors que le magasin de fichiers est adressé par
      le contenu. Le commentaire voisin affirme pourtant que « l'empreinte est
      recalculée par le serveur et fait foi, ce qui rend la détection de doublon
      indépendante de ce que le client affirme » — c'était vrai de `/fichiers`,
      et **faux de cette route-ci** ;
    * un `recue_le` de son choix, quand le domaine écrit que cette date est
      « horodatée par le système à l'arrivée effective. **Seule celle-ci fait
      foi** » ;
    * rien du tout dans `depose_par` : l'acte n'avait **aucun auteur**, alors
      qu'une session était ouverte.

    ⚠️ **Le domaine nommait la règle, et la route donnait la plume au client.**

    C'est la discipline que `DemandeSaisie` énonce dans le contexte comptable :
    *« Il ne porte ni numéro, ni état, ni valideur. Ce qu'un client ne peut pas
    envoyer n'a pas besoin d'être contrôlé. »* La comptabilité l'appliquait ; la
    collecte, non.
    ─────────────────────────────────────────────────────────────────────────────
    """

    # ⚠️ **Un champ inconnu est refusé, jamais ignoré.**
    #
    # Sans cela, un client qui poste `etat: COMPTABILISEE` reçoit un 201 et croit
    # avoir posé l'état. Il ne l'a pas posé — le serveur le pose — mais rien ne le
    # lui dit, et l'intégrateur qui a écrit ce script ne l'apprendra jamais.
    #
    # C'est la même règle que le projet applique aux règles d'évaluation : *un
    # critère silencieusement absent est indiscernable d'un critère satisfait.*
    model_config = ConfigDict(extra="forbid")

    #: Le dossier destinataire. Contrôlé par l'habilitation : un déposant qui
    #: nomme le dossier d'un autre reçoit un 404 qui ne confirme même pas que ce
    #: dossier existe.
    entreprise: str = Field(min_length=1)
    canal: CanalDepot

    #: ⚠️ **Obligatoire : un dépôt sans document n'est pas un dépôt.**
    #:
    #: Le domaine admet une pièce sans empreinte, et il a raison — une pièce peut
    #: exister avant son fichier. Mais ce cas-là s'appelle une **demande de
    #: pièce**, il a son agrégat et ses routes, et il porte ce que le dépôt n'a
    #: pas : une échéance et un caractère bloquant.
    #:
    #: Accepter un dépôt sans document ferait entrer dans la boîte de réception
    #: des lignes que rien ne permet de contrôler, et la complétude les compterait
    #: comme reçues.
    #:
    #: ⚠️ **La clé rendue par `POST /fichiers`**, c'est-à-dire l'empreinte que le
    #: serveur a calculée sur les octets reçus. Elle est **revérifiée** ici : le
    #: fichier est relu et son empreinte recalculée. Un client qui inventerait une
    #: clé est refusé, et une divergence signalerait une corruption du magasin —
    #: ce que l'adressage par le contenu existe précisément pour détecter.
    empreinte: str = Field(min_length=64, max_length=64)

    #: Déclarée par l'expéditeur : la date à laquelle il dit avoir déposé. Le
    #: domaine l'admet, et c'est légitime — une pièce remise par coursier a été
    #: déposée avant d'être saisie.
    #:
    #: ⚠️ **Bornée aux deux sens.** Une date future n'existe pas ; une date
    #: antérieure de six ans permettait de fabriquer un historique. Les délais de
    #: collecte, les relances et la veille lisent cette date.
    depose_le: date | None = None

    #: Ce que le déposant sait du document. Tout est facultatif : avant lecture,
    #: on ne sait pas ce qu'on a reçu, et exiger le montant d'une facture pour
    #: l'accepter ferait renoncer la moitié des adhérents.
    type: TypePiece = TypePiece.INDETERMINE
    reference_document: str | None = None
    date_document: date | None = None
    montant_ttc: Decimal | None = None
    emetteur: str | None = None
    commentaire: str | None = None
    nom_fichier: str | None = None


#: L'antériorité admise pour une date de dépôt déclarée. Quatre-vingt-dix jours :
#: un trimestre couvre le rythme réel d'un adhérent qui apporte ses pièces, et
#: au-delà c'est une reprise d'historique — un geste du cabinet, pas un dépôt.
ANTERIORITE_ADMISE = timedelta(days=90)


@routeur.post(
    "/pieces",
    summary="Déposer une pièce",
    status_code=201,
    responses={
        404: {"description": "Aucun fichier sous cette empreinte"},
        # Le modèle est déclaré : sans lui, le schéma de la réponse 200 était vide, et l'outil
        # de contrat des écrans ne pouvait plus vérifier ce que lit l'écran du dépôt.
        200: {
            "model": ResultatReception,
            "description": "Rejeu : la pièce existait déjà, rendue inchangée",
        },
        409: {"description": "Doublon certain — le fichier a déjà été reçu"},
        422: {"description": "Date de dépôt hors des bornes admises"},
    },
    description=(
        "Accuse réception et confronte la pièce au dossier.\n\n"
        "**Rejouable** : l'identifiant dérivant de l'empreinte, redéposer le même "
        "fichier sur le même dossier rend la même pièce, **inchangée**, en 200 avec "
        "`rejeu: true`. Un client dont la connexion tombe après l'envoi peut donc "
        "recommencer sans crainte (pas 96 : le rejeu réécrivait la pièce).\n\n"
        "Un document **ressemblant** est accepté et signalé pour arbitrage : "
        "refuser automatiquement ferait perdre une charge déductible le jour où un "
        "fournisseur réutilise ses numéros. Un fichier déjà reçu sous une **autre** "
        "identité — une pièce entrée par un autre chemin — est refusé en 409."
    ),
)
def deposer_piece(
    acces: AccesRequis, depot: DepotDePiece, reponse: Response
) -> ResultatReception:
    """Le système pose ce qui fait foi ; le déposant déclare ce qu'il sait.

    ─────────────────────────────────────────────────────────────────────────────
    CE QUE LE DÉPOSANT NE FOURNIT PLUS, ET QUI LE POSE

    | Champ | Posé par |
    | --- | --- |
    | `identifiant` | le serveur, dérivé de l'empreinte |
    | `recue_le` | l'horloge du serveur — *seule celle-ci fait foi* |
    | `etat` | `REÇUE`, toujours |
    | `depose_par` | la session ouverte |
    | `taille_octets` | le magasin, par relecture |
    | `reference_ecriture`, `reference_rapport` | les contextes qui les produisent |
    | `reference_rapprochement` | le rapprochement bancaire |

    ⚠️ **L'IDENTIFIANT DÉRIVE DE L'EMPREINTE**, il n'est pas tiré au sort. Deux
    dépôts du même fichier sur le même dossier produisent donc la même clé, et le
    second ne crée pas une seconde ligne : c'est la même propriété que le magasin
    adressé par le contenu, appliquée à la pièce. Un identifiant tiré au sort
    aurait laissé au seul détecteur de doublon le soin d'attraper un rejeu.
    ─────────────────────────────────────────────────────────────────────────────
    """
    exiger_dossier(acces, Permission.DEPOSER_PIECE, depot.entreprise)

    # ⚠️ Relu, jamais cru. Le magasin est adressé par le contenu : recalculer
    # l'empreinte vérifie à la fois que le client ne l'a pas inventée et que le
    # magasin n'a pas été altéré. C'est le seul moment du parcours où cette
    # vérification coûte une lecture déjà nécessaire.
    try:
        contenu = magasin_fichiers().lire(depot.empreinte)
    except Exception as absent:  # noqa: BLE001 — un fichier absent n'est pas une panne
        raise HTTPException(
            status_code=404,
            detail=(
                "aucun fichier sous cette empreinte. Déposer le document par "
                "`POST /collecte/fichiers` d'abord, puis reporter la clé rendue."
            ),
        ) from absent

    reelle = hashlib.sha256(contenu).hexdigest()
    if reelle != depot.empreinte:
        raise HTTPException(
            status_code=409,
            detail=(
                "l'empreinte du fichier rangé ne correspond plus à sa clé. Le "
                "magasin a été altéré : ne rien déposer sur ce document."
            ),
        )

    aujourd_hui = maintenant().date()
    declaree = depot.depose_le or aujourd_hui
    if declaree > aujourd_hui:
        # Pas 88 : le refus d'une date future parlait de « pièce plus ancienne ».
        raise HTTPException(
            status_code=422,
            detail=(
                f"date de dépôt {declaree} dans l'avenir : une pièce ne se dépose pas "
                "avant d'avoir été remise."
            ),
        )
    if not (aujourd_hui - ANTERIORITE_ADMISE <= declaree <= aujourd_hui):
        raise HTTPException(
            status_code=422,
            detail=(
                f"date de dépôt {declaree} hors des bornes : elle doit être "
                f"comprise entre {aujourd_hui - ANTERIORITE_ADMISE} et "
                f"{aujourd_hui}. Une pièce plus ancienne relève d'une reprise "
                "d'historique, qui est un geste du cabinet."
            ),
        )

    piece = PieceJustificative(
        # Dérivé de l'empreinte : voir la docstring. Le dossier entre dans la clé
        # parce que deux adhérents peuvent légitimement déposer le même document.
        identifiant=f"PJ-{depot.entreprise[:6]}-{depot.empreinte[:16]}",
        entreprise=depot.entreprise,
        canal=depot.canal,
        depose_le=declaree,
        recue_le=maintenant(),
        type=depot.type,
        etat=EtatPiece.RECUE,
        nom_fichier=depot.nom_fichier,
        empreinte=reelle,
        taille_octets=len(contenu),
        reference_document=depot.reference_document,
        date_document=depot.date_document,
        montant_ttc=depot.montant_ttc,
        emetteur=depot.emetteur,
        commentaire=depot.commentaire,
        depose_par=acces.compte,
    )

    try:
        resultat = receptionner(piece, depot_pieces().du_dossier(depot.entreprise))
    except DepotRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    if resultat.rejeu:
        # ⚠️ Pas 96 : la pièce existe, elle n'est **pas** réécrite (voir `receptionner`).
        # 200 et non 201 : rien n'a été créé, et c'est ce que le code dit à un client
        # qui rejoue sa file de dépôts.
        reponse.status_code = 200
        return resultat
    depot_pieces().enregistrer(resultat.piece)
    return resultat


# ── Le fichier lui-même ─────────────────────────────────────────────────────
#
# Jusqu'ici, une « pièce justificative » n'était que des métadonnées : un nom de
# fichier, une empreinte, une taille — et aucun document. Le rapport de
# conformité désignait des constats sans que rien ne permette de les vérifier
# sur la facture.
#
# ⚠️ CE QUI SE JOUE SUR CES DEUX ROUTES
#
# Ce sont les seules du système à accepter puis à **rendre** un fichier fourni
# de l'extérieur. Trois contrôles y sont indispensables, et chacun répare une
# faille distincte :
#
#   · le **type réel** est lu dans les octets, jamais cru sur déclaration — un
#     document HTML étiqueté `image/jpeg`, rendu plus tard avec cette étiquette,
#     ferait s'exécuter du script dans le domaine du cabinet, sur la page où un
#     comptable est connecté ;
#   · la **taille** est bornée avant lecture intégrale, faute de quoi un envoi
#     unique suffit à épuiser la mémoire du processus ;
#   · l'**empreinte est recalculée** par le serveur et fait foi, ce qui rend la
#     détection de doublon indépendante de ce que le client affirme.


#: La taille maximale d'un dépôt. Une facture numérisée en A4 couleur pèse
#: rarement plus de trois mégaoctets ; vingt laissent la marge d'un document de
#: plusieurs pages photographié au téléphone, sans permettre qu'un seul envoi
#: sature le processus.
TAILLE_MAXIMALE = 20 * 1024 * 1024


class DepotFichier(BaseModel):
    """Ce que le serveur retient d'un fichier reçu."""

    empreinte: str
    taille_octets: int
    type_mime: str
    nom_fichier: str
    #: Vrai si l'octet exact était déjà au magasin. Ce n'est pas une erreur :
    #: le même fichier peut légitimement servir deux pièces, et le dire évite
    #: qu'on croie à un échec.
    deja_present: bool


@routeur.post(
    "/fichiers",
    summary="Déposer le fichier d'une pièce",
    status_code=201,
    responses={
        413: {"description": "Fichier trop volumineux"},
        415: {"description": "Type de document refusé"},
    },
    description=(
        "Range le document et rend son empreinte, à reporter sur la pièce. "
        "Le type est déterminé par lecture des premiers octets ; le type déclaré "
        "par le client n'est pas pris en compte."
    ),
)
async def deposer_fichier(
    acces: AccesRequis,
    entreprise: str = Query(..., description="NIU du dossier"),
    fichier: UploadFile = File(..., description="Le document numérisé ou photographié"),
) -> DepotFichier:
    exiger_dossier(acces, Permission.DEPOSER_PIECE, entreprise)

    # ⚠️ Lecture bornée. `await fichier.read()` sans borne charge en mémoire
    # tout ce que l'appelant envoie — un seul envoi de plusieurs gigaoctets
    # suffit alors à tuer le processus. On lit une unité de plus que la limite,
    # pour distinguer « exactement à la limite » de « au-delà ».
    contenu = await fichier.read(TAILLE_MAXIMALE + 1)
    if len(contenu) > TAILLE_MAXIMALE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Fichier trop volumineux : la limite est de "
                f"{TAILLE_MAXIMALE // (1024 * 1024)} Mo. Numérisez en niveaux de "
                "gris ou réduisez la résolution."
            ),
        )
    if not contenu:
        raise HTTPException(status_code=422, detail="Le fichier est vide.")

    type_reel = detecter_le_type(contenu)
    if type_reel is None:
        raise HTTPException(
            status_code=415,
            detail=(
                "Type de document refusé. Formats acceptés : PDF, JPEG, PNG, "
                "TIFF. ⚠️ Le type est déterminé par le contenu du fichier, non "
                "par son extension ni par ce que le navigateur déclare."
            ),
        )

    cle = empreinte(contenu)
    magasin = magasin_fichiers()
    deja = magasin.existe(cle)
    magasin.deposer(cle, contenu, type_mime=type_reel)
    return DepotFichier(
        empreinte=cle,
        taille_octets=len(contenu),
        type_mime=type_reel,
        # ⚠️ Le nom est **assaini** : il est choisi par l'appelant et finira dans
        # un en-tête `Content-Disposition`. Un nom porteur de retour chariot y
        # injecterait un en-tête de son choix.
        nom_fichier=_nom_assaini(fichier.filename),
        deja_present=deja,
    )


@routeur.get(
    "/pieces/{identifiant}/fichier",
    summary="Le document d'une pièce",
    responses={404: {"description": "Pièce inconnue, ou aucun fichier rattaché"}},
)
def telecharger_fichier(acces: AccesRequis, identifiant: str) -> Response:
    """Rend le document, avec les en-têtes qui empêchent son interprétation."""
    introuvable = f"pièce {identifiant} introuvable."
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=introuvable)
    # Pas 88 : même réponse hors périmètre qu'inconnue. Voir `exiger_dossier`.
    exiger_dossier(acces, Permission.LIRE_PIECE, piece.entreprise, introuvable=introuvable)
    if piece.empreinte is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Aucun fichier n'est rattaché à cette pièce. Elle a été saisie "
                "sans document — ce qui reste possible pour une pièce interne."
            ),
        )

    magasin = magasin_fichiers()
    try:
        contenu = magasin.lire(piece.empreinte)
    except (FichierAbsent, CleInvalide) as absent:
        # ⚠️ Le fichier manque alors que la pièce le référence : incohérence
        # entre la base et le magasin, pas erreur de l'appelant. Le 404 est
        # correct de son point de vue, mais la trace doit exister.
        raise HTTPException(
            status_code=404, detail="Le document est introuvable au magasin."
        ) from absent

    nom = piece.nom_fichier or f"{piece.identifiant}"
    return Response(
        content=contenu,
        media_type=magasin.type_mime(piece.empreinte),
        headers={
            # `attachment` : le document ne s'ouvre pas dans la page. Combiné à
            # `nosniff`, c'est ce qui empêche qu'un fichier déposé s'exécute
            # dans le domaine du cabinet.
            "Content-Disposition": f'attachment; filename="{_nom_assaini(nom)}"',
            "X-Content-Type-Options": "nosniff",
            # ⚠️ Un justificatif n'a rien à faire dans un cache partagé : il est
            # nominatif, et le cloisonnement ne vaut plus rien si un mandataire
            # le sert à la requête suivante.
            "Cache-Control": "private, no-store",
        },
    )


def _nom_assaini(nom: str | None) -> str:
    """Un nom de fichier utilisable dans un en-tête, et rien de plus.

    Retire tout ce qui n'est pas alphanumérique, point, tiret ou blanc — ce qui
    élimine d'un coup les séparateurs de chemin, les guillemets et les retours
    chariot. Un nom vide devient « document » : un en-tête sans nom fait
    enregistrer le fichier sous le nom de la route.
    """
    nettoye = re.sub(r"[^A-Za-z0-9 ._-]", "", (nom or "").strip())[:120]
    return nettoye or "document"


@routeur.get(
    "/doublons",
    summary="Les arbitrages de doublon en attente sur tout le portefeuille",
    description=(
        "Le contrôle qu'aucune règle du contexte D ne peut faire : le moteur de "
        "conformité examine une facture, jamais un ensemble. Sans cet écran, la même "
        "facture reçue par deux canaux fait déduire la TVA deux fois."
    ),
)
def lister_doublons(acces: AccesRequis) -> list[Arbitrage]:
    exiger(acces, Permission.ARBITRER_DOUBLON)
    pieces = depot_pieces().toutes()
    par_dossier: dict[str, list[PieceJustificative]] = {}
    for piece in pieces:
        par_dossier.setdefault(piece.entreprise, []).append(piece)

    arbitrages: list[Arbitrage] = []
    deja_vus: set[frozenset[str]] = set()
    for piece in pieces:
        for suspicion in detecter_doublons(piece, par_dossier[piece.entreprise]):
            # Une paire n'est signalée qu'une fois : la confrontation est
            # symétrique, et deux lignes pour un seul arbitrage feraient croire à
            # deux problèmes.
            paire = frozenset({piece.identifiant, suspicion.piece_existante})
            if paire in deja_vus:
                continue
            deja_vus.add(paire)
            arbitrages.append(Arbitrage(piece=piece.identifiant, suspicion=suspicion))
    return arbitrages


@routeur.get("/demandes", summary="Les pièces que le cabinet attend")
def lister_demandes(
    acces: AccesRequis,
    entreprise: str | None = Query(None),
    ouvertes_seulement: bool = Query(True),
) -> list[DemandePiece]:
    depot = depot_demandes()
    exiger(acces, Permission.LIRE_PIECE)
    if entreprise is not None:
        exiger_dossier(acces, Permission.LIRE_PIECE, entreprise)
    demandes = depot.ouvertes(entreprise) if ouvertes_seulement else depot.toutes(entreprise)
    return restreindre(acces, demandes, lambda d: d.entreprise)


# ── Le cycle d'une demande (pas 74) ───────────────────────────────────────────
#
# ─────────────────────────────────────────────────────────────────────────────
# QUATRE GESTES, TROIS PERMISSIONS, ET CE N'EST PAS DE LA CÉRÉMONIE
#
#   demander une rectificative   CONTROLER_CONFORMITE   qui a vu l'anomalie la demande
#   satisfaire par une pièce     IDENTIFIER_PIECE       qui identifie la pièce la rattache
#   classer sans suite           RELANCER_ADHERENT      qui suit la relation décide
#   tracer une relance           RELANCER_ADHERENT      qui relance le consigne
#
# Chaque geste vérifie le dossier de la demande, pas seulement la permission : un
# collaborateur habilité sur trois dossiers ne satisfait pas la demande d'un quatrième.
# ─────────────────────────────────────────────────────────────────────────────


class DemandeDeRectificative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10)
    #: Calée sur une échéance, idéalement. Vide : aucune date promise.
    attendue_pour: date | None = None


class RectificativeDemandee(BaseModel):
    """La demande émise, et combien d'adhérents en ont été prévenus."""

    demande: DemandePiece
    #: ⚠️ Zéro n'est pas une erreur : un dossier sans compte adhérent actif existe.
    #: L'écran le dit, pour que le collaborateur prévienne par un autre canal.
    adherents_prevenus: int


def _prevenir_les_adherents(demande: DemandePiece, reference: str | None) -> int:
    """Prévient par courriel les comptes adhérents actifs du dossier.

    ⚠️ Le motif part dans le message : c'est ce que l'adhérent doit transmettre à son
    fournisseur. Rien d'autre du dossier n'y figure.
    """
    boutique = atelier()
    aujourd_hui = maintenant().date()
    prevenus = 0
    for habilitation in habilitations_actives(
        boutique.habilitations.pour_dossier(demande.entreprise), aujourd_hui
    ):
        if habilitation.role is not Role.ADHERENT:
            continue
        compte = boutique.comptes.lire(habilitation.compte)
        if compte.etat is not EtatCompte.ACTIF:
            continue
        boutique.notifications.envoyer(
            "piece.rectificative_demandee",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.prenom,
                "reference": reference or demande.piece_a_rectifier or "",
                "motif": demande.motif,
            },
        )
        prevenus += 1
    return prevenus


class DemandeDeLecture(BaseModel):
    """Ce que le comptable lit sur le document. Tout est facultatif : seuls les champs
    fournis complètent la pièce, et la pièce doit être identifiée à l'issue."""

    model_config = ConfigDict(extra="forbid")

    type: TypePiece | None = None
    reference_document: str | None = Field(default=None, min_length=1, max_length=120)
    date_document: date | None = None
    montant_ttc: Decimal | None = Field(default=None, ge=0)
    emetteur: str | None = Field(default=None, min_length=1, max_length=200)


@routeur.post(
    "/pieces/{identifiant}/lecture",
    summary="Lire une pièce reçue : l'identifier et la passer à LUE",
    responses={
        404: {"description": "Pièce inconnue, ou hors du portefeuille"},
        409: {"description": "Pièce déjà lue, ou encore non identifiée"},
    },
)
def lire_la_piece(
    acces: AccesRequis, identifiant: str, demande: DemandeDeLecture
) -> PieceJustificative:
    """Pas 91 : la transition `marquer_lue` existait au domaine, sans route.

    `IDENTIFIER_PIECE` : lire une pièce, c'est dire ce qu'elle est. Voir
    `application/lecture.py` pour ce qui est refusé, et pourquoi.
    """
    exiger(acces, Permission.IDENTIFIER_PIECE)
    introuvable = f"pièce {identifiant} introuvable."
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=introuvable)
    exiger_dossier(acces, Permission.IDENTIFIER_PIECE, piece.entreprise, introuvable=introuvable)
    try:
        return lire_une_piece(
            piece,
            type=demande.type,
            reference_document=demande.reference_document,
            date_document=demande.date_document,
            montant_ttc=demande.montant_ttc,
            emetteur=demande.emetteur,
            pieces=depot_pieces(),
        )
    except TransitionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


class DemandeDeClassement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=1, max_length=500)


@routeur.post(
    "/pieces/{identifiant}/classement",
    summary="Classer une pièce sans écriture : doublon, relevé rapproché, document hors sujet",
    responses={
        404: {"description": "Pièce inconnue, ou hors du portefeuille"},
        409: {"description": "Motif trop court, ou traitement déjà terminé"},
    },
)
def classer_la_piece(
    acces: AccesRequis, identifiant: str, demande: DemandeDeClassement
) -> PieceJustificative:
    """Pas 107 : `archiver` existait au domaine, sans route. Un doublon ou un relevé bancaire,
    qui ne produisent pas d'écriture, restaient « à traiter » pour toujours, et bloquaient la
    clôture de leur mois. Voir `application/traitement.py`.

    `IDENTIFIER_PIECE`, comme la lecture : dire qu'une pièce ne produira pas d'écriture, c'est
    dire ce qu'elle est. L'acte est inscrit au journal d'audit, avec son motif : une pièce
    classée disparaît du travail à faire, et il faut pouvoir dire pourquoi.
    """
    exiger(acces, Permission.IDENTIFIER_PIECE)
    introuvable = f"pièce {identifiant} introuvable."
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=introuvable)
    exiger_dossier(acces, Permission.IDENTIFIER_PIECE, piece.entreprise, introuvable=introuvable)
    try:
        classee = classer_une_piece(piece, motif=demande.motif, pieces=depot_pieces())
    except TransitionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="collecte.piece_classee",
        objet_type="piece_justificative",
        objet_id=classee.identifiant,
        apres={"dossier": classee.entreprise, "motif": classee.motif_archivage},
    )
    return classee


@routeur.post(
    "/pieces/{identifiant}/rectificative",
    summary="Demander la facture rectificative d'une pièce",
    status_code=201,
)
def demander_rectificative(
    acces: AccesRequis, identifiant: str, demande: DemandeDeRectificative
) -> RectificativeDemandee:
    exiger(acces, Permission.CONTROLER_CONFORMITE)
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=f"pièce {identifiant} introuvable.")
    exiger_dossier(
        acces,
        Permission.CONTROLER_CONFORMITE,
        piece.entreprise,
        introuvable=f"pièce {identifiant} introuvable.",
    )
    aujourd_hui = maintenant().date()
    try:
        emise = demander_une_rectificative(
            identifiant=f"DP-{aujourd_hui.year}-{uuid.uuid4().hex[:8].upper()}",
            piece=identifiant,
            motif=demande.motif,
            attendue_pour=demande.attendue_pour,
            le=aujourd_hui,
            par=acces.compte,
            pieces=depot_pieces(),
            demandes=depot_demandes(),
        )
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except ValueError as invalide:
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    return RectificativeDemandee(
        demande=emise,
        adherents_prevenus=_prevenir_les_adherents(emise, piece.reference_document),
    )


def _demande_du_perimetre(acces, identifiant: str, permission: Permission) -> DemandePiece:
    """La demande, si elle est du périmètre de l'appelant pour cette permission.

    ⚠️ Chaque route appelle **aussi** `exiger(...)` elle-même, en première ligne : le
    contrôle de surface des permissions lit le corps de la route, et un contrôle
    caché dans une fonction d'aide y serait invisible. Il l'a signalé.
    """
    demande = depot_demandes().par_identifiant(identifiant)
    if demande is None:
        raise HTTPException(status_code=404, detail=f"demande {identifiant} introuvable.")
    exiger_dossier(
        acces, permission, demande.entreprise, introuvable=f"demande {identifiant} introuvable."
    )
    return demande


class Satisfaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    piece: str = Field(min_length=1)


@routeur.post("/demandes/{identifiant}/satisfaction", summary="Rattacher la pièce reçue")
def satisfaire(acces: AccesRequis, identifiant: str, corps: Satisfaction) -> DemandePiece:
    exiger(acces, Permission.IDENTIFIER_PIECE)
    _demande_du_perimetre(acces, identifiant, Permission.IDENTIFIER_PIECE)
    try:
        return satisfaire_une_demande(
            identifiant, piece=corps.piece, le=maintenant().date(),
            pieces=depot_pieces(), demandes=depot_demandes(),
        )
    except DemandeIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


class Classement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10)


@routeur.post("/demandes/{identifiant}/classement", summary="Classer une demande sans suite")
def classer(acces: AccesRequis, identifiant: str, corps: Classement) -> DemandePiece:
    exiger(acces, Permission.RELANCER_ADHERENT)
    _demande_du_perimetre(acces, identifiant, Permission.RELANCER_ADHERENT)
    try:
        return classer_une_demande(
            identifiant, motif=corps.motif, le=maintenant().date(), demandes=depot_demandes()
        )
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


class RelanceTracee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canal: CanalDepot


@routeur.post(
    "/demandes/{identifiant}/relances",
    summary="Tracer une relance émise",
    status_code=201,
    description=(
        "Consigne qu'une relance a été émise ce jour, par ce canal. Tracer n'est pas "
        "émettre : le collaborateur a appelé, écrit ou envoyé le message."
    ),
)
def tracer_relance(acces: AccesRequis, identifiant: str, corps: RelanceTracee) -> DemandePiece:
    exiger(acces, Permission.RELANCER_ADHERENT)
    _demande_du_perimetre(acces, identifiant, Permission.RELANCER_ADHERENT)
    try:
        return tracer_une_relance(
            identifiant, canal=corps.canal, le=maintenant().date(), demandes=depot_demandes()
        )
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


@routeur.get(
    "/relances",
    summary="Les relances à émettre aujourd'hui",
    description=(
        "Les jalons J+7, J+15 et J+30 après la demande. Au-delà de trente jours, ce "
        "n'est plus un oubli : la relance devient une escalade, affaire d'un "
        "responsable de portefeuille et non plus d'un automate."
    ),
)
def lister_relances(
    acces: AccesRequis, a_la_date: date = Query(...)
) -> list[RelancePiece]:
    exiger(acces, Permission.RELANCER_ADHERENT)
    return restreindre(acces, _relances(a_la_date), lambda r: r.entreprise)


def _relances(a_la_date: date) -> list[RelancePiece]:
    return relances_du_jour(depot_demandes().ouvertes(), a_la_date)


# ── L'espace de l'adhérent (pas 112) ──────────────────────────────────────────


def reglages_des_reponses() -> ReglagesDesReponses:
    """Relu à chaque appel : un libellé corrigé au référentiel vaut à la requête suivante."""
    return charger_les_reponses_de_l_adherent(configuration().dossier_referentiel)


@routeur.get("/reponses-possibles", summary="Les réponses toutes faites de l'adhérent au cabinet")
def lire_les_reponses_possibles(acces: AccesRequis) -> ReglagesDesReponses:
    exiger(acces, Permission.LIRE_PIECE)
    return reglages_des_reponses()


class ReponseAuCabinet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nature: NatureDeReponse
    message: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _un_message_dit_quelque_chose(self) -> ReponseAuCabinet:
        # Refusé dès la requête (422) : c'est une saisie incomplète, pas un conflit avec la demande.
        if self.nature is NatureDeReponse.MESSAGE and not (self.message or "").strip():
            raise ValueError("un message au cabinet ne peut pas être vide.")
        return self


@routeur.post(
    "/demandes/{identifiant}/reponse",
    summary="L'adhérent répond à une demande du cabinet",
    status_code=201,
    responses={
        404: {"description": "Demande inconnue ou hors du dossier de l'adhérent"},
        409: {"description": "Demande qui n'attend plus rien, ou réponse déjà reçue aujourd'hui"},
    },
)
def repondre_au_cabinet(
    acces: AccesRequis, identifiant: str, corps: ReponseAuCabinet
) -> DemandePiece:
    exiger(acces, Permission.REPONDRE_AU_CABINET)
    demande = _demande_du_perimetre(acces, identifiant, Permission.REPONDRE_AU_CABINET)
    horodatage = maintenant()
    try:
        repondue = repondre_a_une_demande(
            identifiant,
            entreprise=demande.entreprise,
            nature=corps.nature,
            message=corps.message,
            le=horodatage,
            par=acces.compte,
            jours_si_plus_tard=reglages_des_reponses().jours_si_plus_tard,
            demandes=depot_demandes(),
        )
    except DemandeIntrouvable as absente:
        raise HTTPException(status_code=404, detail=str(absente)) from absente
    except DemandeRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    reponse = repondue.derniere_reponse
    assert reponse is not None  # `repondre` vient de l'ajouter
    texte = texte_de_la_reponse(reponse, reglages_des_reponses())
    atelier().journal.ajouter(
        horodatage=horodatage,
        acteur=acces.compte,
        action="collecte.demande_repondue",
        objet_type="demande_piece",
        objet_id=repondue.identifiant,
        apres={
            "dossier": repondue.entreprise,
            "demande": repondue.identifiant,
            # « piece », pas « motif » : un gabarit d'avis ne lit jamais une clé `motif` (souvent
            # une note interne, voir transverse/domaine/notifications.py). Ici, c'est la pièce
            # attendue telle que l'adhérent l'a lue.
            "piece": repondue.motif,
            "nature": reponse.nature.value,
            "texte": texte,
            "par": acces.nom_complet,
        },
    )
    return repondue


@routeur.get(
    "/dossiers/{entreprise}/justificatifs",
    summary="Les justificatifs d'un mois, tels que l'adhérent les lit",
    description=(
        "Quatre statuts lus par l'adhérent (reçu, enregistré, classé, à corriger), et non les "
        "cinq états du traitement. Le compte par statut porte sur le mois entier, avant filtre."
    ),
)
def lire_les_justificatifs(
    acces: AccesRequis,
    entreprise: str,
    mois: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    statut: StatutPourLAdherent | None = Query(None),
    recherche: str | None = Query(None, max_length=80),
) -> VueDesJustificatifs:
    exiger_dossier(acces, Permission.LIRE_PIECE, entreprise)
    return vue_des_justificatifs(
        pieces=depot_pieces().du_dossier(entreprise),
        demandes_ouvertes=depot_demandes().ouvertes(entreprise),
        # Sans mois demandé : le mois en cours, celui où arrivent les factures du jour.
        mois=mois or f"{maintenant():%Y-%m}",
        statut=statut,
        recherche=recherche,
    )


@routeur.get(
    "/completude/{entreprise}",
    summary="L'état de la collecte d'un dossier sur une période",
    description=(
        "⚠️ Ce n'est **pas** un taux d'exhaustivité. Le système connaît les pièces "
        "reçues et les demandes émises ; il ignore les factures que l'adhérent n'a "
        "mentionnées à personne. Un dossier « à jour » n'est pas un dossier complet."
    ),
)
def lire_completude(
    acces: AccesRequis,
    entreprise: str,
    periode_debut: date = Query(...),
    periode_fin: date = Query(...),
    a_la_date: date = Query(...),
) -> CompletudeDossier:
    exiger_dossier(acces, Permission.LIRE_PIECE, entreprise)
    return evaluer_completude(
        entreprise,
        depot_pieces().du_dossier(entreprise),
        depot_demandes().toutes(entreprise),
        periode_debut=periode_debut,
        periode_fin=periode_fin,
        a_la_date=a_la_date,
    )


__all__ = ["routeur"]


def magasin_fichiers() -> MagasinFichiersLocal:
    """Le magasin en vigueur.

    ⚠️ Pas de variante en mémoire, contrairement aux dépôts. Un magasin en
    mémoire perdrait les justificatifs au redémarrage sans que rien ne le
    signale : la pièce resterait en base, son document aurait disparu, et
    l'incohérence ne se découvrirait qu'au moment de produire la facture devant
    un vérificateur. Écrire sur disque même en développement est plus sûr et ne
    coûte rien.

    ⚠️ Le locataire est celui par défaut, comme partout ailleurs dans ce
    contexte. Il deviendra celui de la session le jour où le cabinet en aura
    plusieurs — le magasin le prend déjà en paramètre, précisément pour que ce
    jour-là rien d'autre ne change.
    """
    return MagasinFichiersLocal(
        Path(configuration().dossier_fichiers), LOCATAIRE_PAR_DEFAUT
    )


def depot_pieces() -> DepotPieces:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    La bascule est ici et non dans chaque route : une route qui choisirait son
    dépôt le choisirait un jour mal, et l'incohérence — une lecture en base, une
    écriture en mémoire — ne se verrait qu'au redémarrage.
    """
    session = session_de_travail()
    if session is None:
        return _pieces_memoire()
    return DepotPiecesSql(session, courant())


def depot_demandes() -> DepotDemandes:
    """Le dépôt en vigueur — SQL dans une requête, mémoire sinon.

    La bascule est ici et non dans chaque route : une route qui choisirait son
    dépôt le choisirait un jour mal, et l'incohérence — une lecture en base, une
    écriture en mémoire — ne se verrait qu'au redémarrage.
    """
    session = session_de_travail()
    if session is None:
        return _demandes_memoire()
    return DepotDemandesSql(session, courant())


# ── La recherche globale (pas 93) ────────────────────────────────────────────


def _lien_de_la_piece(piece) -> str:
    """L'écran qui ouvre la pièce.

    ⚠️ L'écran de détail (E02) est désigné par la **référence de la facture**, et ne
    sait aujourd'hui contrôler que les factures du jeu de démonstration. Un lien vers
    une référence qu'il ne connaît pas ouvrirait « Pièce introuvable » : on renvoie alors
    à la boîte de réception, qui liste toutes les pièces. Le jour où E02 lira les pièces
    réelles, cette fonction seule changera.
    """
    from app.contextes.conformite.api import FACTURES_DEMO

    if piece.reference_document and piece.reference_document in FACTURES_DEMO:
        return f"/pieces/{piece.reference_document}"
    return "/pieces"


@routeur.get(
    "/recherche",
    summary="Chercher une pièce",
    responses={422: {"description": "Requête trop courte pour les réglages de recherche"}},
)
def chercher_une_piece(
    acces: AccesRequis,
    q: str = Query(
        min_length=1, max_length=100, description="Identifiant, n° de facture, émetteur…"
    ),
) -> ReponseDeRecherche:
    """Par identifiant de pièce ou numéro du document (identifiants), par émetteur ou nom
    de fichier (libellés), **dans le périmètre de l'appelant** : l'adhérent ne trouve que
    ses pièces, le comptable que celles de son portefeuille.
    """
    exiger(acces, Permission.LIRE_PIECE)
    reglages = charger_les_reglages_de_recherche(configuration().dossier_referentiel)
    try:
        verifier_la_requete(q, reglages)
    except RequeteTropCourte as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    resultats = []
    for piece in restreindre(acces, depot_pieces().toutes(), lambda p: p.entreprise):
        score = pertinence(
            q,
            identifiants=(piece.identifiant, piece.reference_document),
            libelles=(piece.emetteur, piece.nom_fichier),
            longueur_minimale=reglages.longueur_minimale,
        )
        if score is None:
            continue
        details = [piece.emetteur, piece.type.value, piece.etat.value]
        if piece.montant_ttc is not None:
            details.append(montant_fcfa(piece.montant_ttc))
        resultats.append(
            ResultatDeRecherche(
                nature="Pièce",
                identifiant=piece.identifiant,
                titre=piece.reference_document or piece.nom_fichier or piece.identifiant,
                detail=" · ".join(d for d in details if d),
                dossier=piece.entreprise,
                lien=_lien_de_la_piece(piece),
                pertinence=score,
            )
        )
    return classer_les_resultats("collecte", resultats, reglages)
