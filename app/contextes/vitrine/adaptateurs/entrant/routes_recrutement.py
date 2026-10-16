"""Les offres de poste (16 octobre 2026) : le site les lit, le candidat postule, le cabinet
les rédige.

─────────────────────────────────────────────────────────────────────────────────
DEUX PUBLICS, DEUX JEUX DE ROUTES

* le site, sans authentification : les offres ouvertes, leur détail (les qualifications
  déjà découpées en blocs, voir `texte_riche.py`), et le dépôt d'une candidature ;
* le cabinet, sous `EDITER_VITRINE` (direction, administrateur) : rédiger, publier,
  clore, et lire les candidatures avec leur CV.

⚠️ LE CV : un PDF seulement, 5 Mo au plus, type vérifié sur le contenu et jamais sur
l'étiquette envoyée. Une même personne (même téléphone) ne postule qu'une fois à une
offre : un double envoi ne crée pas deux candidatures.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.contextes.vitrine.adaptateurs.sortant.depots_recrutement import (
    depot_candidatures,
    depot_offres,
)
from app.contextes.vitrine.domaine.recrutement import (
    Candidature,
    OffreDePoste,
    OffreRefusee,
    TypeDeContrat,
)
from app.contextes.vitrine.domaine.texte_riche import BlocDeTexte, en_blocs
from app.infrastructure.config import configuration
from app.partage.en_tetes import disposition_en_piece_jointe
from app.partage.horloge import aujourd_hui, maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import MagasinFichiersLocal, detecter_le_type

__all__ = ["routeur"]

routeur = APIRouter(prefix="/vitrine", tags=["Vitrine · recrutement"])

_TAILLE_MAXIMALE_CV = 5 * 1024 * 1024
_OBJET = "offre_de_poste"


def _magasin() -> MagasinFichiersLocal:
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


# ── Ce que le site lit ──────────────────────────────────────────────────────────


class OffrePublique(BaseModel):
    identifiant: str
    intitule: str
    lieu: str
    type_de_contrat: TypeDeContrat
    date_limite: date | None
    description: list[BlocDeTexte]
    qualifications: list[BlocDeTexte]


def _publique(o: OffreDePoste) -> OffrePublique:
    return OffrePublique(
        identifiant=o.identifiant,
        intitule=o.intitule,
        lieu=o.lieu,
        type_de_contrat=o.type_de_contrat,
        date_limite=o.date_limite,
        description=en_blocs(o.description),
        qualifications=en_blocs(o.qualifications),
    )


def _offre(identifiant: str) -> OffreDePoste:
    offre = depot_offres().lire(identifiant)
    if offre is None:
        raise HTTPException(status_code=404, detail=f"offre {identifiant} introuvable.")
    return offre


@routeur.get("/offres", summary="Les offres de poste ouvertes, pour le site")
def lister_les_offres_ouvertes() -> list[OffrePublique]:
    jour = aujourd_hui()
    return [_publique(o) for o in depot_offres().lister() if o.ouverte_au(jour)]


@routeur.get("/offres/{identifiant}", summary="Une offre ouverte, avec ses qualifications")
def lire_une_offre_ouverte(identifiant: str) -> OffrePublique:
    offre = _offre(identifiant)
    # Une offre en brouillon ou close n'existe pas pour le site : même réponse qu'absente.
    if not offre.ouverte_au(aujourd_hui()):
        raise HTTPException(status_code=404, detail=f"offre {identifiant} introuvable.")
    return _publique(offre)


class AccuseDeCandidature(BaseModel):
    recue: bool
    message: str


@routeur.post(
    "/offres/{identifiant}/candidatures",
    summary="Postuler : coordonnées, message et CV en PDF",
    status_code=201,
    responses={
        404: {"description": "Offre absente, close ou échue"},
        413: {"description": "CV de plus de 5 Mo"},
        415: {"description": "Le CV n'est pas un PDF"},
        422: {"description": "Coordonnées invalides"},
    },
)
async def postuler(
    identifiant: str,
    nom: str = Form(...),
    telephone: str = Form(...),
    courriel: str | None = Form(None),
    message: str = Form(""),
    cv: UploadFile = File(...),
) -> AccuseDeCandidature:
    # Lecture bornée, puis le reste hors de la boucle (voir la collecte).
    contenu = await cv.read(_TAILLE_MAXIMALE_CV + 1)
    return await run_in_threadpool(
        _postuler, identifiant, nom, telephone, courriel, message, contenu, cv.filename
    )


def _postuler(identifiant, nom, telephone, courriel, message, contenu, nom_du_fichier):
    offre = _offre(identifiant)
    if not offre.ouverte_au(aujourd_hui()):
        raise HTTPException(status_code=404, detail=f"offre {identifiant} introuvable.")
    if len(contenu) > _TAILLE_MAXIMALE_CV:
        raise HTTPException(status_code=413, detail="CV trop volumineux : 5 Mo au plus.")
    if detecter_le_type(contenu) != "application/pdf":
        raise HTTPException(status_code=415, detail="Le CV se dépose en PDF.")
    candidatures = depot_candidatures()
    tel = "".join(c for c in telephone if c.isdigit() or c == "+")
    deja = next((c for c in candidatures.de_l_offre(identifiant) if c.telephone == tel), None)
    if deja is not None:
        return AccuseDeCandidature(
            recue=True, message="Votre candidature à cette offre nous est déjà parvenue."
        )
    empreinte = hashlib.sha256(contenu).hexdigest()
    try:
        candidature = Candidature(
            identifiant=f"cand-{uuid.uuid4().hex[:16]}",
            offre=identifiant,
            nom=nom.strip(),
            telephone=tel,
            courriel=(courriel or "").strip() or None,
            message=message.strip(),
            cv_empreinte=empreinte,
            cv_nom=(nom_du_fichier or "cv.pdf")[:200],
            recue_le=maintenant(),
        )
    except ValidationError as invalide:
        raise HTTPException(status_code=422, detail=invalide.errors()[0]["msg"]) from invalide
    _magasin().deposer(empreinte, contenu, type_mime="application/pdf")
    candidatures.enregistrer(candidature)
    return AccuseDeCandidature(
        recue=True, message="Candidature reçue : le cabinet vous recontactera."
    )


# ── Ce que le cabinet rédige ────────────────────────────────────────────────────


class SaisieDOffre(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intitule: str = Field(min_length=3, max_length=160)
    lieu: str = Field(min_length=2, max_length=120)
    type_de_contrat: TypeDeContrat
    description: str = Field(default="", max_length=6000)
    qualifications: str = Field(min_length=10, max_length=8000)
    date_limite: date | None = None


class OffreDuCabinet(BaseModel):
    offre: OffreDePoste
    candidatures: int


def _du_cabinet(offre: OffreDePoste) -> OffreDuCabinet:
    return OffreDuCabinet(
        offre=offre, candidatures=len(depot_candidatures().de_l_offre(offre.identifiant))
    )


def _tracer(acces, action: str, offre: OffreDePoste) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=f"vitrine.{action}",
        objet_type=_OBJET,
        objet_id=offre.identifiant,
        apres={"etat": offre.etat.value, "intitule": offre.intitule},
    )


def _refus(geste):
    try:
        return geste()
    except OffreRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


@routeur.get("/cabinet/offres", summary="Toutes les offres du cabinet, brouillons compris")
def lister_les_offres_du_cabinet(acces: AccesRequis) -> list[OffreDuCabinet]:
    exiger(acces, Permission.EDITER_VITRINE)
    return [_du_cabinet(o) for o in depot_offres().lister()]


@routeur.post("/cabinet/offres", summary="Rédiger une offre (en brouillon)", status_code=201)
def rediger_une_offre(acces: AccesRequis, saisie: SaisieDOffre) -> OffreDuCabinet:
    exiger(acces, Permission.EDITER_VITRINE)
    offre = OffreDePoste(
        identifiant=f"offre-{uuid.uuid4().hex[:12]}",
        creee_le=maintenant(),
        creee_par=acces.compte,
        **saisie.model_dump(),
    )
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_redigee", offre)
    return _du_cabinet(offre)


@routeur.put("/cabinet/offres/{identifiant}", summary="Modifier une offre")
def modifier_une_offre(
    acces: AccesRequis, identifiant: str, saisie: SaisieDOffre
) -> OffreDuCabinet:
    exiger(acces, Permission.EDITER_VITRINE)
    offre = _refus(lambda: _offre(identifiant).modifier(**saisie.model_dump()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_modifiee", offre)
    return _du_cabinet(offre)


@routeur.post("/cabinet/offres/{identifiant}/publication", summary="Publier sur le site")
def publier_une_offre(acces: AccesRequis, identifiant: str) -> OffreDuCabinet:
    exiger(acces, Permission.EDITER_VITRINE)
    offre = _refus(lambda: _offre(identifiant).publier(maintenant(), jour=aujourd_hui()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_publiee", offre)
    return _du_cabinet(offre)


@routeur.post("/cabinet/offres/{identifiant}/cloture", summary="Clore : retirer du site")
def clore_une_offre(acces: AccesRequis, identifiant: str) -> OffreDuCabinet:
    exiger(acces, Permission.EDITER_VITRINE)
    offre = _refus(lambda: _offre(identifiant).clore(maintenant()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_close", offre)
    return _du_cabinet(offre)


@routeur.get(
    "/cabinet/offres/{identifiant}/candidatures",
    summary="Les candidatures reçues, récentes d'abord",
)
def lister_les_candidatures(acces: AccesRequis, identifiant: str) -> list[Candidature]:
    exiger(acces, Permission.EDITER_VITRINE)
    _offre(identifiant)
    return depot_candidatures().de_l_offre(identifiant)


@routeur.get(
    "/cabinet/candidatures/{identifiant}/cv",
    summary="Le CV d'une candidature, en PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def telecharger_le_cv(acces: AccesRequis, identifiant: str) -> Response:
    exiger(acces, Permission.EDITER_VITRINE)
    candidature = depot_candidatures().lire(identifiant)
    if candidature is None:
        raise HTTPException(status_code=404, detail=f"candidature {identifiant} introuvable.")
    return Response(
        content=_magasin().lire(candidature.cv_empreinte),
        media_type="application/pdf",
        headers={"Content-Disposition": disposition_en_piece_jointe(candidature.cv_nom)},
    )

