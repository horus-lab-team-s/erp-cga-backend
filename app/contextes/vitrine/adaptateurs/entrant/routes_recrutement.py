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

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.contextes.vitrine.adaptateurs.sortant.depots_recrutement import (
    depot_candidatures,
    depot_offres,
)
from app.contextes.vitrine.domaine.recrutement import (
    LIBELLES_ETAT,
    Candidature,
    CandidatureRefusee,
    EtatCandidature,
    OffreDePoste,
    OffreRefusee,
    ReponseAuCandidat,
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
    #: 18 octobre 2026 : l'offre a une image, servie par `/vitrine/offres/{id}/image`.
    image: bool = False


def _publique(o: OffreDePoste) -> OffrePublique:
    return OffrePublique(
        identifiant=o.identifiant,
        intitule=o.intitule,
        lieu=o.lieu,
        type_de_contrat=o.type_de_contrat,
        date_limite=o.date_limite,
        description=en_blocs(o.description),
        qualifications=en_blocs(o.qualifications),
        image=o.image_empreinte is not None,
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
    # 19 octobre 2026 (point 25) : l'accusé de réception part de lui-même, si le
    # candidat a laissé une adresse. Un échec d'envoi ne défait pas la candidature.
    if candidature.courriel:
        atelier().notifications.envoyer(
            "recrutement.accuse",
            destinataire=candidature.courriel,
            contexte={"nom": candidature.nom, "poste": offre.intitule},
        )
    return AccuseDeCandidature(
        recue=True,
        message=(
            "Candidature reçue : un accusé vous est envoyé par courriel, et le cabinet "
            "vous recontactera."
            if candidature.courriel
            else "Candidature reçue : le cabinet vous recontactera."
        ),
    )


# ── Ce que le cabinet rédige ────────────────────────────────────────────────────


def _droit_d_editer(acces) -> Permission:
    """Rédiger une offre : qui édite le site, ou qui recrute (18 octobre 2026)."""
    return (
        Permission.GERER_RECRUTEMENT
        if acces.detient(Permission.GERER_RECRUTEMENT)
        else Permission.EDITER_VITRINE
    )


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
    exiger(acces, _droit_d_editer(acces))
    return [_du_cabinet(o) for o in depot_offres().lister()]


@routeur.post("/cabinet/offres", summary="Rédiger une offre (en brouillon)", status_code=201)
def rediger_une_offre(acces: AccesRequis, saisie: SaisieDOffre) -> OffreDuCabinet:
    exiger(acces, _droit_d_editer(acces))
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
    exiger(acces, _droit_d_editer(acces))
    offre = _refus(lambda: _offre(identifiant).modifier(**saisie.model_dump()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_modifiee", offre)
    return _du_cabinet(offre)


@routeur.post("/cabinet/offres/{identifiant}/publication", summary="Publier sur le site")
def publier_une_offre(acces: AccesRequis, identifiant: str) -> OffreDuCabinet:
    exiger(acces, _droit_d_editer(acces))
    offre = _refus(lambda: _offre(identifiant).publier(maintenant(), jour=aujourd_hui()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_publiee", offre)
    return _du_cabinet(offre)


@routeur.post("/cabinet/offres/{identifiant}/cloture", summary="Clore : retirer du site")
def clore_une_offre(acces: AccesRequis, identifiant: str) -> OffreDuCabinet:
    exiger(acces, _droit_d_editer(acces))
    offre = _refus(lambda: _offre(identifiant).clore(maintenant()))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_close", offre)
    return _du_cabinet(offre)


@routeur.get(
    "/cabinet/offres/{identifiant}/candidatures",
    summary="Les candidatures reçues, récentes d'abord",
)
def lister_les_candidatures(acces: AccesRequis, identifiant: str) -> list[Candidature]:
    exiger(acces, Permission.GERER_RECRUTEMENT)
    _offre(identifiant)
    return depot_candidatures().de_l_offre(identifiant)


@routeur.get(
    "/cabinet/candidatures/{identifiant}/cv",
    summary="Le CV d'une candidature, en PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def telecharger_le_cv(acces: AccesRequis, identifiant: str) -> Response:
    exiger(acces, Permission.GERER_RECRUTEMENT)
    candidature = depot_candidatures().lire(identifiant)
    if candidature is None:
        raise HTTPException(status_code=404, detail=f"candidature {identifiant} introuvable.")
    return Response(
        content=_magasin().lire(candidature.cv_empreinte),
        media_type="application/pdf",
        headers={"Content-Disposition": disposition_en_piece_jointe(candidature.cv_nom)},
    )


# ── L'image de l'offre (18 octobre 2026, point 25) ──────────────────────────────

_TAILLE_MAXIMALE_IMAGE = 2 * 1024 * 1024
_IMAGES = {"image/jpeg", "image/png"}


@routeur.put(
    "/cabinet/offres/{identifiant}/image",
    summary="Joindre une image à l'offre (JPEG ou PNG, 2 Mo au plus)",
    responses={413: {"description": "Image trop lourde"}, 415: {"description": "Pas une image"}},
)
async def joindre_une_image(
    acces: AccesRequis, identifiant: str, image: UploadFile = File(...)
) -> OffreDuCabinet:
    exiger(acces, _droit_d_editer(acces))
    contenu = await image.read(_TAILLE_MAXIMALE_IMAGE + 1)
    return await run_in_threadpool(_joindre_une_image, acces, identifiant, contenu)


def _joindre_une_image(acces, identifiant: str, contenu: bytes) -> OffreDuCabinet:
    if len(contenu) > _TAILLE_MAXIMALE_IMAGE:
        raise HTTPException(status_code=413, detail="Image trop lourde : 2 Mo au plus.")
    # ⚠️ Le type lu dans les octets, jamais l'étiquette : une page HTML déguisée en image
    # serait servie sur le site public.
    type_mime = detecter_le_type(contenu)
    if type_mime not in _IMAGES:
        raise HTTPException(status_code=415, detail="L'image se dépose en JPEG ou en PNG.")
    empreinte = hashlib.sha256(contenu).hexdigest()
    _magasin().deposer(empreinte, contenu, type_mime=type_mime)
    offre = _refus(
        lambda: _offre(identifiant).modifier(image_empreinte=empreinte, image_type=type_mime)
    )
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_illustree", offre)
    return _du_cabinet(offre)


@routeur.delete("/cabinet/offres/{identifiant}/image", summary="Retirer l'image de l'offre")
def retirer_l_image(acces: AccesRequis, identifiant: str) -> OffreDuCabinet:
    exiger(acces, _droit_d_editer(acces))
    offre = _refus(lambda: _offre(identifiant).modifier(image_empreinte=None, image_type=None))
    depot_offres().enregistrer(offre)
    _tracer(acces, "offre_sans_image", offre)
    return _du_cabinet(offre)


def _servir_l_image(offre: OffreDePoste) -> Response:
    if offre.image_empreinte is None or offre.image_type not in _IMAGES:
        raise HTTPException(status_code=404, detail="cette offre n'a pas d'image.")
    return Response(
        content=_magasin().lire(offre.image_empreinte),
        media_type=offre.image_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=3600"},
    )


@routeur.get(
    "/offres/{identifiant}/image",
    summary="L'image d'une offre ouverte, pour le site",
    response_class=Response,
)
def image_publique(identifiant: str) -> Response:
    offre = _offre(identifiant)
    if not offre.ouverte_au(aujourd_hui()):
        raise HTTPException(status_code=404, detail=f"offre {identifiant} introuvable.")
    return _servir_l_image(offre)


@routeur.get(
    "/cabinet/offres/{identifiant}/image",
    summary="L'image d'une offre, brouillon compris",
    response_class=Response,
)
def image_du_cabinet(acces: AccesRequis, identifiant: str) -> Response:
    exiger(acces, _droit_d_editer(acces))
    return _servir_l_image(_offre(identifiant))


# ── Traiter une candidature, et lui répondre (18 octobre 2026, point 25) ────────────


def _candidature(identifiant: str) -> Candidature:
    candidature = depot_candidatures().lire(identifiant)
    if candidature is None:
        raise HTTPException(status_code=404, detail=f"candidature {identifiant} introuvable.")
    return candidature


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: L'état où la candidature passe.
    decision: EtatCandidature
    note: str | None = Field(default=None, max_length=1000)


@routeur.post(
    "/cabinet/candidatures/{identifiant}/decision",
    summary="Décider : présélection, entretien, retenue, non retenue",
)
def decider(acces: AccesRequis, identifiant: str, decision: Decision) -> Candidature:
    exiger(acces, Permission.GERER_RECRUTEMENT)
    candidature = _candidature(identifiant)
    try:
        decidee = candidature.decider(
            decision.decision,
            par=acces.compte,
            le=maintenant(),
            note=(decision.note or "").strip() or None,
        )
    except CandidatureRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    depot_candidatures().enregistrer(decidee)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="vitrine.candidature_decidee",
        objet_type="candidature",
        objet_id=identifiant,
        apres={"etat": decision.decision.value},
        motif=decision.note,
    )
    return decidee


class ReponseProposee(BaseModel):
    objet: str
    corps: str
    #: `MODELE` : le texte type du cabinet ; `AGENT` : une proposition à relire.
    source: str
    #: Le candidat a laissé une adresse : la réponse partira par courriel.
    courriel: str | None


def service_de_redaction():
    """L'agent de rédaction, ou `None` sans clé : l'écran garde alors le texte type."""
    reglages = configuration()
    if not reglages.anthropic_cle_api:
        return None
    from app.contextes.vitrine.adaptateurs.sortant.redaction_reponse_claude import (
        RedactionDeReponse,
    )

    return RedactionDeReponse(cle_api=reglages.anthropic_cle_api, modele=reglages.lecture_modele)


@routeur.get(
    "/cabinet/candidatures/{identifiant}/reponse-proposee",
    summary="La réponse proposée pour l'état de la candidature, à relire avant l'envoi",
)
def proposer_une_reponse(
    acces: AccesRequis,
    identifiant: str,
    agent: bool = Query(False, description="Demander une proposition à l'agent"),
) -> ReponseProposee:
    exiger(acces, Permission.GERER_RECRUTEMENT)
    from app.contextes.vitrine.domaine.reponses_de_recrutement import modele_de_reponse

    candidature = _candidature(identifiant)
    offre = _offre(candidature.offre)
    objet, corps = modele_de_reponse(candidature.etat, candidature, offre)
    source = "MODELE"
    service = service_de_redaction() if agent else None
    if service is not None:
        note = candidature.decisions[-1].note if candidature.decisions else None
        propose = service.proposer(
            poste=offre.intitule,
            lieu=offre.lieu,
            prenom=candidature.appellation,
            decision=LIBELLES_ETAT[candidature.etat],
            note=note,
            objet_type=objet,
            corps_type=corps,
        )
        if propose is not None:
            objet, corps = propose
            source = "AGENT"
    return ReponseProposee(objet=objet, corps=corps, source=source, courriel=candidature.courriel)


class ReponseAEnvoyer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objet: str = Field(min_length=3, max_length=200)
    corps: str = Field(min_length=20, max_length=5000)
    source: str = Field(default="MODELE", pattern="^(MODELE|AGENT)$")


@routeur.post(
    "/cabinet/candidatures/{identifiant}/reponse",
    summary="Envoyer au candidat la réponse relue",
)
def repondre(acces: AccesRequis, identifiant: str, reponse: ReponseAEnvoyer) -> Candidature:
    """⚠️ Le texte envoyé est celui que l'humain a relu, pas la proposition. Un « [à
    compléter] » oublié est refusé : il partirait tel quel chez le candidat."""
    exiger(acces, Permission.GERER_RECRUTEMENT)
    if "[à compléter]" in reponse.corps.lower() or "[a completer]" in reponse.corps.lower():
        raise HTTPException(
            status_code=422,
            detail="Le texte contient encore « [à compléter] » : complétez-le avant l'envoi.",
        )
    candidature = _candidature(identifiant)
    offre = _offre(candidature.offre)
    envoyee = False
    if candidature.courriel:
        paragraphes = [p.strip() for p in reponse.corps.split("\n\n") if p.strip()]
        if len(paragraphes) > 10:
            paragraphes = [*paragraphes[:9], "\n\n".join(paragraphes[9:])]
        contexte = {"objet": reponse.objet, "poste": offre.intitule}
        contexte |= {f"p{rang}": "" for rang in range(1, 11)}
        contexte |= {f"p{rang}": texte for rang, texte in enumerate(paragraphes, start=1)}
        envoyee = atelier().notifications.envoyer(
            "recrutement.reponse", destinataire=candidature.courriel, contexte=contexte
        )
    repondue = candidature.repondre(
        ReponseAuCandidat(
            objet=reponse.objet,
            corps=reponse.corps,
            source=reponse.source,
            par=acces.compte,
            le=maintenant(),
            envoyee=envoyee,
        )
    )
    depot_candidatures().enregistrer(repondue)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="vitrine.candidat_repondu",
        objet_type="candidature",
        objet_id=identifiant,
        apres={"envoyee": envoyee, "source": reponse.source},
    )
    return repondue
