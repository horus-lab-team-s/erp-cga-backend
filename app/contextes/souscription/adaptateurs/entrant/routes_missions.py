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
* `GET  /missions/{id}/livrable` : le PDF des seuls points retenus ;
* `GET  /missions/modeles-de-contrat` et `POST /missions/{id}/modele` : partir d'un
  modèle de contrat, avec ou sans agent (18 octobre 2026, point 23) ;
* `GET  /missions/{id}/livrable/word` : le projet de contrat en Word, à retoucher.

Voir `domaine/missions.py` : l'agent propose, le juriste décide.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
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
    LIBELLES_TYPE,
    DecisionProposition,
    EtatMission,
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
    habilitations_actives,
    permissions_de,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.en_tetes import disposition_en_ligne
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, maintenant
from app.partage.locataire import courant
from app.partage.magasin_de_fichiers import (
    FichierAbsent,
    MagasinFichiersLocal,
    detecter_le_type,
)
from app.partage.sceau import SceauInvalide, exiger_le_sceau, sceller

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


# ── Les demandes ponctuelles payées, et qui reprend une mission (18 octobre 2026) ──


class DemandeAOuvrir(BaseModel):
    reference: str
    client: str
    courriel: str | None
    telephone: str
    message: str
    payee_le: datetime | None
    #: Le responsable commercial, qui a chiffré la demande.
    responsable: str | None


@routeur.get("/a-ouvrir", summary="Les demandes ponctuelles payées qui attendent leur mission")
def demandes_a_ouvrir(acces: AccesRequis) -> list[DemandeAOuvrir]:
    """⚠️ La référence ne se recopie plus à la main : une demande venue de la vitrine,
    chiffrée et réglée, se présente ici jusqu'à ce qu'une mission la prenne."""
    exiger(acces, Permission.TRAITER_MISSION)
    from app.contextes.souscription.adaptateurs.entrant.sources import depot_dossiers

    prises = {m.reference_commerciale for m in _depot().lister() if m.reference_commerciale}
    demandes = [
        DemandeAOuvrir(
            reference=d.reference,
            client=d.demande.nom,
            courriel=d.demande.courriel,
            telephone=d.demande.telephone,
            message=(d.demande.message or "")[:500],
            payee_le=d.payee_le,
            responsable=d.responsable,
        )
        for d in depot_dossiers().payes()
        if d.service == "PONCTUEL" and d.reference not in prises
    ]
    # 19 octobre 2026 (point 24) : la DSF achetée en ligne attend ici sa mission.
    commandes = [
        DemandeAOuvrir(
            reference=s.reference,
            client=_client_de_la_commande(s),
            courriel=s.prospect.courriel,
            telephone=s.prospect.telephone,
            message=(
                f"DSF achetée en ligne ({s.libelle}"
                f"{f', formule {s.formule}' if s.formule else ''})"
                f"{f' · NIU {s.niu or s.prospect.niu}' if (s.niu or s.prospect.niu) else ''}."
            ),
            payee_le=s.payee_le,
            responsable=None,
        )
        for s in _dsf_payees()
        if s.reference not in prises
    ]
    return demandes + commandes


def _dsf_payees():
    """Les commandes de DSF réglées en ligne (point 24)."""
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir
    from app.contextes.souscription.domaine.souscriptions import EtatSouscription

    return [
        s
        for s in comptoir().souscriptions.lister()
        if s.service == "DSF" and s.etat in (EtatSouscription.PAYEE, EtatSouscription.ACTIVEE)
    ]


def _client_de_la_commande(souscription) -> str:
    p = souscription.prospect
    return p.denomination or f"{p.prenom} {p.nom}"


class ModeleLu(BaseModel):
    code: str
    libelle: str
    cahier: str
    articles: int


@routeur.get("/modeles-de-contrat", summary="Les modèles de contrat du cabinet")
def modeles_de_contrat(acces: AccesRequis) -> list[ModeleLu]:
    exiger(acces, Permission.TRAITER_MISSION)
    from app.contextes.souscription.domaine.modeles_de_contrat import MODELES_DE_CONTRAT

    return [
        ModeleLu(code=m.code, libelle=m.libelle, cahier=m.cahier, articles=len(m.articles))
        for m in MODELES_DE_CONTRAT
    ]


class CandidatALaMission(BaseModel):
    compte: str
    nom: str


def _candidats() -> list[CandidatALaMission]:
    """Les comptes actifs qui instruisent les missions, à la matrice du cabinet."""
    boutique = atelier()
    jour = aujourd_hui()
    candidats = []
    for compte in boutique.comptes.lister():
        actives = habilitations_actives(
            boutique.habilitations.pour_compte(compte.identifiant), jour
        )
        if Permission.TRAITER_MISSION in permissions_de(h.role for h in actives):
            candidats.append(CandidatALaMission(compte=compte.identifiant, nom=compte.nom_affiche))
    return sorted(candidats, key=lambda c: c.nom)


@routeur.get("/{identifiant}/candidats", summary="Qui peut reprendre la mission")
def candidats_a_la_mission(acces: AccesRequis, identifiant: str) -> list[CandidatALaMission]:
    exiger(acces, Permission.AFFECTER_DOSSIER)
    _mission(identifiant)
    return _candidats()


class Reaffectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responsable: str = Field(min_length=1, max_length=64)
    motif: str = Field(min_length=10, max_length=500)


@routeur.post("/{identifiant}/reaffectation", summary="Confier la mission à un autre")
def reaffecter(acces: AccesRequis, identifiant: str, demande: Reaffectation) -> Mission:
    exiger(acces, Permission.AFFECTER_DOSSIER)
    if demande.responsable not in {c.compte for c in _candidats()}:
        raise HTTPException(
            status_code=422,
            detail=f"{demande.responsable} n'instruit pas de missions : choisir un juriste.",
        )
    mission = _refus(lambda: _mission(identifiant).reaffecter(demande.responsable))
    return _enregistrer(
        mission,
        action="reaffectee",
        par=acces.compte,
        apres={"responsable": demande.responsable, "motif": demande.motif},
    )


#: L'usage du sceau qui ouvre le suivi d'une mission au client. Voir `partage/sceau`.
_USAGE_SUIVI = "suivi-de-mission"


def _secret_du_suivi() -> str:
    return configuration().cle_chiffrement or "secret-de-developpement"


def lien_de_suivi(mission: Mission) -> str:
    """La page où le client suit sa demande, puis ouvre son livrable (19 octobre 2026)."""
    sceau = sceller(mission.identifiant, secret=_secret_du_suivi(), usage=_USAGE_SUIVI)
    base = configuration().adresse_publique_vitrine.rstrip("/")
    return f"{base}/fr/ma-mission/{mission.identifiant}?s={sceau}"


def _prevenir_le_client(mission: Mission, code: str) -> None:
    """Le client apprend que sa demande est prise en charge, puis livrée. ⚠️ Ne lève
    jamais : le geste du cabinet est fait, l'avis n'a pas à l'annuler."""
    if not mission.client_courriel:
        return
    try:
        atelier().notifications.envoyer(
            code,
            destinataire=mission.client_courriel,
            contexte={
                "client": mission.client,
                "objet_de_la_demande": mission.objet,
                "reference": mission.reference_commerciale or mission.identifiant,
                # 19 octobre 2026 (point 22) : le suivi, puis le livrable, par ce lien.
                "lien": lien_de_suivi(mission),
            },
        )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("avis %s de %s non envoyé", code, mission.identifiant)


# ══ Ce que le client voit, sans compte (19 octobre 2026, point 22) ═══════════


class SuiviDeMaMission(BaseModel):
    """Ce que le client lit de sa demande : l'étape, et rien du travail interne.

    ⚠️ Ni les propositions, ni le cahier, ni le nom du juriste : le client sait que le
    cabinet instruit, puis qu'un second relit, puis que c'est livré.
    """

    objet: str
    prestation: str
    etat: EtatMission
    ouverte_le: datetime
    livree_le: datetime | None
    livrable: bool


def _mission_scellee(identifiant: str, s: str) -> Mission:
    """La mission derrière un lien signé, ou 404 : le même refus pour un sceau faux et
    une mission inconnue, sans quoi les identifiants s'énuméreraient."""
    try:
        exiger_le_sceau(identifiant, s, secret=_secret_du_suivi(), usage=_USAGE_SUIVI)
        return _mission(identifiant)
    except (SceauInvalide, HTTPException) as refus:
        raise HTTPException(
            status_code=404,
            detail=(
                "Ce lien ne s'ouvre pas. Vérifiez que vous l'avez copié en entier, "
                "ou écrivez au cabinet."
            ),
        ) from refus


@routeur.get("/{identifiant}/suivi", summary="Le suivi d'une demande, par le lien reçu (public)")
def suivre_ma_mission(identifiant: str, s: str = "") -> SuiviDeMaMission:
    mission = _mission_scellee(identifiant, s)
    return SuiviDeMaMission(
        objet=mission.objet,
        prestation=LIBELLES_TYPE.get(mission.type, "Prestation ponctuelle"),
        etat=mission.etat,
        ouverte_le=mission.ouverte_le,
        livree_le=mission.validee_le if mission.etat is EtatMission.LIVREE else None,
        livrable=mission.etat is EtatMission.LIVREE,
    )


@routeur.get(
    "/{identifiant}/suivi/livrable",
    summary="Le livrable, par le lien reçu (public), une fois validé",
    responses={409: {"description": "Pas encore livré"}},
)
def mon_livrable(identifiant: str, s: str = "") -> Response:
    """⚠️ Seulement une fois la mission LIVRÉE, c'est-à-dire validée par un second : un
    livrable en cours de relecture n'est pas un avis du cabinet."""
    mission = _mission_scellee(identifiant, s)
    if mission.etat is not EtatMission.LIVREE:
        raise HTTPException(status_code=409, detail="Le livrable n'est pas encore validé.")
    return Response(
        content=rendre_le_livrable(mission),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition_en_ligne(f"livrable-{mission.identifiant}.pdf")
        },
    )


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
        # 19 octobre 2026 (point 24) : la référence peut être celle d'une DSF achetée en
        # ligne. Elle ouvre une DSF ciblée, et rien d'autre.
        commande = next(
            (s for s in _dsf_payees() if s.reference == demande.reference_commerciale), None
        )
        if dossier is None and commande is not None:
            if demande.type is not TypeMission.DSF_CIBLEE:
                raise HTTPException(
                    status_code=409,
                    detail=f"la commande {commande.reference} est une DSF : ouvrez une DSF ciblée.",
                )
            deja = next(
                (m for m in _depot().lister() if m.reference_commerciale == commande.reference),
                None,
            )
            if deja is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"la commande {commande.reference} a déjà sa mission : "
                        f"{deja.identifiant}."
                    ),
                )
            client = client or _client_de_la_commande(commande)
            courriel = courriel or commande.prospect.courriel
        elif dossier is None:
            raise HTTPException(
                status_code=404, detail=f"dossier {demande.reference_commerciale} introuvable."
            )
        # ⚠️ On ne livre que ce qui est payé : une mission ouverte sur une proforma
        # non réglée ferait travailler le cabinet pour rien, ou gratuitement.
        if dossier is not None and (
            dossier.etat is not EtatDossier.PAYEE or dossier.service != "PONCTUEL"
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"le dossier {dossier.reference} n'est pas une prestation ponctuelle "
                    f"payée (service {dossier.service}, état {dossier.etat.value}). La "
                    "mission s'ouvre après le règlement."
                ),
            )
        # 18 octobre 2026 (point 22) : une demande, une mission. Une seconde ouverture
        # ferait travailler deux juristes sur la même prestation payée une fois.
        if dossier is not None:
            deja = next(
                (m for m in _depot().lister() if m.reference_commerciale == dossier.reference),
                None,
            )
            if deja is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"la demande {dossier.reference} a déjà sa mission : {deja.identifiant}."
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
    ouverte = _enregistrer(
        mission, action="ouverte", par=acces.compte, apres={"type": mission.type}
    )
    _prevenir_le_client(ouverte, "mission.ouverte")
    return ouverte


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


class ModeleChoisi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modele: str = Field(min_length=1, max_length=60)


@routeur.post(
    "/{identifiant}/modele",
    summary="Partir d'un modèle : le canevas du cahier, et ses articles types à décider",
)
def partir_d_un_modele(acces: AccesRequis, identifiant: str, demande: ModeleChoisi) -> Mission:
    """⚠️ Le cahier déjà écrit est gardé : le modèle ne l'écrase pas. Les articles types
    arrivent EN ATTENTE, et un modèle ne se verse qu'une fois par mission."""
    exiger(acces, Permission.TRAITER_MISSION)
    from app.contextes.souscription.domaine.modeles_de_contrat import modele_de_contrat

    modele = modele_de_contrat(demande.modele)
    if modele is None:
        raise HTTPException(status_code=404, detail=f"aucun modèle « {demande.modele} ».")
    mission = _mission(identifiant)
    if mission.type is not TypeMission.REDACTION_CONTRAT:
        raise HTTPException(status_code=409, detail="un modèle de contrat sert une rédaction.")
    if any(p.origine is OrigineProposition.MODELE for p in mission.propositions):
        raise HTTPException(
            status_code=409, detail="les articles d'un modèle sont déjà dans cette mission."
        )
    if not mission.cahier:
        mission = _refus(
            lambda: mission.ecrire_le_cahier(type_de_contrat=modele.libelle, cahier=modele.cahier)
        )
    articles = [
        {"rubrique": Rubrique.ARTICLE, "titre": a.titre, "texte": a.texte}
        for a in modele.articles
    ]
    mise_a_jour = _refus(lambda: mission.ajouter(articles, origine=OrigineProposition.MODELE))
    return _enregistrer(
        mise_a_jour,
        action="modele_verse",
        par=acces.compte,
        apres={"modele": modele.code, "articles": len(articles)},
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
    livree = _enregistrer(mission, action="livree", par=acces.compte)
    _prevenir_le_client(livree, "mission.livree")
    return livree


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
    return Response(
        content=rendre_le_livrable(mission),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{mission.identifiant}.pdf"'},
    )


@routeur.get(
    "/{identifiant}/livrable/word",
    summary="Le projet de contrat en Word : les articles retenus, à retoucher avec le client",
)
def livrable_word(acces: AccesRequis, identifiant: str) -> Response:
    if not acces.detient(Permission.VALIDER_MISSION):
        exiger(acces, Permission.TRAITER_MISSION)
    from app.contextes.souscription.adaptateurs.sortant.document_mission_word import (
        TYPE_WORD,
        rendre_le_contrat_en_word,
    )

    mission = _mission(identifiant)
    if mission.type is not TypeMission.REDACTION_CONTRAT:
        raise HTTPException(status_code=409, detail="seul un projet de contrat part en Word.")
    return Response(
        content=rendre_le_contrat_en_word(mission),
        media_type=TYPE_WORD,
        headers={"Content-Disposition": f'attachment; filename="{mission.identifiant}.docx"'},
    )
