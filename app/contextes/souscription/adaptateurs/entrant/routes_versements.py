"""Les versements du parcours : la règle, la situation d'un dossier, les frais au guichet.

─────────────────────────────────────────────────────────────────────────────────
9 NOVEMBRE 2026 (réunion du 4 novembre, demande 21, et précision de l'utilisateur)

* `GET/PUT /souscription/versements/regle` : les frais de traitement par service et
  le seuil de démarrage. Lus par le cabinet ; fixés par le super administrateur seul
  (`ACCORDER_FAVEURS`), avec un motif, au journal ;
* `GET /acquisition/dossiers/{reference}/versements` : payé, reste, soldé, seuil ;
* `POST /acquisition/dossiers/{reference}/frais-de-traitement/encaissement` : la caisse
  constate des frais versés au guichet (`ENCAISSER`).

Les tranches de la proforma passent par les routes d'avant ce lot (`/reglement`,
`/encaissement`, `/paiement-en-ligne`), qui acceptent désormais un montant.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.souscription.adaptateurs.entrant.sources import (
    boite_du_parcours,
    depot_dossiers,
    depot_proformas,
)
from app.contextes.souscription.adaptateurs.sortant.depots_memoire import DossierIntrouvable
from app.contextes.souscription.application.versements import (
    proforma_a_regler,
    recevoir_un_versement,
    situation_du_dossier,
)
from app.contextes.souscription.domaine.dossier_commercial import DossierCommercial
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.versements_du_parcours import (
    REGLE_SANS_FRAIS,
    NatureVersement,
    RegleDesVersements,
    SituationDesVersements,
    VersementDejaRecu,
    VersementRefuse,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

__all__ = [
    "regle_des_versements",
    "routeur_acquisition",
    "routeur_souscription",
    "situation_des_versements_de",
]

routeur_souscription = APIRouter(prefix="/souscription", tags=["Versements du parcours"])
routeur_acquisition = APIRouter(prefix="/acquisition", tags=["Versements du parcours"])


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def regle_des_versements() -> RegleDesVersements:
    return _comptoir().regle_des_versements.lire() or REGLE_SANS_FRAIS


def situation_des_versements_de(dossier: DossierCommercial) -> SituationDesVersements:
    """Pour les autres routes du parcours : la caisse, la consultation, le débit."""
    proforma = proforma_a_regler(depot_proformas().par_dossier(dossier.reference))
    return situation_du_dossier(
        dossier,
        proforma,
        _comptoir().versements_du_parcours.pour(dossier.reference),
        regle_des_versements(),
    )


# ── La règle ──────────────────────────────────────────────────────────────────────


class RegleDesVersementsEnVigueur(BaseModel):
    regle: RegleDesVersements
    #: Aucune règle fixée : pas de frais, règlement complet avant le démarrage.
    par_defaut: bool


@routeur_souscription.get("/versements/regle", summary="Frais de traitement et seuil de démarrage")
def lire_la_regle_des_versements(acces: AccesRequis) -> RegleDesVersementsEnVigueur:
    if not acces.detient(Permission.ACCORDER_FAVEURS):
        exiger(acces, Permission.LIRE_PROSPECT)
    fixee = _comptoir().regle_des_versements.lire()
    return RegleDesVersementsEnVigueur(regle=fixee or REGLE_SANS_FRAIS, par_defaut=fixee is None)


class RegleDesVersementsAFixer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Le montant par code de service ; un service absent n'a pas de frais.
    frais_par_service: dict[str, Decimal] = Field(default_factory=dict)
    seuil_demarrage_pourcent: int = Field(ge=1, le=100)
    #: 9 novembre 2026 : la pénalité d'un rendez-vous manqué. Absente : aucune.
    penalite_rendez_vous: Decimal | None = Field(default=None, gt=0)
    motif: str = Field(min_length=10, max_length=500)


@routeur_souscription.put(
    "/versements/regle", summary="Fixer les frais de traitement et le seuil de démarrage"
)
def fixer_la_regle_des_versements(
    acces: AccesRequis, demande: RegleDesVersementsAFixer
) -> RegleDesVersementsEnVigueur:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=demande.motif)
    try:
        regle = RegleDesVersements(
            frais_par_service=demande.frais_par_service,
            seuil_demarrage_pourcent=demande.seuil_demarrage_pourcent,
            penalite_rendez_vous=demande.penalite_rendez_vous,
            motif=demande.motif,
            fixee_par=acces.compte,
            fixee_le=maintenant(),
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    _comptoir().regle_des_versements.enregistrer(regle)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="versements.regle",
        objet_type="versements",
        objet_id="regle-des-versements",
        apres={
            "frais_par_service": {k: str(v) for k, v in regle.frais_par_service.items()},
            "seuil_demarrage_pourcent": regle.seuil_demarrage_pourcent,
            "penalite_rendez_vous": (
                str(regle.penalite_rendez_vous) if regle.penalite_rendez_vous else None
            ),
        },
        motif=demande.motif,
    )
    return RegleDesVersementsEnVigueur(regle=regle, par_defaut=False)


# ── La situation d'un dossier ─────────────────────────────────────────────────────


def _dossier(reference: str) -> DossierCommercial:
    try:
        return depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


@routeur_acquisition.get(
    "/dossiers/{reference}/versements",
    summary="Ce qui est versé sur un dossier, et ce qu'il reste à payer",
)
def lire_les_versements(acces: AccesRequis, reference: str) -> SituationDesVersements:
    if not acces.detient(Permission.ENCAISSER):
        exiger(acces, Permission.LIRE_PROSPECT)
    return situation_des_versements_de(_dossier(reference))


class FraisAuGuichet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Le numéro du reçu de caisse : il empêche de compter deux fois le même versement.
    reference_externe: str = Field(min_length=1, max_length=120)


@routeur_acquisition.post(
    "/dossiers/{reference}/frais-de-traitement/encaissement",
    summary="Constater des frais de traitement versés au guichet",
    status_code=201,
)
def encaisser_les_frais(
    acces: AccesRequis, reference: str, corps: FraisAuGuichet
) -> SituationDesVersements:
    exiger(acces, Permission.ENCAISSER)
    dossier = _dossier(reference)
    regle = regle_des_versements()
    attendu = regle.frais_de(dossier.service) if dossier.service else None
    if attendu is None:
        raise HTTPException(
            status_code=409,
            detail=f"aucun frais de traitement fixé pour le service {dossier.service or '?'}.",
        )
    proforma = proforma_a_regler(depot_proformas().par_dossier(reference))
    try:
        resultat = recevoir_un_versement(
            dossier=dossier,
            proforma=proforma,
            versements=_comptoir().versements_du_parcours,
            regle=regle,
            nature=NatureVersement.FRAIS_DE_TRAITEMENT,
            montant=attendu,
            canal=CanalDeReglement.GUICHET,
            reference_externe=f"guichet:{corps.reference_externe.strip()}",
            constate_par=acces.compte,
            a_l_instant=maintenant(),
            boite=boite_du_parcours(),
        )
    except (VersementRefuse, VersementDejaRecu) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    if resultat.engagement is not None and not resultat.engagement.rejeu:
        depot_dossiers().enregistrer(resultat.dossier)
    return resultat.situation


class RestitutionAuGuichet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Le reçu signé par le client, ou la référence du transfert retour.
    reference_externe: str = Field(min_length=1, max_length=120)
    motif: str = Field(min_length=5, max_length=500)


@routeur_acquisition.post(
    "/dossiers/{reference}/trop-percu/restitution",
    summary="Constater qu'un trop-perçu a été rendu au client",
    status_code=201,
)
def restituer_le_trop_percu(
    acces: AccesRequis, reference: str, corps: RestitutionAuGuichet
) -> SituationDesVersements:
    """9 novembre 2026 : l'argent reçu de l'opérateur que rien n'absorbait (au-delà du
    reste, proforma soldée, dossier classé) est rendu au client ; la caisse le constate,
    pour tout le trop-perçu en attente, avec un motif au journal."""
    exiger(acces, Permission.ENCAISSER, motif=corps.motif)
    dossier = _dossier(reference)
    situation = situation_des_versements_de(dossier)
    if situation.trop_percu <= 0:
        raise HTTPException(status_code=409, detail="Aucun trop-perçu n'attend sur ce dossier.")
    try:
        resultat = recevoir_un_versement(
            dossier=dossier,
            proforma=proforma_a_regler(depot_proformas().par_dossier(reference)),
            versements=_comptoir().versements_du_parcours,
            regle=regle_des_versements(),
            nature=NatureVersement.RESTITUTION,
            montant=situation.trop_percu,
            canal=CanalDeReglement.GUICHET,
            reference_externe=f"restitution:{corps.reference_externe.strip()}",
            constate_par=acces.compte,
            a_l_instant=maintenant(),
            boite=boite_du_parcours(),
        )
    except (VersementRefuse, VersementDejaRecu) as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="versements.restitution",
        objet_type="dossier",
        objet_id=reference,
        apres={"montant": str(situation.trop_percu)},
        motif=corps.motif,
    )
    return resultat.situation
