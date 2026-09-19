"""API de la revue d'un mois transmis (pas 102), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
QUI FAIT QUOI

* lire les revues : `LIRE_COMPTABILITE`, restreint au périmètre ;
* transmettre, répondre à une remarque, retransmettre : `SAISIR_ECRITURE`, le comptable ;
* poser et clore une remarque, renvoyer, valider : `REVISER_DOSSIER`, le réviseur seul.

La revue retient les **noms** (« transmis par Léonard FOTSO ») ; chaque passage de relais
retient aussi le **compte**, pour que la notification arrive à la bonne personne. Le journal
d'audit porte le compte comme acteur : c'est lui qui fait foi.

Les passages de relais sont annoncés par les notifications (pas 94), sans une ligne de code
de plus : un abonnement par action dans `notifications/abonnements.yaml`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.contextes.comptabilite.adaptateurs.entrant.routes_cloture_mensuelle import (
    points_de_cloture_du_mois,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_http import _journaux, depot
from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
    depot_des_rapprochements,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues
from app.contextes.comptabilite.adaptateurs.sortant.revue_yaml import (
    charger_les_reglages_de_l_echantillon,
)
from app.contextes.comptabilite.application.revue import (
    PointDeControle,
    du_mois,
    exiger_une_cloture_sans_point_bloquant,
    points_de_controle,
    remarquer,
    transmettre_un_mois,
)
from app.contextes.comptabilite.domaine.entites import EtatEcriture, NatureJournal
from app.contextes.comptabilite.domaine.revue import (
    NatureObjet,
    ObjetDeRemarque,
    RevueDeDossier,
    RevueRefusee,
    StatutRemarque,
    StatutRevue,
    echantillonner,
)
from app.contextes.transverse.api import (
    Acces,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    restreindre,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · revue"])


def _reglages():
    return charger_les_reglages_de_l_echantillon(configuration().dossier_referentiel)


def _refus(erreur: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(erreur))


def _revue(dossier: str, identifiant: str) -> RevueDeDossier:
    for revue in depot_des_revues().du_dossier(dossier):
        if revue.identifiant == identifiant:
            return revue
    raise HTTPException(status_code=404, detail=f"revue « {identifiant} » inconnue.")


def _journaliser(acces: Acces, action: str, revue: RevueDeDossier, **donnees) -> None:
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="revue_de_dossier",
        objet_id=revue.identifiant,
        apres={
            "dossier": revue.dossier,
            "identifiant": revue.identifiant,
            "periode": f"du {revue.du:%d/%m/%Y} au {revue.au:%d/%m/%Y}",
            "par": acces.nom_complet,
            # Le compte de celui qui a transmis : c'est lui qui reçoit renvois et validation.
            "comptable": revue.transmise_par_compte,
            "remarques_ouvertes": revue.compter(StatutRemarque.OUVERTE),
            **donnees,
        },
    )


# ── Ce que les écrans lisent ──────────────────────────────────────────────────


class ResumeDeRevue(BaseModel):
    identifiant: str
    dossier: str
    du: date
    au: date
    statut: StatutRevue
    transmise_par: str
    transmise_par_compte: str
    ouvertes: int
    traitees: int
    closes: int
    echantillon: int
    ecritures_du_mois: int


class EcritureDuMois(BaseModel):
    """De quoi choisir l'objet d'une remarque sans ouvrir le journal."""

    cle: str
    date: date
    libelle: str
    montant: Decimal
    piece: str | None
    comptes: list[str]


class VueRevue(BaseModel):
    revue: RevueDeDossier
    points: list[PointDeControle]
    ecritures: list[EcritureDuMois]
    #: Pour l'écran : la session peut-elle valider ? (réviseur, et pas celui qui a transmis)
    auteur_de_la_transmission: bool


def _resume(r: RevueDeDossier) -> ResumeDeRevue:
    return ResumeDeRevue(
        identifiant=r.identifiant,
        dossier=r.dossier,
        du=r.du,
        au=r.au,
        statut=r.statut,
        transmise_par=r.transmise_par,
        transmise_par_compte=r.transmise_par_compte,
        ouvertes=r.compter(StatutRemarque.OUVERTE),
        traitees=r.compter(StatutRemarque.TRAITEE),
        closes=r.compter(StatutRemarque.CLOSE),
        echantillon=len(r.echantillon),
        ecritures_du_mois=r.ecritures_du_mois,
    )


def _vue(acces: Acces, r: RevueDeDossier) -> VueRevue:
    exercice = depot(r.dossier).lister(r.exercice)
    banques = {
        j.code: j.compte_contrepartie
        for j in _journaux.charger()
        if j.nature is NatureJournal.BANQUE and j.compte_contrepartie
    }
    return VueRevue(
        revue=r,
        points=points_de_controle(
            r, exercice, banques, depot_des_rapprochements().du_dossier(r.dossier)
        ),
        ecritures=[
            EcritureDuMois(
                cle=e.cle,
                date=e.date_operation,
                libelle=e.libelle,
                montant=e.montant,
                piece=e.piece_justificative,
                comptes=sorted({l_.compte for l_ in e.lignes}),
            )
            for e in sorted(du_mois(exercice, r.du, r.au), key=lambda e: (e.date_operation, e.cle))
        ],
        auteur_de_la_transmission=acces.compte == r.transmise_par_compte,
    )


@routeur.get("/revues", summary="Les mois transmis ou renvoyés du périmètre : la file de revue")
def lister_les_revues(
    acces: AccesRequis, statut: StatutRevue | None = Query(None)
) -> list[ResumeDeRevue]:
    """La file du réviseur (`statut=TRANSMISE`) et celle du comptable (`statut=RENVOYEE`).

    Les plus anciennes d'abord : un mois qui attend depuis longtemps passe devant.
    """
    exiger(acces, Permission.LIRE_COMPTABILITE)
    revues = restreindre(acces, depot_des_revues().toutes(), lambda r: r.dossier)
    return [_resume(r) for r in revues if statut is None or r.statut is statut]


@routeur.get(
    "/dossiers/{entreprise}/revues/{identifiant}",
    summary="Une revue : points de contrôle, échantillon, remarques",
)
def lire_la_revue(acces: AccesRequis, entreprise: str, identifiant: str) -> VueRevue:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    return _vue(acces, _revue(entreprise, identifiant))


class DemandeDeTransmission(BaseModel):
    du: date
    au: date
    exercice: str | None = Field(None, description="Défaut : l'année de début")
    message: str | None = Field(None, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/revues",
    summary="Transmettre un mois au réviseur",
    status_code=201,
)
def transmettre(acces: AccesRequis, entreprise: str, demande: DemandeDeTransmission) -> VueRevue:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    exercice = demande.exercice or str(demande.du.year)
    # ⚠️ Pas 107 : les points de la clôture mensuelle, calculés par la même fonction que
    # l'écran de clôture. Un point bloquant refuse la transmission, qui verrouillerait le mois.
    points, _chiffres, _reglages_cloture = points_de_cloture_du_mois(
        entreprise, demande.du, demande.au, exercice
    )
    try:
        revue = transmettre_un_mois(
            dossier=entreprise,
            exercice=exercice,
            du=demande.du,
            au=demande.au,
            ecritures_de_l_exercice=depot(entreprise).lister(exercice),
            reglages=_reglages(),
            par=acces.nom_complet,
            compte=acces.compte,
            le=maintenant(),
            message=(demande.message or "").strip() or None,
            depot=depot_des_revues(),
            points_de_cloture=points,
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    _journaliser(acces, "comptabilite.mois_transmis", revue)
    return _vue(acces, revue)


class DemandeDeRemarque(BaseModel):
    nature: NatureObjet
    reference: str = Field(min_length=1, max_length=64)
    texte: str = Field(min_length=1, max_length=1000)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/remarques",
    summary="Poser une remarque rattachée à une écriture, une pièce ou un compte",
)
def poser_une_remarque(
    acces: AccesRequis, entreprise: str, identifiant: str, demande: DemandeDeRemarque
) -> VueRevue:
    exiger_dossier(acces, Permission.REVISER_DOSSIER, entreprise)
    r = _revue(entreprise, identifiant)
    if len(demande.texte.strip()) < 10:
        raise HTTPException(
            status_code=422,
            detail="la remarque compte au moins 10 caractères : dire ce qui ne va pas.",
        )
    try:
        remarquee = remarquer(
            r,
            objet=ObjetDeRemarque(nature=demande.nature, reference=demande.reference),
            texte=demande.texte,
            ecritures_de_l_exercice=depot(entreprise).lister(r.exercice),
            par=acces.nom_complet,
            le=maintenant(),
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(remarquee)
    return _vue(acces, remarquee)


class DemandeDeReponse(BaseModel):
    reponse: str = Field(min_length=1, max_length=1000)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/remarques/{rang}/reponse",
    summary="Répondre à une remarque du réviseur",
)
def repondre(
    acces: AccesRequis, entreprise: str, identifiant: str, rang: int, demande: DemandeDeReponse
) -> VueRevue:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    try:
        repondue = _revue(entreprise, identifiant).repondre(
            rang, demande.reponse, par=acces.nom_complet, le=maintenant()
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(repondue)
    return _vue(acces, repondue)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/remarques/{rang}/cloture",
    summary="Clore une remarque satisfaite",
)
def clore_une_remarque(
    acces: AccesRequis, entreprise: str, identifiant: str, rang: int
) -> VueRevue:
    exiger_dossier(acces, Permission.REVISER_DOSSIER, entreprise)
    try:
        close = _revue(entreprise, identifiant).clore_la_remarque(
            rang, par=acces.nom_complet, le=maintenant()
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(close)
    return _vue(acces, close)


class DemandeDePassage(BaseModel):
    message: str | None = Field(None, max_length=500)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/renvoi",
    summary="Renvoyer le mois au comptable avec les remarques",
)
def renvoyer(
    acces: AccesRequis, entreprise: str, identifiant: str, demande: DemandeDePassage
) -> VueRevue:
    exiger_dossier(acces, Permission.REVISER_DOSSIER, entreprise)
    try:
        renvoyee = _revue(entreprise, identifiant).renvoyer(
            par=acces.nom_complet,
            compte=acces.compte,
            le=maintenant(),
            message=(demande.message or "").strip() or None,
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(renvoyee)
    _journaliser(acces, "comptabilite.mois_renvoye", renvoyee)
    return _vue(acces, renvoyee)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/retransmission",
    summary="Retransmettre le mois, chaque remarque ayant sa réponse",
)
def retransmettre(
    acces: AccesRequis, entreprise: str, identifiant: str, demande: DemandeDePassage
) -> VueRevue:
    exiger_dossier(acces, Permission.SAISIR_ECRITURE, entreprise)
    r = _revue(entreprise, identifiant)
    exercice = depot(entreprise).lister(r.exercice)
    mois = du_mois(exercice, r.du, r.au)
    brouillons = sorted(e.cle for e in mois if e.etat is EtatEcriture.BROUILLON)
    if brouillons:
        raise HTTPException(
            status_code=422,
            detail=f"écriture(s) en brouillon sur la période ({', '.join(brouillons[:5])}) : "
            "les valider avant de retransmettre.",
        )
    points, _chiffres, _reglages_cloture = points_de_cloture_du_mois(
        entreprise, r.du, r.au, r.exercice
    )
    try:
        # ⚠️ Pas 107 : un mois renvoyé n'est plus verrouillé, le comptable l'a corrigé ;
        # le retransmettre le verrouille à nouveau, aux mêmes conditions que la première fois.
        exiger_une_cloture_sans_point_bloquant(points)
        retransmise = r.retransmettre(
            par=acces.nom_complet,
            compte=acces.compte,
            le=maintenant(),
            echantillon=echantillonner(mois, exercice, _reglages()),
            ecritures_du_mois=len(mois),
            message=(demande.message or "").strip() or None,
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(retransmise)
    _journaliser(acces, "comptabilite.mois_transmis", retransmise)
    return _vue(acces, retransmise)


@routeur.post(
    "/dossiers/{entreprise}/revues/{identifiant}/validation",
    summary="Valider le mois : le second regard est donné",
)
def valider(acces: AccesRequis, entreprise: str, identifiant: str) -> VueRevue:
    exiger_dossier(acces, Permission.REVISER_DOSSIER, entreprise)
    try:
        validee = _revue(entreprise, identifiant).valider(
            par=acces.nom_complet, compte=acces.compte, le=maintenant()
        )
    except RevueRefusee as refus:
        raise _refus(refus) from refus
    depot_des_revues().enregistrer(validee)
    _journaliser(acces, "comptabilite.mois_valide", validee)
    return _vue(acces, validee)


__all__ = ["routeur"]
