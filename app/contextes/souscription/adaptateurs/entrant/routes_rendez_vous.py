"""Les rendez-vous de création : créneaux, agenda, constat, pénalité (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
QUI FAIT QUOI

* le visiteur lit les créneaux libres (`GET /acquisition/rendez-vous/creneaux`, publique)
  et en prend un en déposant sa demande (`POST /acquisition/demandes`, champ
  `rendez_vous`) ;
* le responsable de la création (`QUALIFIER_PROSPECT` : chargé des formalités, direction)
  tient l'agenda : il part de la proposition par défaut et FERME ce qui ne lui convient
  pas ; il constate le rendez-vous honoré ou manqué, et pose le lien d'une visioconférence ;
* la caisse (`ENCAISSER`) reçoit la pénalité d'un rendez-vous manqué, dont le montant est
  fixé par le super administrateur avec les frais de dossier.

⚠️ UN RENDEZ-VOUS MANQUÉ BLOQUE LE SUIVANT TANT QUE LA PÉNALITÉ N'EST PAS RÉGLÉE. Sans
pénalité fixée, rien n'est dû et rien n'est bloqué.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.adaptateurs.entrant.sources import (
    boite_du_parcours,
    depot_dossiers,
    depot_proformas,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import (
    CreneauDejaPris,
    DossierIntrouvable,
)
from app.contextes.souscription.application.versements import (
    proforma_a_regler,
    recevoir_un_versement,
)
from app.contextes.souscription.domaine.dossier_commercial import DossierCommercial
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.rendez_vous import (
    AGENDA_PAR_DEFAUT,
    AgendaDeCreation,
    CreneauRefuse,
    EtatRendezVous,
    ModeRendezVous,
    PlageHoraire,
    RendezVous,
    creneaux_libres,
    verifier_le_creneau,
)
from app.contextes.souscription.domaine.versements_du_parcours import (
    NatureVersement,
    SituationDesVersements,
    VersementDejaRecu,
    VersementRefuse,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.erreurs import message_lisible
from app.partage.horloge import a_douala, maintenant

__all__ = [
    "agenda_de_creation",
    "penalite_due",
    "prendre_un_rendez_vous",
    "routeur",
]

routeur = APIRouter(prefix="/acquisition", tags=["Rendez-vous de création"])


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def agenda_de_creation() -> AgendaDeCreation:
    return _comptoir().agenda_de_creation.lire() or AGENDA_PAR_DEFAUT


def _a_douala() -> datetime:
    return a_douala(maintenant())


def penalite_due(dossier: str, situation: SituationDesVersements | None = None) -> Decimal:
    """Ce que les rendez-vous manqués de ce dossier coûtent encore. Zéro sans pénalité fixée."""
    from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
        regle_des_versements,
        situation_des_versements_de,
    )

    penalite = regle_des_versements().penalite_rendez_vous
    if penalite is None:
        return Decimal(0)
    manques = sum(
        1 for r in _comptoir().rendez_vous.pour(dossier) if r.etat is EtatRendezVous.MANQUE
    )
    if manques == 0:
        return Decimal(0)
    if situation is None:
        situation = situation_des_versements_de(depot_dossiers().lire(dossier))
    return max(penalite * manques - situation.penalites_versees, Decimal(0))


def prendre_un_rendez_vous(dossier: str, mode: ModeRendezVous, debut: datetime) -> RendezVous:
    """Réserve le créneau pour ce dossier. Lève `HTTPException` 409 si c'est impossible."""
    boutique = _comptoir()
    du = penalite_due(dossier) if boutique.rendez_vous.pour(dossier) else Decimal(0)
    if du > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Un rendez-vous précédent a été manqué : la pénalité de {du} FCFA est à "
                "régler au cabinet avant d'en prendre un nouveau."
            ),
        )
    agenda = agenda_de_creation()
    debut = debut.replace(tzinfo=None, second=0, microsecond=0)
    deja = boutique.rendez_vous.depuis(debut - timedelta(days=1))
    try:
        fin = verifier_le_creneau(agenda, deja, debut, mode, _a_douala())
    except CreneauRefuse as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    rdv = RendezVous(
        identifiant=f"rdv-{uuid.uuid4().hex[:16]}",
        dossier=dossier,
        mode=mode,
        debut=debut,
        fin=fin,
        pris_le=maintenant(),
    )
    try:
        boutique.rendez_vous.enregistrer(rdv)
    except CreneauDejaPris as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return rdv


# ── Ce que le site propose ────────────────────────────────────────────────────────


class CreneauxProposes(BaseModel):
    duree_minutes: int
    modes: list[ModeRendezVous]
    #: Les heures de début libres, à Douala.
    creneaux: list[datetime]


@routeur.get(
    "/rendez-vous/creneaux",
    summary="Les créneaux de rendez-vous de création encore libres (publique)",
)
def lire_les_creneaux_libres() -> CreneauxProposes:
    agenda = agenda_de_creation()
    maintenant_a_douala = _a_douala()
    pris = _comptoir().rendez_vous.depuis(maintenant_a_douala - timedelta(days=1))
    return CreneauxProposes(
        duree_minutes=agenda.duree_minutes,
        modes=list(agenda.modes),
        creneaux=creneaux_libres(agenda, pris, maintenant_a_douala),
    )


# ── L'agenda du responsable ───────────────────────────────────────────────────────


class AgendaEnVigueur(BaseModel):
    agenda: AgendaDeCreation
    #: Rien n'a été fixé : c'est la proposition d'ouverture.
    par_defaut: bool


@routeur.get("/agenda", summary="L'agenda des rendez-vous de création")
def lire_l_agenda(acces: AccesRequis) -> AgendaEnVigueur:
    exiger(acces, Permission.LIRE_PROSPECT)
    fixe = _comptoir().agenda_de_creation.lire()
    return AgendaEnVigueur(agenda=fixe or AGENDA_PAR_DEFAUT, par_defaut=fixe is None)


class AgendaAFixer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plages: list[PlageHoraire] = Field(min_length=0, max_length=50)
    duree_minutes: int = Field(ge=15, le=240)
    modes: list[ModeRendezVous] = Field(min_length=0)
    jours_fermes: list[date] = Field(default_factory=list, max_length=200)
    delai_minimal_heures: int = Field(default=24, ge=0, le=168)
    horizon_jours: int = Field(default=14, ge=1, le=90)


def _journal(acces, action: str, apres: dict) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=f"rendez_vous.{action}",
        objet_type="agenda_de_creation",
        objet_id="agenda",
        apres=apres,
    )


@routeur.put("/agenda", summary="Fixer les plages, la durée et les modes des rendez-vous")
def fixer_l_agenda(acces: AccesRequis, demande: AgendaAFixer) -> AgendaEnVigueur:
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    en_vigueur = agenda_de_creation()
    try:
        agenda = AgendaDeCreation(
            plages=tuple(demande.plages),
            duree_minutes=demande.duree_minutes,
            modes=tuple(dict.fromkeys(demande.modes)),
            creneaux_fermes=en_vigueur.creneaux_fermes,
            jours_fermes=tuple(sorted(set(demande.jours_fermes))),
            delai_minimal_heures=demande.delai_minimal_heures,
            horizon_jours=demande.horizon_jours,
            fixe_par=acces.compte,
            fixe_le=maintenant(),
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    _comptoir().agenda_de_creation.enregistrer(agenda)
    _journal(acces, "agenda", {"plages": len(agenda.plages), "duree": agenda.duree_minutes})
    return AgendaEnVigueur(agenda=agenda, par_defaut=False)


class CreneauVise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: L'heure de début, à Douala.
    debut: datetime


def _avec_fermeture(acces, debut: datetime, fermer: bool) -> AgendaEnVigueur:
    agenda = agenda_de_creation()
    debut = debut.replace(tzinfo=None, second=0, microsecond=0)
    fermes = set(agenda.creneaux_fermes)
    if fermer:
        fermes.add(debut)
    else:
        fermes.discard(debut)
    nouvel = agenda.model_copy(
        update={
            "creneaux_fermes": tuple(sorted(fermes)),
            "fixe_par": acces.compte,
            "fixe_le": maintenant(),
        }
    )
    _comptoir().agenda_de_creation.enregistrer(nouvel)
    _journal(acces, "fermeture" if fermer else "reouverture", {"debut": debut.isoformat()})
    return AgendaEnVigueur(agenda=nouvel, par_defaut=False)


@routeur.post("/agenda/fermetures", summary="Fermer un créneau proposé")
def fermer_un_creneau(acces: AccesRequis, corps: CreneauVise) -> AgendaEnVigueur:
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    return _avec_fermeture(acces, corps.debut, fermer=True)


@routeur.post("/agenda/reouvertures", summary="Rouvrir un créneau fermé")
def rouvrir_un_creneau(acces: AccesRequis, corps: CreneauVise) -> AgendaEnVigueur:
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    return _avec_fermeture(acces, corps.debut, fermer=False)


# ── Les rendez-vous pris ──────────────────────────────────────────────────────────


class RendezVousDuCabinet(BaseModel):
    rendez_vous: RendezVous
    client: str
    telephone: str
    courriel: str | None
    service: str


def _dossier(reference: str) -> DossierCommercial:
    try:
        return depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


def _du_cabinet(rdv: RendezVous) -> RendezVousDuCabinet:
    dossier = _dossier(rdv.dossier)
    return RendezVousDuCabinet(
        rendez_vous=rdv,
        client=dossier.demande.nom,
        telephone=dossier.demande.telephone,
        courriel=dossier.demande.courriel,
        service=dossier.service,
    )


@routeur.get("/rendez-vous", summary="Les rendez-vous de création, depuis une semaine")
def lire_les_rendez_vous(acces: AccesRequis) -> list[RendezVousDuCabinet]:
    exiger(acces, Permission.LIRE_PROSPECT)
    depuis = datetime.combine(_a_douala().date() - timedelta(days=7), time(0))
    return [_du_cabinet(r) for r in _comptoir().rendez_vous.depuis(depuis)]


class ConstatDuRendezVous(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Ce que le cabinet constate : HONORE, MANQUE ou ANNULE.
    issue: EtatRendezVous
    motif: str | None = Field(default=None, max_length=500)


def _rendez_vous(identifiant: str) -> RendezVous:
    rdv = _comptoir().rendez_vous.lire(identifiant)
    if rdv is None:
        raise HTTPException(status_code=404, detail=f"rendez-vous {identifiant} introuvable.")
    return rdv


@routeur.post(
    "/rendez-vous/{identifiant}/constat",
    summary="Constater un rendez-vous honoré ou manqué, ou l'annuler",
)
def constater_le_rendez_vous(
    acces: AccesRequis, identifiant: str, corps: ConstatDuRendezVous
) -> RendezVousDuCabinet:
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    rdv = _rendez_vous(identifiant)
    if corps.issue is EtatRendezVous.PREVU:
        raise HTTPException(status_code=422, detail="Un constat dit honoré, manqué ou annulé.")
    if rdv.etat is not EtatRendezVous.PREVU:
        raise HTTPException(status_code=409, detail=f"Ce rendez-vous est déjà {rdv.etat}.")
    if corps.issue is EtatRendezVous.MANQUE and rdv.debut > _a_douala():
        raise HTTPException(
            status_code=409, detail="Un rendez-vous ne se dit manqué qu'après son heure."
        )
    constate = rdv.model_copy(
        update={
            "etat": corps.issue,
            "constate_le": maintenant(),
            "constate_par": acces.compte,
            "motif": corps.motif,
        }
    )
    _comptoir().rendez_vous.enregistrer(constate)
    _journal(acces, "constat", {"rendez_vous": identifiant, "etat": corps.issue.value})
    return _du_cabinet(constate)


class LienDeVisio(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lien: str = Field(min_length=8, max_length=500, pattern=r"^https://")


@routeur.put(
    "/rendez-vous/{identifiant}/lien", summary="Poser le lien de visioconférence d'un rendez-vous"
)
def poser_le_lien(acces: AccesRequis, identifiant: str, corps: LienDeVisio) -> RendezVousDuCabinet:
    exiger(acces, Permission.QUALIFIER_PROSPECT)
    rdv = _rendez_vous(identifiant)
    if rdv.mode is not ModeRendezVous.EN_LIGNE:
        raise HTTPException(status_code=409, detail="Ce rendez-vous se tient au cabinet.")
    pose = rdv.model_copy(update={"lien": corps.lien})
    _comptoir().rendez_vous.enregistrer(pose)
    return _du_cabinet(pose)


# ── La pénalité ───────────────────────────────────────────────────────────────────


class PenaliteAuGuichet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_externe: str = Field(min_length=1, max_length=120)


@routeur.post(
    "/dossiers/{reference}/penalite/encaissement",
    summary="Constater la pénalité d'un rendez-vous manqué, versée au guichet",
    status_code=201,
)
def encaisser_la_penalite(
    acces: AccesRequis, reference: str, corps: PenaliteAuGuichet
) -> SituationDesVersements:
    exiger(acces, Permission.ENCAISSER)
    from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
        regle_des_versements,
    )

    dossier = _dossier(reference)
    regle = regle_des_versements()
    if regle.penalite_rendez_vous is None or penalite_due(reference) <= 0:
        raise HTTPException(status_code=409, detail="Aucune pénalité n'est due sur ce dossier.")
    try:
        resultat = recevoir_un_versement(
            dossier=dossier,
            proforma=proforma_a_regler(depot_proformas().par_dossier(reference)),
            versements=_comptoir().versements_du_parcours,
            regle=regle,
            nature=NatureVersement.PENALITE_RENDEZ_VOUS,
            montant=regle.penalite_rendez_vous,
            canal=CanalDeReglement.GUICHET,
            reference_externe=f"guichet:{corps.reference_externe.strip()}",
            constate_par=acces.compte,
            a_l_instant=maintenant(),
            boite=boite_du_parcours(),
        )
    except (VersementRefuse, VersementDejaRecu) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return resultat.situation
