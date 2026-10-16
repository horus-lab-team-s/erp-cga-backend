"""Les missions des prestations ponctuelles : ouvrir, instruire, soumettre, valider, livrer.

─────────────────────────────────────────────────────────────────────────────────
* `GET  /missions` : les missions du cabinet (TRAITER_MISSION) ;
* `POST /missions` : ouvrir, depuis un dossier commercial PAYÉ de prestation
  ponctuelle, ou directement (TRAITER_MISSION) ;
* `POST /missions/fichiers` : verser le document à analyser ;
* `GET  /missions/{id}` et `GET .../document` ;
* `GET  /missions/lecture-automatique` : la lecture par l'agent est-elle disponible ;
* `POST /missions/{id}/analyse-automatique` : l'agent propose (503 sans clé) ;
* `POST /missions/{id}/propositions` : un point ajouté à la main ;
* `POST /missions/{id}/propositions/{rang}/decision` : retenir, modifier, écarter ;
* `POST /missions/{id}/soumission` puis `/validation` (un autre, VALIDER_MISSION) ou
  `/renvoi` ;
* `GET  /missions/{id}/livrable` : le PDF des seuls points retenus.

Voir `domaine/missions.py` : l'agent propose, le juriste décide.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.adaptateurs.sortant.depots_missions import (
    DepotMissionsSql,
    missions_en_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.document_mission import rendre_le_livrable
from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.contextes.souscription.domaine.missions import (
    DecisionProposition,
    Gravite,
    LectureAutomatique,
    Mission,
    OrigineProposition,
    Rubrique,
    TransitionMissionRefusee,
    TypeMission,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.en_tetes import disposition_en_ligne
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import (
    FichierAbsent,
    MagasinFichiersLocal,
    detecter_le_type,
)

routeur = APIRouter(prefix="/missions", tags=["Prestations ponctuelles"])

#: Un contrat se photographie ou se numérise : 20 Mo, comme les pièces.
_TAILLE_MAXIMALE = 20 * 1024 * 1024
_ACCEPTES = {"application/pdf", "image/jpeg", "image/png"}


def _depot():
    session = session_de_travail()
    return missions_en_memoire() if session is None else DepotMissionsSql(session, courant())


def _magasin() -> MagasinFichiersLocal:
    return MagasinFichiersLocal(Path(configuration().dossier_fichiers), courant())


def service_de_redaction():
    """L'agent de rédaction, ou `None` sans clé."""
    config = configuration()
    if not config.anthropic_cle_api:
        return None
    from app.contextes.souscription.adaptateurs.sortant.redaction_claude import RedactionClaude

    return RedactionClaude(cle_api=config.anthropic_cle_api, modele=config.lecture_modele)


def service_d_analyse():
    """L'agent d'analyse, ou `None` sans clé : aucun document ne sort alors du serveur."""
    config = configuration()
    if not config.anthropic_cle_api:
        return None
    from app.contextes.souscription.adaptateurs.sortant.analyse_claude import AnalyseClaude

    return AnalyseClaude(cle_api=config.anthropic_cle_api, modele=config.lecture_modele)


def _mission(identifiant: str) -> Mission:
    mission = _depot().lire(identifiant)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"mission {identifiant} introuvable.")
    return mission


def _enregistrer(mission: Mission, *, action: str, par: str, apres: dict | None = None) -> Mission:
    _depot().enregistrer(mission)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=par,
        action=f"missions.{action}",
        objet_type="mission",
        objet_id=mission.identifiant,
        apres={"etat": mission.etat.value, **(apres or {})},
    )
    return mission


def _refus(geste):
    try:
        return geste()
    except TransitionMissionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


# ── Lire ──────────────────────────────────────────────────────────────────────


class EtatDeLaLecture(BaseModel):
    disponible: bool


@routeur.get("/lecture-automatique", summary="La lecture par l'agent est-elle configurée ?")
def lecture_disponible(acces: AccesRequis) -> EtatDeLaLecture:
    exiger(acces, Permission.TRAITER_MISSION)
    return EtatDeLaLecture(disponible=service_d_analyse() is not None)


@routeur.get("", summary="Les missions du cabinet")
def lister(acces: AccesRequis) -> list[Mission]:
    # Le juriste, ou le second qui valide : il doit lire ce qu'il valide.
    if not acces.detient(Permission.VALIDER_MISSION):
        exiger(acces, Permission.TRAITER_MISSION)
    return _depot().lister()


@routeur.get("/{identifiant}", summary="Une mission, avec ses propositions")
def lire(acces: AccesRequis, identifiant: str) -> Mission:
    # Le juriste, ou le second qui valide : il doit lire ce qu'il valide.
    if not acces.detient(Permission.VALIDER_MISSION):
        exiger(acces, Permission.TRAITER_MISSION)
    return _mission(identifiant)


@routeur.get("/{identifiant}/document", summary="Le document de la mission")
def document(acces: AccesRequis, identifiant: str) -> Response:
    # Le juriste, ou le second qui valide : il doit lire ce qu'il valide.
    if not acces.detient(Permission.VALIDER_MISSION):
        exiger(acces, Permission.TRAITER_MISSION)
    mission = _mission(identifiant)
    if not mission.document_empreinte:
        raise HTTPException(status_code=404, detail="aucun document versé à cette mission.")
    try:
        contenu = _magasin().lire(mission.document_empreinte)
    except FichierAbsent as absent:
        raise HTTPException(status_code=404, detail="document introuvable au magasin.") from absent
    return Response(
        content=contenu,
        media_type=detecter_le_type(contenu) or "application/octet-stream",
        headers={"Content-Disposition": disposition_en_ligne(mission.document_nom or "document")},
    )


# ── Ouvrir et verser ─────────────────────────────────────────────────────────


class Ouverture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TypeMission
    objet: str = Field(min_length=1, max_length=500)
    #: Le dossier commercial payé d'où vient la mission ; sinon le client se saisit.
    reference_commerciale: str | None = None
    client: str | None = Field(default=None, max_length=255)
    client_courriel: str | None = None
    #: DSF ciblée : le dossier et l'exercice.
    dossier_niu: str | None = None
    exercice: str | None = None


@routeur.post("", status_code=201, summary="Ouvrir une mission")
def ouvrir(acces: AccesRequis, demande: Ouverture) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    client, courriel = demande.client, demande.client_courriel
    if demande.reference_commerciale:
        from app.contextes.souscription.adaptateurs.entrant.sources import (
            depot_dossiers,
        )
        from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
            DossierIntrouvable,
        )

        try:
            dossier = depot_dossiers().lire(demande.reference_commerciale)
        except DossierIntrouvable:
            dossier = None
        if dossier is None:
            raise HTTPException(
                status_code=404, detail=f"dossier {demande.reference_commerciale} introuvable."
            )
        # ⚠️ On ne livre que ce qui est payé : une mission ouverte sur une proforma
        # non réglée ferait travailler le cabinet pour rien, ou gratuitement.
        if dossier.etat is not EtatDossier.PAYEE or dossier.service != "PONCTUEL":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"le dossier {dossier.reference} n'est pas une prestation ponctuelle "
                    f"payée (service {dossier.service}, état {dossier.etat.value}). La "
                    "mission s'ouvre après le règlement."
                ),
            )
        client = client or dossier.demande.nom
        courriel = courriel or dossier.demande.courriel
    if not client:
        raise HTTPException(status_code=422, detail="le client de la mission est obligatoire.")
    if demande.type is TypeMission.DSF_CIBLEE and not (demande.dossier_niu and demande.exercice):
        raise HTTPException(
            status_code=422,
            detail="une DSF ciblée porte sur un dossier et un exercice : les deux sont requis.",
        )
    instant = maintenant()
    mission = Mission(
        identifiant=f"MIS-{instant:%Y}-{uuid.uuid4().hex[:6].upper()}",
        type=demande.type,
        client=client,
        client_courriel=courriel,
        reference_commerciale=demande.reference_commerciale,
        objet=demande.objet,
        responsable=acces.compte,
        ouverte_le=instant,
        dossier_niu=demande.dossier_niu if demande.type is TypeMission.DSF_CIBLEE else None,
        exercice=demande.exercice if demande.type is TypeMission.DSF_CIBLEE else None,
    )
    return _enregistrer(mission, action="ouverte", par=acces.compte, apres={"type": mission.type})


@routeur.post("/{identifiant}/document", summary="Verser le document à analyser")
async def verser(acces: AccesRequis, identifiant: str, fichier: UploadFile = File(...)) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    # Lecture bornée, puis rangement hors de la boucle (voir la collecte).
    contenu = await fichier.read(_TAILLE_MAXIMALE + 1)
    return await run_in_threadpool(_verser, acces.compte, identifiant, contenu, fichier.filename)


def _verser(par: str, identifiant: str, contenu: bytes, nom: str | None) -> Mission:
    if len(contenu) > _TAILLE_MAXIMALE:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux : 20 Mo au plus.")
    type_mime = detecter_le_type(contenu)
    if type_mime not in _ACCEPTES:
        raise HTTPException(
            status_code=415, detail="Seuls un PDF ou une photo (JPEG, PNG) se versent."
        )
    empreinte = hashlib.sha256(contenu).hexdigest()
    _magasin().deposer(empreinte, contenu, type_mime=type_mime)
    mission = _refus(
        lambda: _mission(identifiant).joindre(empreinte=empreinte, nom=nom or "document")
    )
    return _enregistrer(mission, action="document_verse", par=par, apres={"document": empreinte})


# ── Instruire ────────────────────────────────────────────────────────────────


@routeur.post("/{identifiant}/analyse-automatique", summary="L'agent propose une analyse")
def analyser(acces: AccesRequis, identifiant: str) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    mission = _mission(identifiant)
    service = service_d_analyse()
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="lecture automatique non configurée : instruisez la mission à la main.",
        )
    if mission.type is not TypeMission.ANALYSE_CONTRAT:
        raise HTTPException(status_code=409, detail="l'agent n'analyse que des contrats.")
    if not mission.document_empreinte:
        raise HTTPException(status_code=409, detail="versez d'abord le contrat à analyser.")
    lecture = service.lire(_magasin().lire(mission.document_empreinte))
    trace = LectureAutomatique(
        moteur=service.MOTEUR,
        faite_le=maintenant(),
        demandee_par=acces.compte,
        propositions=len(lecture.propositions),
        incident=lecture.incident,
    )
    mise_a_jour = _refus(
        lambda: mission.ajouter(
            lecture.propositions, origine=OrigineProposition.AGENT, lecture=trace
        )
    )
    return _enregistrer(
        mise_a_jour,
        action="analyse_automatique",
        par=acces.compte,
        apres={"propositions": trace.propositions, "incident": trace.incident},
    )


class Cahier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_de_contrat: str = Field(min_length=3, max_length=200)
    cahier: str = Field(min_length=20, max_length=8000)


@routeur.post("/{identifiant}/cahier", summary="Écrire le cahier de rédaction")
def ecrire_le_cahier(acces: AccesRequis, identifiant: str, demande: Cahier) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    mission = _refus(
        lambda: _mission(identifiant).ecrire_le_cahier(
            type_de_contrat=demande.type_de_contrat, cahier=demande.cahier
        )
    )
    return _enregistrer(mission, action="cahier_ecrit", par=acces.compte)


@routeur.post("/{identifiant}/redaction-automatique", summary="L'agent propose les articles")
def rediger(acces: AccesRequis, identifiant: str) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    mission = _mission(identifiant)
    service = service_de_redaction()
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="rédaction automatique non configurée : rédigez les articles à la main.",
        )
    if mission.type is not TypeMission.REDACTION_CONTRAT:
        raise HTTPException(status_code=409, detail="l'agent ne rédige que des contrats.")
    if not (mission.cahier and mission.type_de_contrat):
        raise HTTPException(status_code=409, detail="écrivez d'abord le cahier de rédaction.")
    lecture = service.rediger(type_de_contrat=mission.type_de_contrat, cahier=mission.cahier)
    trace = LectureAutomatique(
        moteur=service.MOTEUR,
        faite_le=maintenant(),
        demandee_par=acces.compte,
        propositions=len(lecture.propositions),
        incident=lecture.incident,
    )
    mise_a_jour = _refus(
        lambda: mission.ajouter(
            lecture.propositions, origine=OrigineProposition.AGENT, lecture=trace
        )
    )
    return _enregistrer(
        mise_a_jour,
        action="redaction_automatique",
        par=acces.compte,
        apres={"propositions": trace.propositions, "incident": trace.incident},
    )


class PropositionManuelle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubrique: Rubrique
    titre: str = Field(min_length=1, max_length=200)
    texte: str = Field(min_length=1, max_length=4000)
    citation: str | None = Field(default=None, max_length=2000)
    gravite: Gravite | None = None


@routeur.post("/{identifiant}/propositions", summary="Ajouter un point à la main")
def proposer(acces: AccesRequis, identifiant: str, point: PropositionManuelle) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    mission = _refus(
        lambda: _mission(identifiant).ajouter(
            [point.model_dump()], origine=OrigineProposition.COLLABORATEUR
        )
    )
    return _enregistrer(mission, action="proposition_ajoutee", par=acces.compte)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionProposition
    texte_retenu: str | None = Field(default=None, max_length=4000)
    motif: str | None = Field(default=None, max_length=1000)


@routeur.post("/{identifiant}/propositions/{rang}/decision", summary="Retenir, modifier, écarter")
def decider(acces: AccesRequis, identifiant: str, rang: int, demande: Decision) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    try:
        mission = _mission(identifiant).decider(
            rang,
            decision=demande.decision,
            par=acces.compte,
            a_l_instant=maintenant(),
            texte_retenu=demande.texte_retenu,
            motif=demande.motif,
        )
    except TransitionMissionRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    except ValueError as invalide:
        # Retenue sans texte, écartée sans motif : la proposition le refuse.
        raise HTTPException(status_code=422, detail=message_lisible(invalide)) from invalide
    return _enregistrer(
        mission,
        action="proposition_decidee",
        par=acces.compte,
        apres={"rang": rang, "decision": demande.decision},
    )


# ── Soumettre, valider, livrer ───────────────────────────────────────────────


@routeur.post("/{identifiant}/soumission", summary="Soumettre au second regard")
def soumettre(acces: AccesRequis, identifiant: str) -> Mission:
    exiger(acces, Permission.TRAITER_MISSION)
    mission = _refus(
        lambda: _mission(identifiant).soumettre(par=acces.compte, a_l_instant=maintenant())
    )
    return _enregistrer(mission, action="soumise", par=acces.compte)


@routeur.post("/{identifiant}/validation", summary="Valider : la mission est livrée")
def valider(acces: AccesRequis, identifiant: str) -> Mission:
    exiger(acces, Permission.VALIDER_MISSION)
    mission = _refus(
        lambda: _mission(identifiant).valider(par=acces.compte, a_l_instant=maintenant())
    )
    return _enregistrer(mission, action="livree", par=acces.compte)


class Renvoi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=5, max_length=1000)


@routeur.post("/{identifiant}/renvoi", summary="Renvoyer au juriste, avec un motif")
def renvoyer(acces: AccesRequis, identifiant: str, demande: Renvoi) -> Mission:
    exiger(acces, Permission.VALIDER_MISSION)
    mission = _refus(lambda: _mission(identifiant).renvoyer(motif=demande.motif))
    return _enregistrer(
        mission, action="renvoyee", par=acces.compte, apres={"motif": demande.motif}
    )


@routeur.get("/{identifiant}/livrable", summary="Le livrable PDF : les seuls points retenus")
def livrable(acces: AccesRequis, identifiant: str) -> Response:
    # Le juriste, ou le second qui valide : il doit lire ce qu'il valide.
    if not acces.detient(Permission.VALIDER_MISSION):
        exiger(acces, Permission.TRAITER_MISSION)
    mission = _mission(identifiant)
    noms = {c.identifiant: c.nom_affiche for c in atelier().comptes.lister()}
    return Response(
        content=rendre_le_livrable(mission, noms=noms),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{mission.identifiant}.pdf"'},
    )
