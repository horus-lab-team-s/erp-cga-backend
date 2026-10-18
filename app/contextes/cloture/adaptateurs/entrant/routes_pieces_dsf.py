"""Les pièces à joindre à la DSF : la liste du guide de la DGI, et où en est chacune
(15 octobre 2026). Voir `domaine/pieces_dsf.py`.

L'état d'une pièce n'a pas de table : chaque geste (verser, déclarer sans objet,
retirer) est une entrée du journal d'audit, et la dernière entrée d'une pièce fait foi.
Le fichier versé va au magasin, par son empreinte, comme les documents des missions.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.contextes.cloture.adaptateurs.entrant.routes_http import depot_dossiers
from app.contextes.cloture.application.liasse import determiner_le_systeme
from app.contextes.cloture.domaine.entites import SystemeDsf
from app.contextes.cloture.domaine.pieces_dsf import (
    PIECES_DSF,
    EtatPiece,
    Marque,
    etat_des_pieces,
)
from app.contextes.portefeuille.api import EntrepriseIntrouvable
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger_dossier
from app.infrastructure.config import configuration
from app.partage.en_tetes import disposition_en_ligne
from app.partage.horloge import maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import FichierAbsent, MagasinFichiersLocal, detecter_le_type

routeur = APIRouter(prefix="/cloture", tags=["Clôture et DSF"])

OBJET_PIECE = "piece_dsf"
_ACTION = "cloture.piece_dsf"
#: Le portail de la DGI accepte 15 Mo par pièce (guide DSF 2025, § 13).
_TAILLE_MAXIMALE = 15 * 1024 * 1024
#: Un PDF ou un scan : le magasin refuse les documents bureautiques (voir son en-tête).
_ACCEPTES = {"application/pdf", "image/jpeg", "image/png", "image/tiff"}


class PieceLue(BaseModel):
    numero: int
    libelle: str
    page: str
    etat: EtatPiece
    #: Pour une pièce PRODUITE : ce que la console télécharge (« balance-clients »…).
    produite: str | None
    motif: str | None
    nom_du_fichier: str | None
    par: str | None


class PiecesDeLaDsf(BaseModel):
    systeme: SystemeDsf
    pieces: list[PieceLue]
    a_fournir: int
    #: Toutes les pièces sont là : le bouton « Soumettre » du portail s'activera.
    complet: bool


class SansObjet(BaseModel):
    motif: str = Field(min_length=15, max_length=500)


def _objet(entreprise: str, exercice: str) -> str:
    return f"{entreprise}/{exercice}"


def _systeme(entreprise: str, exercice: str) -> SystemeDsf:
    try:
        dossier = depot_dossiers().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    periode = next((e for e in dossier.exercices if e.libelle == exercice), None)
    if periode is None:
        raise HTTPException(status_code=404, detail=f"l'exercice {exercice} n'existe pas.")
    return determiner_le_systeme(dossier.regime_au(periode.cloture))[0]


def _marques(entreprise: str, exercice: str) -> dict[int, Marque]:
    marques: dict[int, Marque] = {}
    for e in atelier().journal.lister(
        objet_type=OBJET_PIECE, objet_id=_objet(entreprise, exercice)
    ):
        if e.action != _ACTION:
            continue
        marques[int(e.apres["numero"])] = Marque(
            etat=EtatPiece(e.apres["etat"]),
            par=e.acteur,
            le=e.horodatage,
            motif=e.apres.get("motif"),
            nom_du_fichier=e.apres.get("nom_du_fichier"),
            empreinte=e.apres.get("document"),
        )
    return marques


def _piece(numero: int, systeme: SystemeDsf):
    piece = next((p for p in PIECES_DSF if p.numero == numero), None)
    page = (
        None
        if piece is None
        else (piece.page_normal if systeme is SystemeDsf.NORMAL else piece.page_smt)
    )
    if piece is None or page is None:
        raise HTTPException(
            status_code=404, detail=f"la DSF {systeme.value} ne demande pas la pièce {numero}."
        )
    if piece.produite:
        raise HTTPException(
            status_code=409, detail="la plateforme produit cette pièce : rien à verser."
        )
    return piece


def lignes_des_pieces_dsf(entreprise: str, exercice: str):
    """Les pièces de la DSF et leur état : ce que l'agent de relecture lit (point 24)."""
    return etat_des_pieces(_systeme(entreprise, exercice), _marques(entreprise, exercice))


def _marquer(acteur: str, entreprise: str, exercice: str, numero: int, **apres) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acteur,
        action=_ACTION,
        objet_type=OBJET_PIECE,
        objet_id=_objet(entreprise, exercice),
        apres={"numero": numero, **apres},
    )


def _magasin() -> MagasinFichiersLocal:
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


@routeur.get(
    "/dossiers/{entreprise}/dsf/{exercice}/pieces-jointes",
    summary="Les pièces à joindre à la DSF selon le guide de la DGI, et ce qui manque",
)
def lire_les_pieces(acces: AccesRequis, entreprise: str, exercice: str) -> PiecesDeLaDsf:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    systeme = _systeme(entreprise, exercice)
    lignes = etat_des_pieces(systeme, _marques(entreprise, exercice))
    a_fournir = sum(1 for x in lignes if x.etat is EtatPiece.A_FOURNIR)
    return PiecesDeLaDsf(
        systeme=systeme,
        a_fournir=a_fournir,
        complet=a_fournir == 0,
        pieces=[
            PieceLue(
                numero=x.piece.numero,
                libelle=x.piece.libelle,
                page=x.page,
                etat=x.etat,
                produite=x.piece.produite,
                motif=x.marque.motif if x.marque and x.etat is EtatPiece.NEANT else None,
                nom_du_fichier=(
                    x.marque.nom_du_fichier if x.marque and x.etat is EtatPiece.JOINTE else None
                ),
                par=x.marque.par if x.marque and x.etat is not EtatPiece.A_FOURNIR else None,
            )
            for x in lignes
        ],
    )


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/pieces-jointes/{numero}/fichier",
    summary="Verser une pièce (PDF ou scan, 15 Mo au plus)",
    responses={413: {"description": "Plus de 15 Mo"}, 415: {"description": "Ni PDF ni image"}},
)
async def verser_une_piece(
    acces: AccesRequis, entreprise: str, exercice: str, numero: int, fichier: UploadFile = File(...)
) -> PiecesDeLaDsf:
    exiger_dossier(acces, Permission.PREPARER_DSF, entreprise)
    contenu = await fichier.read(_TAILLE_MAXIMALE + 1)
    # ⚠️ Le rangement ET la relecture dans le fil : la relecture lit le dossier et le
    # journal, et rien de bloquant ne reste sur la boucle (voir test_boucle_non_bloquee).
    return await run_in_threadpool(
        _verser_puis_relire, acces, entreprise, exercice, numero, contenu, fichier.filename
    )


def _verser_puis_relire(acces, entreprise, exercice, numero, contenu, nom) -> PiecesDeLaDsf:
    _verser(acces.compte, entreprise, exercice, numero, contenu, nom)
    return lire_les_pieces(acces, entreprise, exercice)


def _verser(acteur, entreprise, exercice, numero, contenu: bytes, nom: str | None) -> None:
    _piece(numero, _systeme(entreprise, exercice))
    if len(contenu) > _TAILLE_MAXIMALE:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux : 15 Mo au plus.")
    type_mime = detecter_le_type(contenu)
    if type_mime not in _ACCEPTES:
        raise HTTPException(status_code=415, detail="Seuls un PDF ou un scan se versent.")
    empreinte = hashlib.sha256(contenu).hexdigest()
    _magasin().deposer(empreinte, contenu, type_mime=type_mime)
    _marquer(
        acteur,
        entreprise,
        exercice,
        numero,
        etat=EtatPiece.JOINTE.value,
        # ⚠️ Sous « document », pas « empreinte » : le journal expurge toute clé de ce nom
        # (celle des mots de passe), et la pièce devenait introuvable (constaté le 15
        # octobre 2026). Les missions rangent leur document de la même façon.
        document=empreinte,
        nom_du_fichier=(nom or f"piece-{numero}")[:200],
    )


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/pieces-jointes/{numero}/sans-objet",
    summary="Déclarer une pièce sans objet pour ce dossier, avec le motif",
)
def declarer_sans_objet(
    acces: AccesRequis, entreprise: str, exercice: str, numero: int, demande: SansObjet
) -> PiecesDeLaDsf:
    exiger_dossier(acces, Permission.PREPARER_DSF, entreprise)
    _piece(numero, _systeme(entreprise, exercice))
    _marquer(
        acces.compte, entreprise, exercice, numero, etat=EtatPiece.NEANT.value, motif=demande.motif
    )
    return lire_les_pieces(acces, entreprise, exercice)


@routeur.post(
    "/dossiers/{entreprise}/dsf/{exercice}/pieces-jointes/{numero}/retrait",
    summary="Remettre une pièce à fournir (fichier erroné, motif revu)",
)
def retirer_une_piece(
    acces: AccesRequis, entreprise: str, exercice: str, numero: int
) -> PiecesDeLaDsf:
    exiger_dossier(acces, Permission.PREPARER_DSF, entreprise)
    _piece(numero, _systeme(entreprise, exercice))
    _marquer(acces.compte, entreprise, exercice, numero, etat=EtatPiece.A_FOURNIR.value)
    return lire_les_pieces(acces, entreprise, exercice)


@routeur.get(
    "/dossiers/{entreprise}/dsf/{exercice}/pieces-jointes/{numero}/fichier",
    summary="Relire la pièce versée",
    response_class=Response,
)
def lire_une_piece(acces: AccesRequis, entreprise: str, exercice: str, numero: int) -> Response:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    marque = _marques(entreprise, exercice).get(numero)
    if marque is None or marque.etat is not EtatPiece.JOINTE or not marque.empreinte:
        raise HTTPException(status_code=404, detail="aucune pièce versée sous ce numéro.")
    try:
        contenu = _magasin().lire(marque.empreinte)
    except FichierAbsent as absent:
        raise HTTPException(status_code=404, detail="pièce introuvable au magasin.") from absent
    return Response(
        content=contenu,
        media_type=detecter_le_type(contenu) or "application/octet-stream",
        headers={"Content-Disposition": disposition_en_ligne(marque.nom_du_fichier or "piece")},
    )


# ── Ma DSF : la déclaration vue de l'adhérent (18 octobre 2026, point 24) ─────────
#
# « Le module DSF en libre-service pour l'utilisateur en ligne, ou à la demande avec un
# espace de dépôt comme la création. » L'adhérent suit où en est la DSF de chaque exercice
# clos et verse les pièces que le guide de la DGI demande ; le cabinet les retrouve dans
# sa liste, versées sous le compte de l'adhérent.
#
# ⚠️ CE QUE L'ADHÉRENT NE LIT PAS : ni les chiffres d'une liasse avant son dépôt (voir
# `PERMISSIONS_PAR_ROLE`, l'adhérent ne lit pas une comptabilité en cours), ni le nom de
# qui prépare, valide ou dépose (point 21). La copie déposée se télécharge, ses visas au
# nom du cabinet.
#
# ⚠️ CE QUE L'ADHÉRENT NE FAIT PAS : déclarer une pièce sans objet ou retirer celle que le
# cabinet a versée. Il remplace la sienne tant que la DSF n'est pas déposée.

#: Les exercices montrés : les derniers clos, le plus récent d'abord.
_EXERCICES_MONTRES = 3


class EtatDeMaDsf(StrEnum):
    EN_PREPARATION = "EN_PREPARATION"
    PREPAREE = "PREPAREE"
    VALIDEE = "VALIDEE"
    DEPOSEE = "DEPOSEE"


class MaPieceDsf(BaseModel):
    numero: int
    libelle: str
    etat: EtatPiece
    nom_du_fichier: str | None
    versable: bool


class MaDsfDeLExercice(BaseModel):
    exercice: str
    cloture: date
    etat: EtatDeMaDsf
    numero_accuse: str | None
    deposee_le: datetime | None
    document: bool
    a_fournir: int
    pieces: list[MaPieceDsf]


class MaDsf(BaseModel):
    exercices: list[MaDsfDeLExercice]


def _declarations(entreprise: str, exercice: str):
    from app.contextes.cloture.adaptateurs.sortant.depots_dsf import DepotDSFSql, dsf_en_memoire
    from app.contextes.transverse.api import session_de_travail

    session = session_de_travail()
    depot = dsf_en_memoire() if session is None else DepotDSFSql(session, courant())
    return depot.de_l_exercice(entreprise, exercice)


def _la_deposee_ou_la_derniere(entreprise: str, exercice: str):
    from app.contextes.cloture.domaine.declaration_dsf import EtatDSF

    versions = _declarations(entreprise, exercice)
    deposee = next((d for d in versions if d.etat is EtatDSF.DEPOSEE), None)
    return deposee or max(versions, key=lambda d: d.version, default=None)


def _exercices_clos(entreprise: str):
    try:
        dossier = depot_dossiers().lire(entreprise)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    aujourd_hui = maintenant().date()
    clos = [e for e in dossier.exercices if e.clos or e.cloture < aujourd_hui]
    return sorted(clos, key=lambda e: e.cloture, reverse=True)[:_EXERCICES_MONTRES]


def _ma_dsf_de(compte: str, entreprise: str, periode) -> MaDsfDeLExercice:
    dsf = _la_deposee_ou_la_derniere(entreprise, periode.libelle)
    etat = EtatDeMaDsf.EN_PREPARATION if dsf is None else EtatDeMaDsf(dsf.etat.value)
    deposee = etat is EtatDeMaDsf.DEPOSEE
    lignes = etat_des_pieces(
        _systeme(entreprise, periode.libelle), _marques(entreprise, periode.libelle)
    )
    return MaDsfDeLExercice(
        exercice=periode.libelle,
        cloture=periode.cloture,
        etat=etat,
        numero_accuse=dsf.numero_accuse if deposee else None,
        deposee_le=dsf.deposee_le if deposee else None,
        document=deposee,
        a_fournir=sum(1 for x in lignes if x.etat is EtatPiece.A_FOURNIR),
        pieces=[
            MaPieceDsf(
                numero=x.piece.numero,
                libelle=x.piece.libelle,
                etat=x.etat,
                nom_du_fichier=(
                    x.marque.nom_du_fichier if x.marque and x.etat is EtatPiece.JOINTE else None
                ),
                versable=not deposee and _versable_par(compte, x),
            )
            for x in lignes
        ],
    )


def _versable_par(compte: str, ligne) -> bool:
    if ligne.etat is EtatPiece.A_FOURNIR:
        return True
    return (
        ligne.etat is EtatPiece.JOINTE and ligne.marque is not None and ligne.marque.par == compte
    )


@routeur.get(
    "/dossiers/{entreprise}/ma-dsf",
    summary="Ma DSF : où en est chaque exercice clos, et les pièces attendues (adhérent)",
)
def lire_ma_dsf(acces: AccesRequis, entreprise: str) -> MaDsf:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, entreprise)
    return MaDsf(
        exercices=[_ma_dsf_de(acces.compte, entreprise, e) for e in _exercices_clos(entreprise)]
    )


@routeur.post(
    "/dossiers/{entreprise}/ma-dsf/{exercice}/pieces/{numero}",
    summary="Verser une pièce de ma DSF (PDF ou scan, 15 Mo au plus)",
    responses={
        409: {"description": "DSF déposée, ou pièce déjà traitée par le cabinet"},
        413: {"description": "Plus de 15 Mo"},
        415: {"description": "Ni PDF ni image"},
    },
)
async def verser_ma_piece(
    acces: AccesRequis, entreprise: str, exercice: str, numero: int, fichier: UploadFile = File(...)
) -> MaDsf:
    exiger_dossier(acces, Permission.DEPOSER_PIECE, entreprise)
    contenu = await fichier.read(_TAILLE_MAXIMALE + 1)
    return await run_in_threadpool(
        _verser_ma_piece_puis_relire, acces, entreprise, exercice, numero, contenu, fichier.filename
    )


def _verser_ma_piece_puis_relire(acces, entreprise, exercice, numero, contenu, nom) -> MaDsf:
    periode = next((e for e in _exercices_clos(entreprise) if e.libelle == exercice), None)
    if periode is None:
        raise HTTPException(
            status_code=404, detail=f"l'exercice {exercice} n'attend pas encore sa DSF."
        )
    vue = _ma_dsf_de(acces.compte, entreprise, periode)
    if vue.etat is EtatDeMaDsf.DEPOSEE:
        raise HTTPException(status_code=409, detail="la DSF de cet exercice est déjà déposée.")
    piece = next((p for p in vue.pieces if p.numero == numero), None)
    if piece is not None and not piece.versable and piece.etat is not EtatPiece.PRODUITE:
        raise HTTPException(
            status_code=409,
            detail="le cabinet a déjà traité cette pièce : écrivez-lui pour la changer.",
        )
    _verser(acces.compte, entreprise, exercice, numero, contenu, nom)
    return lire_ma_dsf(acces, entreprise)


@routeur.get(
    "/dossiers/{entreprise}/ma-dsf/{exercice}/document",
    summary="La copie de ma DSF déposée, visas au nom du cabinet",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def ma_dsf_deposee(acces: AccesRequis, entreprise: str, exercice: str) -> Response:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, entreprise)
    from app.contextes.cloture.adaptateurs.sortant.document_dsf import rendre_la_dsf
    from app.contextes.cloture.domaine.declaration_dsf import EtatDSF

    dsf = _la_deposee_ou_la_derniere(entreprise, exercice)
    if dsf is None or dsf.etat is not EtatDSF.DEPOSEE:
        raise HTTPException(status_code=404, detail=f"la DSF {exercice} n'est pas encore déposée.")
    return Response(
        content=rendre_la_dsf(dsf, pour_le_client=True),
        media_type="application/pdf",
        headers={"Content-Disposition": disposition_en_ligne(f"DSF-{entreprise}-{exercice}.pdf")},
    )
