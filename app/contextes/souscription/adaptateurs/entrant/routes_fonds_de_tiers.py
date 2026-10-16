"""Les fonds qu'un adhérent confie au cabinet pour payer l'administration.

─────────────────────────────────────────────────────────────────────────────────
DÉCISION DU CABINET, 14 OCTOBRE 2026 : sous mandat de paiement, l'adhérent remet
les fonds de sa TVA ou de sa CNPS au cabinet, par mobile money ou au guichet, et
le cabinet reverse puis porte la quittance.

LES GARDIENS

* arrêter le montant à appeler : `VERIFIER_UN_REGLEMENT` sur ce dossier (comptable,
  réviseur), et **seulement sous mandat de paiement** ;
* lire les appels : `LIRE_DOSSIER` sur ce dossier ;
* remettre ses fonds par mobile money : `CONFIER_SES_FONDS`, l'adhérent ;
* la caisse, et le constat au guichet : `ENCAISSER` ;
* reverser à l'administration : `VERIFIER_UN_REGLEMENT` sur ce dossier.

⚠️ Celle qui encaisse ne fixe pas le montant, et ne déclare pas l'administration
payée : la quittance de l'administration est portée par celui qui tient le dossier.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contextes.portefeuille.api import (
    DepotEntreprisesSql,
    EntrepriseIntrouvable,
    entreprises_en_memoire,
)
from app.contextes.souscription.application.fonds_de_tiers import (
    constater_le_reversement,
    constater_les_fonds,
)
from app.contextes.souscription.domaine.fonds_de_tiers import (
    AppelDeFonds,
    EtatAppelDeFonds,
    TransitionDeFondsRefusee,
    identifiant_d_appel,
)
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.paiements import NaturePaiement, Paiement, debit_en_attente
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    exiger,
    exiger_dossier,
    session_de_travail,
)
from app.partage import horloge
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import courant
from app.partage.telephone import NumeroInvalide, normaliser_telephone

__all__ = ["routeur"]

routeur = APIRouter(prefix="/souscription/fonds-de-tiers", tags=["Fonds de tiers"])

_RECENTS = timedelta(days=31)


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _boite():
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        _boite_du_parcours,
    )

    return _boite_du_parcours()


def _entreprise(niu: str):
    session = session_de_travail()
    depot = entreprises_en_memoire() if session is None else DepotEntreprisesSql(session, courant())
    try:
        return depot.lire(niu)
    except EntrepriseIntrouvable as absente:
        raise HTTPException(status_code=404, detail=f"dossier {niu} introuvable.") from absente


def _appel(niu: str, identifiant: str) -> AppelDeFonds:
    appel = _comptoir().appels_de_fonds.lire(identifiant)
    # ⚠️ Un appel d'un autre dossier rend la même réponse qu'un appel inconnu.
    if appel is None or appel.niu != niu:
        raise HTTPException(status_code=404, detail="Appel de fonds introuvable.")
    return appel


class MesFondsConfies(BaseModel):
    #: Les obligations que le cabinet paie pour l'adhérent, aujourd'hui. Vide : aucun mandat.
    obligations_mandatees: list[str]
    appels: list[AppelDeFonds]


# ── La caisse (avant `/{niu}`, sinon « caisse » serait lu comme un NIU) ──────


@routeur.get("/caisse", summary="Les fonds de tiers à recevoir, et reçus récemment")
def caisse_des_fonds(acces: AccesRequis) -> list[AppelDeFonds]:
    exiger(acces, Permission.ENCAISSER)
    aujourd_hui = horloge.aujourd_hui()
    appels = _comptoir().appels_de_fonds.lister()
    dus = [a for a in appels if a.etat is EtatAppelDeFonds.A_REGLER]
    recus = [
        a
        for a in appels
        if a.etat is not EtatAppelDeFonds.A_REGLER
        and a.fonds_recus_le is not None
        and aujourd_hui - jour_a_douala(a.fonds_recus_le) <= _RECENTS
    ]
    return sorted(dus, key=lambda a: a.periode_debut) + sorted(
        recus, key=lambda a: a.fonds_recus_le, reverse=True
    )


# ── Un dossier ────────────────────────────────────────────────────────────────


@routeur.get("/{niu}", summary="Ce que le cabinet paie pour l'adhérent, et ses appels de fonds")
def fonds_du_dossier(acces: AccesRequis, niu: str) -> MesFondsConfies:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    entreprise = _entreprise(niu)
    aujourd_hui = horloge.aujourd_hui()
    mandatees = sorted(
        {
            code
            for m in entreprise.mandats_de_paiement
            if m.couvre(aujourd_hui)
            for code in m.obligations
        }
    )
    return MesFondsConfies(
        obligations_mandatees=mandatees, appels=_comptoir().appels_de_fonds.pour(niu)
    )


class AppelAArreter(BaseModel):
    code_obligation: str = Field(min_length=1)
    libelle: str = Field(min_length=1, max_length=200)
    periode_debut: date
    periode_fin: date
    montant: Decimal = Field(gt=0)


@routeur.post(
    "/{niu}/appels", summary="Arrêter le montant à appeler pour une échéance", status_code=201
)
def arreter_un_appel(acces: AccesRequis, niu: str, corps: AppelAArreter) -> AppelDeFonds:
    """⚠️ SANS MANDAT DE PAIEMENT, AUCUN APPEL. Détenir les fonds d'un adhérent sans son
    accord écrit, c'est détenir de l'argent sans qualité."""
    exiger_dossier(acces, Permission.VERIFIER_UN_REGLEMENT, niu)
    entreprise = _entreprise(niu)
    if not entreprise.paiement_mandate(corps.code_obligation, corps.periode_debut):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{entreprise.denomination} n'a pas mandaté le cabinet pour payer "
                f"{corps.code_obligation} à cette date : il règle lui-même l'administration "
                "et envoie sa quittance."
            ),
        )
    boutique = _comptoir()
    identifiant = identifiant_d_appel(niu, corps.code_obligation, corps.periode_debut)
    if boutique.appels_de_fonds.lire(identifiant) is not None:
        raise HTTPException(status_code=409, detail="Un appel existe déjà pour cette échéance.")
    appel = AppelDeFonds(
        identifiant=identifiant,
        niu=niu,
        denomination=entreprise.denomination,
        code_obligation=corps.code_obligation,
        libelle=corps.libelle,
        periode_debut=corps.periode_debut,
        periode_fin=corps.periode_fin,
        montant=corps.montant,
        arrete_par=acces.compte,
        arrete_le=maintenant(),
    )
    boutique.appels_de_fonds.enregistrer(appel)
    return appel


class DemandeDeDebitDeFonds(BaseModel):
    telephone: str = Field(min_length=9, max_length=20)


class DebitDeFondsDemande(BaseModel):
    """⚠️ `accepte` veut dire « l'opérateur a envoyé la demande », PAS « reçu »."""

    accepte: bool
    message: str
    telephone: str
    montant: Decimal
    simule: bool


@routeur.post(
    "/{niu}/appels/{identifiant}/paiement-en-ligne",
    summary="Remettre ses fonds au cabinet par mobile money",
    status_code=202,
)
def confier_en_ligne(
    acces: AccesRequis, niu: str, identifiant: str, corps: DemandeDeDebitDeFonds
) -> DebitDeFondsDemande:
    exiger_dossier(acces, Permission.CONFIER_SES_FONDS, niu)
    appel = _appel(niu, identifiant)
    if appel.etat is not EtatAppelDeFonds.A_REGLER:
        raise HTTPException(status_code=409, detail="Les fonds de cette échéance sont déjà reçus.")
    try:
        telephone = normaliser_telephone(corps.telephone)
    except NumeroInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus

    from app.contextes.souscription.application.engagement import nouvelle_cle_idempotence

    boutique = _comptoir()
    if debit_en_attente(
        boutique.paiements.par_souscription(appel.identifiant),
        echeance=None,
        a_l_instant=maintenant(),
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Un débit est déjà en attente de confirmation sur votre téléphone pour cette "
                "échéance. Validez-le, ou réessayez dans une demi-heure."
            ),
        )
    paiement = Paiement(
        identifiant=f"pay-{uuid.uuid4().hex[:16]}",
        nature=NaturePaiement.FONDS_DE_TIERS,
        reference_reglee=appel.identifiant,
        cle_idempotence=nouvelle_cle_idempotence(),
        montant=appel.montant,
        telephone=telephone,
        initie_le=maintenant(),
    )
    boutique.paiements.enregistrer(paiement)
    initiation = boutique.fournisseur.initier(
        montant=paiement.montant,
        telephone=paiement.telephone,
        cle_idempotence=paiement.cle_idempotence,
        libelle=f"{appel.libelle} {appel.periode_debut:%m/%Y}, {appel.denomination}",
    )
    if initiation.reference_externe:
        boutique.paiements.enregistrer(paiement.avec_reference(initiation.reference_externe))
    return DebitDeFondsDemande(
        accepte=initiation.accepte,
        message=initiation.message,
        telephone=f"…{telephone[-4:]}",
        montant=paiement.montant,
        simule=bool(boutique.fournisseur.simule),
    )


class ConstatDeFonds(BaseModel):
    reference_externe: str = Field(min_length=3, max_length=120)


@routeur.post(
    "/{niu}/appels/{identifiant}/encaissement",
    summary="Constater au guichet les fonds remis pour l'administration",
)
def recevoir_au_guichet(
    acces: AccesRequis, niu: str, identifiant: str, corps: ConstatDeFonds
) -> AppelDeFonds:
    exiger(acces, Permission.ENCAISSER)
    try:
        return constater_les_fonds(
            _comptoir().appels_de_fonds,
            _boite(),
            _appel(niu, identifiant),
            canal=CanalDeReglement.GUICHET,
            reference=corps.reference_externe.strip(),
            a_l_instant=maintenant(),
            par=acces.compte,
        )
    except TransitionDeFondsRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus


class Reversement(BaseModel):
    #: La pièce qui porte la quittance de l'administration, déposée au dossier.
    quittance: str = Field(min_length=1)


@routeur.post(
    "/{niu}/appels/{identifiant}/reversement",
    summary="Porter la quittance de l'administration : l'échéance est payée",
)
def reverser(acces: AccesRequis, niu: str, identifiant: str, corps: Reversement) -> AppelDeFonds:
    exiger_dossier(acces, Permission.VERIFIER_UN_REGLEMENT, niu)
    # ⚠️ La quittance doit être une pièce DE CE DOSSIER : sinon la quittance d'un autre
    # adhérent rendrait « payée » une échéance que l'administration n'a pas reçue.
    from app.contextes.collecte.api import DepotPiecesSql, pieces_en_memoire

    session = session_de_travail()
    pieces = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
    piece = pieces.par_identifiant(corps.quittance)
    if piece is None or piece.entreprise != niu:
        raise HTTPException(status_code=404, detail=f"pièce {corps.quittance} introuvable.")
    try:
        return constater_le_reversement(
            _comptoir().appels_de_fonds,
            _boite(),
            _appel(niu, identifiant),
            quittance=corps.quittance,
            a_l_instant=maintenant(),
            par=acces.compte,
        )
    except TransitionDeFondsRefusee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
