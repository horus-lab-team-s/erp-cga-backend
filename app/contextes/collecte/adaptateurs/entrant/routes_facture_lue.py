"""La facture lue et sa lecture automatique (16 octobre 2026).

Extrait de `routes_http.py`, qui dépassait 1 600 lignes. Les routes gardent leurs chemins
(`/collecte/...`) ; le routeur est monté à côté de celui de la collecte. La lecture par
Claude reste optionnelle : sans clé, la facture se saisit à la main.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.collecte.adaptateurs.entrant.routes_http import (
    DemandeDeRectificative,
    RectificativeDemandee,
    demande_du_perimetre,
    depot_demandes,
    depot_pieces,
    magasin_fichiers,
    prevenir_les_adherents_de_la_demande,
)
from app.contextes.collecte.adaptateurs.sortant.magasin_local import (
    CleInvalide,
    FichierAbsent,
)
from app.contextes.collecte.api import (
    CanalDepot,
    DemandePiece,
    EtatPiece,
    PieceJustificative,
    RelancePiece,
    TransitionRefusee,
    TypePiece,
    relances_du_jour,
)
from app.contextes.collecte.application.demandes_de_piece import (
    DemandeIntrouvable,
    DemandeRefusee,
    classer_une_demande,
    demander_une_rectificative,
    satisfaire_une_demande,
    tracer_une_relance,
)
from app.contextes.collecte.application.lecture import lire_une_piece
from app.contextes.collecte.application.traitement import classer_une_piece
from app.contextes.collecte.domaine.facture_lue import FactureLue, LigneLue, SourceDeLecture
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    restreindre,
)
from app.infrastructure.config import configuration
from app.partage import horloge
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, maintenant

routeur = APIRouter(prefix="/collecte", tags=["Collecte"])


# ── La facture lue, et sa lecture automatique (14 octobre 2026) ──────────────
#
# « Une facture chargée doit vite devenir une écriture. » La pièce ne portait que son
# type, sa référence, sa date, son TTC et son émetteur : rien dont on pût tirer une
# écriture. Elle porte désormais la facture lue et confirmée (`FactureLue`), d'où la
# comptabilité propose l'écriture. La lecture automatique, quand elle est configurée,
# préremplit ; un collaborateur confirme toujours.


def service_de_lecture():
    """Le moteur de lecture automatique, ou `None` s'il n'est pas configuré.

    ⚠️ `None` sans clé, et c'est voulu : aucun document ne sort du serveur tant que le
    cabinet n'a pas posé `CGA_ANTHROPIC_CLE_API`. Les essais remplacent cette fonction.
    """
    reglages = configuration()
    if not reglages.anthropic_cle_api:
        return None
    from app.contextes.collecte.adaptateurs.sortant.extraction_claude import ExtractionClaude

    return ExtractionClaude(cle_api=reglages.anthropic_cle_api, modele=reglages.lecture_modele)


class EtatDeLaLecture(BaseModel):
    disponible: bool


@routeur.get("/lecture-automatique", summary="La lecture automatique est-elle configurée ?")
def lecture_automatique_disponible(acces: AccesRequis) -> EtatDeLaLecture:
    exiger(acces, Permission.IDENTIFIER_PIECE)
    return EtatDeLaLecture(disponible=bool(configuration().anthropic_cle_api))


class ValeurProposee(BaseModel):
    champ: str
    valeur: Any
    confiance: Decimal
    #: Le collaborateur doit la relire : confiance sous le seuil, ou champ qui engage
    #: la déclaration (montants, NIU, date) quel que soit le score.
    a_relire: bool


class LectureProposee(BaseModel):
    """Ce que la lecture a vu. **Rien n'est enregistré** : c'est une proposition."""

    moteur: str
    incident: str | None
    valeurs: list[ValeurProposee]


def _piece_de_mon_portefeuille(acces, identifiant: str) -> PieceJustificative:
    introuvable = f"pièce {identifiant} introuvable."
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=introuvable)
    exiger_dossier(acces, Permission.IDENTIFIER_PIECE, piece.entreprise, introuvable=introuvable)
    return piece


@routeur.post(
    "/pieces/{identifiant}/lecture-automatique",
    summary="Lire automatiquement le document d'une pièce, pour préremplir sa facture",
    responses={
        404: {"description": "Pièce inconnue, hors portefeuille, ou sans document"},
        503: {"description": "Lecture automatique non configurée"},
    },
)
def lire_automatiquement(acces: AccesRequis, identifiant: str) -> LectureProposee:
    """⚠️ Le document est envoyé à l'API d'Anthropic. Rien n'est écrit au dossier : les valeurs
    reviennent à l'écran, et c'est `POST /pieces/{id}/facture` qui enregistre ce que le
    collaborateur a confirmé."""
    exiger(acces, Permission.IDENTIFIER_PIECE)
    piece = _piece_de_mon_portefeuille(acces, identifiant)
    service = service_de_lecture()
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="La lecture automatique n'est pas configurée : saisissez la facture.",
        )
    if piece.empreinte is None:
        raise HTTPException(status_code=404, detail="Cette pièce n'a pas de document à lire.")
    try:
        contenu = magasin_fichiers().lire(piece.empreinte)
    except (CleInvalide, FichierAbsent, KeyError, FileNotFoundError) as absent:
        raise HTTPException(status_code=404, detail="Le document est introuvable.") from absent
    extraction = service.extraire(contenu, piece=piece.identifiant, type_attendu=piece.type)
    return LectureProposee(
        moteur=extraction.moteur,
        incident=extraction.incident,
        valeurs=[
            ValeurProposee(
                champ=v.champ,
                valeur=v.valeur_lue,
                confiance=v.confiance,
                a_relire=not v.acceptable_automatiquement,
            )
            for v in extraction.champs.values()
        ],
    )


class LigneSaisie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    designation: str = Field(min_length=1, max_length=300)
    montant_ht: Decimal = Field(gt=0)


class FactureSaisie(BaseModel):
    """La facture, telle que le collaborateur l'a confirmée."""

    model_config = ConfigDict(extra="forbid")

    emetteur: str = Field(min_length=1, max_length=200)
    niu_emetteur: str | None = Field(default=None, max_length=32)
    #: « J'ai vérifié sur le portail de la DGI que ce NIU est actif. » Voir `FactureLue`.
    niu_actif: bool | None = None
    reference: str = Field(min_length=1, max_length=64)
    date_emission: date
    lignes: list[LigneSaisie] = Field(default_factory=list, max_length=200)
    montant_ht: Decimal = Field(gt=0)
    montant_tva: Decimal = Field(ge=0)
    montant_ttc: Decimal = Field(gt=0)
    source: SourceDeLecture = SourceDeLecture.SAISIE


@routeur.post(
    "/pieces/{identifiant}/facture",
    summary="Enregistrer la facture lue et confirmée d'une pièce",
    responses={
        404: {"description": "Pièce inconnue, ou hors du portefeuille"},
        409: {"description": "Pièce déjà comptabilisée ou archivée"},
        422: {"description": "Les montants ne se recoupent pas"},
    },
)
def enregistrer_la_facture(
    acces: AccesRequis, identifiant: str, saisie: FactureSaisie
) -> PieceJustificative:
    """La pièce devient une facture d'achat lue, prête à proposer son écriture.

    ⚠️ PLUS RIEN NE CHANGE APRÈS L'ÉCRITURE. Une pièce comptabilisée porte la facture
    d'où l'écriture est sortie ; la retoucher ferait mentir le lien entre les deux. Une
    erreur se corrige par une contre-passation, comme toute écriture validée.
    """
    exiger(acces, Permission.IDENTIFIER_PIECE)
    piece = _piece_de_mon_portefeuille(acces, identifiant)
    if piece.etat in (EtatPiece.COMPTABILISEE, EtatPiece.ARCHIVEE):
        raise HTTPException(
            status_code=409,
            detail=(
                f"pièce {identifiant} {piece.etat.value.lower()} : "
                "sa facture ne se modifie plus."
            ),
        )
    try:
        facture = FactureLue(
            emetteur=saisie.emetteur.strip(),
            niu_emetteur=(saisie.niu_emetteur or "").replace(" ", "").upper() or None,
            niu_actif=saisie.niu_actif,
            reference=saisie.reference.strip(),
            date_emission=saisie.date_emission,
            lignes=tuple(
                LigneLue(designation=ligne.designation.strip(), montant_ht=ligne.montant_ht)
                for ligne in saisie.lignes
            ),
            montant_ht=saisie.montant_ht,
            montant_tva=saisie.montant_tva,
            montant_ttc=saisie.montant_ttc,
            source=saisie.source,
            lue_par=acces.compte,
            lue_le=maintenant(),
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    pieces = depot_pieces()
    if piece.etat is EtatPiece.RECUE:
        try:
            piece = lire_une_piece(
                piece,
                type=TypePiece.FACTURE_ACHAT,
                reference_document=facture.reference,
                date_document=facture.date_emission,
                montant_ttc=facture.montant_ttc,
                emetteur=facture.emetteur,
                pieces=pieces,
            )
        except TransitionRefusee as refus:
            raise HTTPException(status_code=409, detail=str(refus)) from refus
    else:
        piece = piece.model_copy(
            update={
                "reference_document": facture.reference,
                "date_document": facture.date_emission,
                "montant_ttc": facture.montant_ttc,
                "emetteur": facture.emetteur,
            }
        )
    avec = piece.model_copy(update={"facture": facture})
    pieces.enregistrer(avec)
    return avec


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
    aujourd_hui = horloge.aujourd_hui()
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
        adherents_prevenus=prevenir_les_adherents_de_la_demande(emise, piece.reference_document),
    )




class Satisfaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    piece: str = Field(min_length=1)


@routeur.post("/demandes/{identifiant}/satisfaction", summary="Rattacher la pièce reçue")
def satisfaire(acces: AccesRequis, identifiant: str, corps: Satisfaction) -> DemandePiece:
    exiger(acces, Permission.IDENTIFIER_PIECE)
    demande_du_perimetre(acces, identifiant, Permission.IDENTIFIER_PIECE)
    try:
        return satisfaire_une_demande(
            identifiant, piece=corps.piece, le=aujourd_hui(),
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
    demande_du_perimetre(acces, identifiant, Permission.RELANCER_ADHERENT)
    try:
        return classer_une_demande(
            identifiant, motif=corps.motif, le=aujourd_hui(), demandes=depot_demandes()
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
    demande_du_perimetre(acces, identifiant, Permission.RELANCER_ADHERENT)
    try:
        return tracer_une_relance(
            identifiant, canal=corps.canal, le=aujourd_hui(), demandes=depot_demandes()
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


