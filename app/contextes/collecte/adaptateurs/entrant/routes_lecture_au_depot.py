"""La lecture au dépôt : réglage du cabinet, accord de l'adhérent, lecture préparée, et le
travail de fond qui lit (18 octobre 2026, point 13). Voir `domaine/lecture_au_depot.py`.

QUI FAIT QUOI

* activer ou couper la fonctionnalité : la direction (`ARRETER_POLITIQUE`, une politique
  du cabinet) ou le super administrateur (`GERER_ROLES`), avec un motif ;
* donner ou retirer l'accord : l'adhérent, pour son dossier (`REPONDRE_AU_CABINET`, un
  geste du client). Le cabinet ne consent pas à sa place ;
* lire ce qui a été préparé : qui identifie les pièces (`IDENTIFIER_PIECE`).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.collecte.adaptateurs.entrant.routes_facture_lue import service_de_lecture
from app.contextes.collecte.adaptateurs.entrant.routes_http import depot_pieces, magasin_fichiers
from app.contextes.collecte.adaptateurs.sortant.depots_lecture_au_depot import (
    depots_de_la_lecture,
)
from app.contextes.collecte.domaine.lecture_au_depot import (
    ConsentementDeLecture,
    LecturePreparee,
    ReglageDeLecture,
    ValeurPreparee,
    points_a_verifier,
)
from app.contextes.collecte.domaine.pieces import CanalDepot, EtatPiece
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
)
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import maintenant

__all__ = ["NOM_TRAVAIL", "lire_les_pieces_deposees", "routeur", "travail_de_lecture_au_depot"]

routeur = APIRouter(prefix="/collecte", tags=["Collecte"])
_journal = logging.getLogger("cga.lecture_au_depot")

#: Les canaux du client : ce qu'il dépose lui-même, depuis son espace ou son téléphone.
CANAUX_DU_CLIENT = frozenset({CanalDepot.PORTAIL, CanalDepot.MOBILE})
#: Un passage lit au plus ce nombre de pièces : chaque lecture est un appel au réseau.
PAR_PASSAGE = 5


# ── Le réglage du cabinet ─────────────────────────────────────────────────────────


class EtatDeLaLecture(BaseModel):
    actif: bool
    #: La clé de l'agent est posée : sans elle, l'activer ne lit rien.
    agent_disponible: bool
    reglage: ReglageDeLecture | None


def _etat() -> EtatDeLaLecture:
    reglage, _, _ = depots_de_la_lecture()
    fixe = reglage.lire()
    return EtatDeLaLecture(
        actif=bool(fixe and fixe.actif),
        agent_disponible=service_de_lecture() is not None,
        reglage=fixe,
    )


@routeur.get("/lecture-au-depot", summary="La lecture automatique au dépôt, pour le cabinet")
def lire_le_reglage(acces: AccesRequis) -> EtatDeLaLecture:
    exiger(acces, Permission.LIRE_DOSSIER)
    return _etat()


class ReglageDemande(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actif: bool
    motif: str = Field(min_length=10, max_length=500)


def _droit_de_regler(acces) -> Permission:
    return (
        Permission.GERER_ROLES
        if acces.detient(Permission.GERER_ROLES)
        else Permission.ARRETER_POLITIQUE
    )


@routeur.put("/lecture-au-depot", summary="Activer ou couper la lecture automatique au dépôt")
def regler(acces: AccesRequis, demande: ReglageDemande) -> EtatDeLaLecture:
    exiger(acces, _droit_de_regler(acces), motif=demande.motif)
    reglage, _, _ = depots_de_la_lecture()
    reglage.enregistrer(
        ReglageDeLecture(
            actif=demande.actif,
            motif=demande.motif,
            regle_par=acces.compte,
            regle_le=maintenant(),
        )
    )
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="collecte.lecture_au_depot",
        objet_type="reglage",
        objet_id="lecture-au-depot",
        apres={"actif": demande.actif},
        motif=demande.motif,
    )
    return _etat()


# ── L'accord de l'adhérent ────────────────────────────────────────────────────────


class AccordDuDossier(BaseModel):
    #: La fonctionnalité est active au cabinet : sinon, l'accord ne sert à rien encore.
    proposee: bool
    accorde: bool
    le: str | None = None


@routeur.get(
    "/dossiers/{niu}/consentement-de-lecture",
    summary="L'accord de l'adhérent à la lecture automatique de ses pièces",
)
def lire_l_accord(acces: AccesRequis, niu: str) -> AccordDuDossier:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    _, consentements, _ = depots_de_la_lecture()
    accord = consentements.lire(niu)
    return AccordDuDossier(
        proposee=_etat().actif,
        accorde=bool(accord and accord.accorde),
        le=accord.le.isoformat() if accord else None,
    )


class AccordDemande(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accorde: bool


@routeur.put(
    "/dossiers/{niu}/consentement-de-lecture",
    summary="Donner ou retirer son accord à la lecture automatique",
)
def donner_l_accord(acces: AccesRequis, niu: str, demande: AccordDemande) -> AccordDuDossier:
    """⚠️ Le client seul : son document part chez Anthropic, c'est à lui d'en décider."""
    exiger_dossier(acces, Permission.REPONDRE_AU_CABINET, niu)
    _, consentements, _ = depots_de_la_lecture()
    consentements.enregistrer(
        ConsentementDeLecture(niu=niu, accorde=demande.accorde, par=acces.compte, le=maintenant())
    )
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="collecte.consentement_de_lecture",
        objet_type="dossier",
        objet_id=niu,
        apres={"accorde": demande.accorde},
    )
    return lire_l_accord(acces, niu)


# ── La lecture préparée d'une pièce ─────────────────────────────────────────────────


@routeur.get(
    "/pieces/{identifiant}/lecture-preparee",
    summary="Ce que l'agent a lu de la pièce à son dépôt, s'il l'a lue",
)
def lire_la_lecture_preparee(acces: AccesRequis, identifiant: str) -> LecturePreparee:
    introuvable = f"aucune lecture préparée pour la pièce {identifiant}."
    piece = depot_pieces().par_identifiant(identifiant)
    if piece is None:
        raise HTTPException(status_code=404, detail=introuvable)
    exiger_dossier(acces, Permission.IDENTIFIER_PIECE, piece.entreprise, introuvable=introuvable)
    _, _, lectures = depots_de_la_lecture()
    lecture = lectures.lire(identifiant)
    if lecture is None:
        raise HTTPException(status_code=404, detail=introuvable)
    return lecture


# ── Le travail de fond ──────────────────────────────────────────────────────────────

NOM_TRAVAIL = "lecture-au-depot"


def travail_de_lecture_au_depot() -> Travail:
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=2),
        objet=(
            "lit les pièces que les adhérents consentants ont déposées, et prépare leur "
            "facture pour le collaborateur ; ne bloque jamais un dépôt"
        ),
    )


def lire_les_pieces_deposees() -> str:
    """Un passage. ⚠️ Rien ne se lit si la fonctionnalité est coupée, si la clé manque, ou
    sans l'accord de l'adhérent ; une panne de lecture est rangée avec son incident, pour
    ne pas relire la même pièce à chaque passage."""
    reglage, consentements, lectures = depots_de_la_lecture()
    fixe = reglage.lire()
    if not (fixe and fixe.actif):
        return "lecture au dépôt coupée"
    service = service_de_lecture()
    if service is None:
        return "agent non configuré : rien ne sort"
    pieces = depot_pieces()
    lues = 0
    for accord in consentements.accordes():
        for piece in pieces.du_dossier(accord.niu):
            if lues >= PAR_PASSAGE:
                return f"{lues} pièce(s) lue(s), la suite au prochain passage"
            if (
                piece.etat is not EtatPiece.RECUE
                or piece.canal not in CANAUX_DU_CLIENT
                or piece.empreinte is None
                or lectures.lire(piece.identifiant) is not None
            ):
                continue
            try:
                contenu = magasin_fichiers().lire(piece.empreinte)
                extraction = service.extraire(
                    contenu, piece=piece.identifiant, type_attendu=piece.type
                )
                valeurs = [
                    ValeurPreparee(
                        champ=v.champ,
                        valeur=v.valeur_lue,
                        confiance=v.confiance,
                        a_relire=not v.acceptable_automatiquement,
                    )
                    for v in extraction.champs.values()
                ]
                lecture = LecturePreparee(
                    piece=piece.identifiant,
                    entreprise=piece.entreprise,
                    lue_le=maintenant(),
                    moteur=extraction.moteur,
                    incident=extraction.incident,
                    valeurs=valeurs,
                    points_a_verifier=[] if extraction.incident else points_a_verifier(valeurs),
                )
            except Exception as panne:  # noqa: BLE001 — une panne de l'agent ne bloque rien
                _journal.exception("lecture au dépôt de %s impossible", piece.identifiant)
                lecture = LecturePreparee(
                    piece=piece.identifiant,
                    entreprise=piece.entreprise,
                    lue_le=maintenant(),
                    moteur="indisponible",
                    incident=f"lecture impossible : {type(panne).__name__}",
                )
            lectures.enregistrer(lecture)
            lues += 1
    return f"{lues} pièce(s) lue(s)"
