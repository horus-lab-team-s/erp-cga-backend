"""Les pièces à joindre à la DSF : la liste du guide de la DGI, et où en est chacune
(15 octobre 2026). Voir `domaine/pieces_dsf.py`.

L'état d'une pièce n'a pas de table : chaque geste (verser, déclarer sans objet,
retirer) est une entrée du journal d'audit, et la dernière entrée d'une pièce fait foi.
Le fichier versé va au magasin, par son empreinte, comme les documents des missions.
"""

from __future__ import annotations

import hashlib
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
