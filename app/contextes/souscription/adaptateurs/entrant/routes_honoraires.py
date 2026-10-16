"""Les honoraires des adhérents : le contrat, l'échéancier, le règlement.

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE CABINET A DEMANDÉ, LE 14 OCTOBRE 2026

« Paiement Tara ou manuel des frais avec changement de statut », depuis l'espace
adhérent. Décidé le même jour : l'échéancier complet, le règlement par mobile
money depuis l'espace, et le règlement au guichet constaté par la caisse.

CINQ ROUTES, ET CHACUNE A SON GARDIEN

* fixer le contrat : `FIXER_LES_TARIFS`, la direction. Un montant d'honoraires
  engage le cabinet envers l'adhérent, comme un prix du catalogue ;
* lire l'échéancier : `LIRE_DOSSIER` **sur ce dossier**. L'adhérent lit le sien,
  le collaborateur ceux de son portefeuille, et hors portée rend 404 ;
* demander le débit : `REGLER_SES_HONORAIRES` sur ce dossier, l'adhérent ;
* la caisse, et le constat au guichet : `ENCAISSER`. Celle qui encaisse ne fixe
  pas le montant, et celle qui le fixe n'encaisse pas.

⚠️ PRÉFIXE `/souscription/honoraires` : le service déclare `/souscription` et
`/acquisition` (`registre/services.py`). Un préfixe nouveau aurait été un
changement du registre pour une commodité de nommage.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.contextes.portefeuille.api import (
    EntrepriseIntrouvable,
    depot_entreprises,
)
from app.contextes.souscription.domaine.honoraires import (
    CanalDeReglement,
    ContratDHonoraires,
    EcheanceDHonoraires,
    EtatEcheanceDHonoraires,
    PeriodeDejaReglee,
    PeriodiciteDHonoraires,
    ReglementDHonoraires,
    echeancier_d_honoraires,
)
from app.contextes.souscription.domaine.paiements import NaturePaiement, Paiement, debit_en_attente
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
)
from app.partage import horloge
from app.partage.horloge import aujourd_hui, jour_a_douala, maintenant
from app.partage.telephone import NumeroInvalide, normaliser_telephone

__all__ = ["routeur"]

routeur = APIRouter(prefix="/souscription/honoraires", tags=["Honoraires des adhérents"])

#: Combien de jours la caisse garde un règlement dans « Réglés récemment ».
_RECENTS = timedelta(days=31)


def _comptoir():
    # Importé à l'appel : `routes_http` monte le comptoir, et ce module est chargé
    # avec l'application.
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _denomination(niu: str) -> str:
    """La dénomination au portefeuille, ou 404 : un contrat ne se fixe que pour
    une entreprise que le cabinet suit."""
    depot = depot_entreprises()
    try:
        return depot.lire(niu).denomination
    except EntrepriseIntrouvable as absente:
        raise HTTPException(status_code=404, detail=f"dossier {niu} introuvable.") from absente


class ContratAFixer(BaseModel):
    montant: Decimal = Field(gt=0)
    periodicite: PeriodiciteDHonoraires
    debut: date


class MesHonoraires(BaseModel):
    """Le contrat, l'échéancier, et ce qu'il faut retenir d'un coup d'œil."""

    contrat: ContratDHonoraires | None
    echeances: list[EcheanceDHonoraires]
    #: Ce qui est exigible aujourd'hui : à régler et en retard.
    du: Decimal
    en_retard: int
    #: Le débit mobile se demande depuis l'espace.
    reglable_en_ligne: bool


def _mes_honoraires(niu: str) -> MesHonoraires:
    boutique = _comptoir()
    contrat = boutique.contrats_honoraires.lire(niu)
    if contrat is None:
        return MesHonoraires(
            contrat=None, echeances=[], du=Decimal(0), en_retard=0, reglable_en_ligne=False
        )
    echeances = echeancier_d_honoraires(
        contrat, boutique.reglements_honoraires.pour(niu), aujourd_hui()
    )
    dues = [
        e
        for e in echeances
        if e.etat in (EtatEcheanceDHonoraires.A_REGLER, EtatEcheanceDHonoraires.EN_RETARD)
    ]
    return MesHonoraires(
        contrat=contrat,
        echeances=echeances,
        du=sum((e.montant for e in dues), Decimal(0)),
        en_retard=sum(1 for e in dues if e.etat is EtatEcheanceDHonoraires.EN_RETARD),
        reglable_en_ligne=True,
    )


def _echeance_non_reglee(niu: str, periode: date) -> tuple[ContratDHonoraires, EcheanceDHonoraires]:
    """La période demandée, si elle existe et n'est pas réglée. 404 ou 409 sinon."""
    vue = _mes_honoraires(niu)
    if vue.contrat is None:
        raise HTTPException(status_code=404, detail="Aucun contrat d'honoraires pour ce dossier.")
    echeance = next((e for e in vue.echeances if e.debut == periode), None)
    if echeance is None:
        raise HTTPException(status_code=404, detail="Aucune échéance ne commence à cette date.")
    if echeance.etat is EtatEcheanceDHonoraires.REGLEE:
        raise HTTPException(status_code=409, detail="Cette période est déjà réglée.")
    return vue.contrat, echeance


# ── La caisse ─────────────────────────────────────────────────────────────────
#
# ⚠️ Déclarée AVANT `/{niu}` : sinon « caisse » serait lu comme un NIU.


class LigneDeCaisseHonoraires(BaseModel):
    niu: str
    denomination: str
    periode: date
    fin: date
    montant: Decimal
    etat: EtatEcheanceDHonoraires
    canal: CanalDeReglement | None = None
    reference_externe: str | None = None
    constate_le: str | None = None


@routeur.get("/caisse", summary="Les honoraires dus et réglés récemment, pour la caisse")
def caisse_des_honoraires(acces: AccesRequis) -> list[LigneDeCaisseHonoraires]:
    """Ce qui est exigible en tête (en retard d'abord), puis ce qui a été réglé
    depuis un mois, quel que soit le canal. Le mobile money y arrive seul."""
    exiger(acces, Permission.ENCAISSER)
    boutique = _comptoir()
    aujourd_hui = horloge.aujourd_hui()
    dues: list[LigneDeCaisseHonoraires] = []
    reglees: list[LigneDeCaisseHonoraires] = []
    for contrat in boutique.contrats_honoraires.lister():
        for e in echeancier_d_honoraires(
            contrat, boutique.reglements_honoraires.pour(contrat.niu), aujourd_hui, a_venir=0
        ):
            ligne = LigneDeCaisseHonoraires(
                niu=contrat.niu,
                denomination=contrat.denomination,
                periode=e.debut,
                fin=e.fin,
                montant=e.montant,
                etat=e.etat,
            )
            if e.reglement is None:
                dues.append(ligne)
            elif aujourd_hui - jour_a_douala(e.reglement.constate_le) <= _RECENTS:
                reglees.append(
                    ligne.model_copy(
                        update={
                            "canal": e.reglement.canal,
                            "reference_externe": e.reglement.reference_externe,
                            "constate_le": e.reglement.constate_le.isoformat(),
                        }
                    )
                )
    dues.sort(key=lambda x: (x.etat is not EtatEcheanceDHonoraires.EN_RETARD, x.periode))
    reglees.sort(key=lambda x: x.constate_le or "", reverse=True)
    return dues + reglees


# ── Un dossier ────────────────────────────────────────────────────────────────


@routeur.get("/{niu}", summary="Le contrat et l'échéancier d'honoraires d'un dossier")
def honoraires_du_dossier(acces: AccesRequis, niu: str) -> MesHonoraires:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return _mes_honoraires(niu)


@routeur.put("/{niu}/contrat", summary="Fixer le contrat d'honoraires d'un adhérent")
def fixer_le_contrat(acces: AccesRequis, niu: str, corps: ContratAFixer) -> MesHonoraires:
    """⚠️ Le nouveau contrat REMPLACE l'ancien pour l'avenir comme pour le passé :
    l'échéancier se recalcule. Les règlements déjà constatés restent en base."""
    exiger(acces, Permission.FIXER_LES_TARIFS)
    contrat = ContratDHonoraires(
        niu=niu,
        denomination=_denomination(niu),
        montant=corps.montant,
        periodicite=corps.periodicite,
        debut=corps.debut,
        fixe_par=acces.compte,
        fixe_le=maintenant(),
    )
    _comptoir().contrats_honoraires.enregistrer(contrat)
    return _mes_honoraires(niu)


class DemandeDeDebit(BaseModel):
    periode: date
    telephone: str = Field(min_length=9, max_length=20)


class DebitDemande(BaseModel):
    """⚠️ `accepte` veut dire « l'opérateur a envoyé la demande », PAS « payé »."""

    accepte: bool
    message: str
    telephone: str
    montant: Decimal
    #: Aucun débit réel : la plateforme n'a pas d'identifiants marchands.
    simule: bool


@routeur.post(
    "/{niu}/paiement-en-ligne",
    summary="Demander le débit mobile money d'une période, depuis son espace",
    status_code=202,
)
def payer_en_ligne(acces: AccesRequis, niu: str, corps: DemandeDeDebit) -> DebitDemande:
    """Crée le paiement, PUIS appelle l'opérateur : écrire avant d'appeler.

    ⚠️ RIEN N'EST RÉGLÉ ICI. C'est la notification de l'opérateur qui constate le
    règlement (`SuiteDHonoraires`) ; la page de l'adhérent se relit et change
    d'elle-même. Un clic n'est pas un paiement.
    """
    exiger_dossier(acces, Permission.REGLER_SES_HONORAIRES, niu)
    contrat, echeance = _echeance_non_reglee(niu, corps.periode)
    try:
        telephone = normaliser_telephone(corps.telephone)
    except NumeroInvalide as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus

    from app.contextes.souscription.application.engagement import nouvelle_cle_idempotence

    boutique = _comptoir()
    if debit_en_attente(
        [
            p
            for p in boutique.paiements.par_souscription(niu)
            if p.nature is NaturePaiement.HONORAIRES
        ],
        echeance=echeance.debut.isoformat(),
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
        nature=NaturePaiement.HONORAIRES,
        reference_reglee=niu,
        echeance=echeance.debut.isoformat(),
        cle_idempotence=nouvelle_cle_idempotence(),
        montant=echeance.montant,
        telephone=telephone,
        initie_le=maintenant(),
    )
    boutique.paiements.enregistrer(paiement)
    initiation = boutique.fournisseur.initier(
        montant=paiement.montant,
        telephone=paiement.telephone,
        cle_idempotence=paiement.cle_idempotence,
        libelle=f"Honoraires {contrat.denomination} du {echeance.debut:%d/%m/%Y}",
    )
    if initiation.reference_externe:
        boutique.paiements.enregistrer(paiement.avec_reference(initiation.reference_externe))
    return DebitDemande(
        accepte=initiation.accepte,
        message=initiation.message,
        telephone=f"…{telephone[-4:]}",
        montant=paiement.montant,
        simule=bool(boutique.fournisseur.simule),
    )


class ConstatAuGuichet(BaseModel):
    periode: date
    reference_externe: str = Field(min_length=3, max_length=120, description="Le numéro du reçu")


@routeur.post("/{niu}/encaissement", summary="Constater un règlement d'honoraires au guichet")
def encaisser_au_guichet(acces: AccesRequis, niu: str, corps: ConstatAuGuichet) -> MesHonoraires:
    """⚠️ Le montant n'est pas saisi : c'est celui de l'échéance. Un montant tapé
    à la main est la première source d'écart de caisse."""
    exiger(acces, Permission.ENCAISSER)
    _, echeance = _echeance_non_reglee(niu, corps.periode)
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        boite_du_parcours,
    )
    from app.contextes.souscription.application.reglement_des_honoraires import (
        constater_le_reglement,
    )

    try:
        # 15 octobre 2026 : écrit ET publié, pour que l'écriture se propose chez l'adhérent.
        constater_le_reglement(
            _comptoir().reglements_honoraires,
            boite_du_parcours(),
            ReglementDHonoraires(
                identifiant=f"RH-{uuid.uuid4().hex[:16]}",
                niu=niu,
                periode=echeance.debut,
                montant=echeance.montant,
                canal=CanalDeReglement.GUICHET,
                reference_externe=corps.reference_externe.strip(),
                constate_le=maintenant(),
                constate_par=acces.compte,
            ),
        )
    except PeriodeDejaReglee as refus:
        raise HTTPException(status_code=409, detail=str(refus)) from refus
    return _mes_honoraires(niu)


@routeur.get(
    "/{niu}/reglements/{identifiant}/recu",
    summary="Le reçu PDF d'un règlement d'honoraires",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def recu_du_reglement(acces: AccesRequis, niu: str, identifiant: str) -> Response:
    """15 octobre 2026 : l'adhérent classe et produit le reçu de ce qu'il a réglé.

    ⚠️ Même garde que son échéancier (son dossier, ou le cabinet) ; le règlement est
    cherché DANS ce dossier : l'identifiant d'un autre adhérent rend 404.
    """
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    from app.contextes.souscription.adaptateurs.sortant.document_recu_honoraires import (
        rendre_le_recu,
    )
    from app.partage.formats import MOIS

    vue = _mes_honoraires(niu)
    echeance = next(
        (e for e in vue.echeances if e.reglement and e.reglement.identifiant == identifiant),
        None,
    )
    if echeance is None:
        raise HTTPException(status_code=404, detail=f"aucun règlement {identifiant} à ce dossier.")
    reglement = echeance.reglement
    if (echeance.fin - echeance.debut).days <= 31:
        periode = f"{MOIS[echeance.debut.month - 1]} {echeance.debut.year}"
    else:
        periode = f"du {echeance.debut:%d/%m/%Y} au {echeance.fin:%d/%m/%Y}"
    noms = {c.identifiant: c.nom_affiche for c in atelier().comptes.lister()}
    encaisse_par = (
        "paiement en ligne (mobile money)"
        if reglement.constate_par == "systeme"
        else noms.get(reglement.constate_par, reglement.constate_par)
    )
    return Response(
        content=rendre_le_recu(
            reglement, adherent=_denomination(niu), periode=periode, encaisse_par=encaisse_par
        ),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="recu-{identifiant}.pdf"'},
    )
