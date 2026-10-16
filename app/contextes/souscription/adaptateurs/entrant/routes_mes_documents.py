"""Les documents de la cliente d'une création : sa proforma, son reçu (15 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUI MANQUAIT : connectée à « Ma création », elle suivait son dossier mais ne
retrouvait ni la proforma qu'elle avait acceptée, ni de reçu de ce qu'elle avait
payé ; la proforma n'était lisible que par le lien signé du courriel, qui expire.

⚠️ SA PORTÉE, ET RIEN D'AUTRE : son habilitation porte les dossiers de formalité
`CRE-<dossier commercial>`. Une proforma ou un reçu d'un autre dossier commercial rend
404, qu'il existe ou non.

⚠️ LE REÇU NE SE FABRIQUE QUE SUR UN DOSSIER PAYÉ, pour le montant ARRÊTÉ de la
proforma acceptée : jamais un montant saisi. La référence du paiement figure quand le
paiement en ligne la porte ; au guichet, le reçu le dit.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.contextes.souscription.domaine.dossier_commercial import EtatDossier
from app.contextes.souscription.domaine.proforma import EtatProforma
from app.contextes.transverse.api import AccesRequis, Permission, exiger

__all__ = ["routeur"]

routeur = APIRouter(prefix="/mes-documents", tags=["Espace client"])

_PREFIXE = "CRE-"
#: Les états d'une proforma que la cliente a reçue.
_TRANSMISES = (EtatProforma.TRANSMISE, EtatProforma.ACCEPTEE)


def _dossiers_commerciaux(acces: AccesRequis) -> list[str]:
    return [d[len(_PREFIXE) :] for d in (acces.dossiers or []) if d.startswith(_PREFIXE)]


class ProformaDeLaCliente(BaseModel):
    numero: str
    version: int
    etat: EtatProforma
    total: Decimal
    emise_le: datetime


class MesDocumentsDeCreation(BaseModel):
    dossier: str
    denomination: str | None
    proformas: list[ProformaDeLaCliente]
    reglee: bool
    reglee_le: datetime | None = None


def _proforma_payee(dossier_commercial: str):
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        depot_proformas,
    )

    """La proforma que le règlement a soldée : l'acceptée, sinon la dernière transmise.

    Le guichet encaisse sur le numéro de la proforma en cours, et une proforma transmise
    réglée sans passer par le lien d'acceptation reste TRANSMISE : le dossier payé suffit.
    Une proforma remplacée ou annulée n'est jamais celle-là.
    """
    proformas = list(reversed(depot_proformas().par_dossier(dossier_commercial)))
    return next((p for p in proformas if p.etat is EtatProforma.ACCEPTEE), None) or next(
        (p for p in proformas if p.etat is EtatProforma.TRANSMISE), None
    )


@routeur.get("/creation", summary="Les proformas et le reçu de la cliente connectée")
def mes_documents(acces: AccesRequis) -> list[MesDocumentsDeCreation]:
    exiger(acces, Permission.SUIVRE_MA_CREATION)
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        depot_dossiers,
        depot_proformas,
    )
    from app.contextes.souscription.adaptateurs.sortant.depots_memoire import DossierIntrouvable

    vues = []
    for reference in _dossiers_commerciaux(acces):
        try:
            dossier = depot_dossiers().lire(reference)
        except DossierIntrouvable:
            continue
        proformas = [
            ProformaDeLaCliente(
                numero=p.numero, version=p.version, etat=p.etat, total=p.total, emise_le=p.emise_le
            )
            for p in depot_proformas().par_dossier(reference)
            # Ce qu'on lui a transmis : une proforma émise mais jamais envoyée reste au cabinet.
            if p.etat in _TRANSMISES
        ]
        payee = dossier.etat is EtatDossier.PAYEE and dossier.payee_le is not None
        vues.append(
            MesDocumentsDeCreation(
                dossier=reference,
                denomination=dossier.demande.nom,
                proformas=proformas,
                reglee=payee,
                reglee_le=dossier.payee_le if payee else None,
            )
        )
    return vues


@routeur.get(
    "/creation/proformas/{numero}/document",
    summary="Le PDF d'une de ses proformas, sous session",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def ma_proforma(acces: AccesRequis, numero: str) -> Response:
    exiger(acces, Permission.SUIVRE_MA_CREATION)
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        servir_le_pdf_de_la_proforma,
    )
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        depot_proformas,
    )
    from app.contextes.souscription.adaptateurs.sortant.depots_memoire import ProformaIntrouvable

    try:
        proforma = depot_proformas().lire(numero)
    except ProformaIntrouvable as absente:
        raise HTTPException(status_code=404, detail=f"proforma {numero} introuvable.") from absente
    # ⚠️ La même règle que la liste : ce qu'on lui a transmis, rien d'autre. Une proforma
    # émise mais jamais envoyée, ou annulée, reste au cabinet.
    if proforma.dossier not in _dossiers_commerciaux(acces) or proforma.etat not in _TRANSMISES:
        raise HTTPException(status_code=404, detail=f"proforma {numero} introuvable.")
    return servir_le_pdf_de_la_proforma(proforma)


@routeur.get(
    "/creation/{dossier}/recu",
    summary="Le reçu de ce qu'elle a réglé",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def mon_recu(acces: AccesRequis, dossier: str) -> Response:
    exiger(acces, Permission.SUIVRE_MA_CREATION)
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir
    from app.contextes.souscription.adaptateurs.entrant.sources import (
        depot_dossiers,
    )
    from app.contextes.souscription.adaptateurs.sortant.depots_memoire import DossierIntrouvable
    from app.contextes.souscription.adaptateurs.sortant.document_recu_honoraires import (
        rendre_un_recu,
    )
    from app.contextes.souscription.domaine.paiements import (
        NaturePaiement,
        StatutPaiement,
    )

    if dossier not in _dossiers_commerciaux(acces):
        raise HTTPException(status_code=404, detail=f"dossier {dossier} introuvable.")
    try:
        commercial = depot_dossiers().lire(dossier)
    except DossierIntrouvable as absent:
        raise HTTPException(status_code=404, detail=f"dossier {dossier} introuvable.") from absent
    proforma = _proforma_payee(dossier)
    if commercial.etat is not EtatDossier.PAYEE or commercial.payee_le is None or proforma is None:
        raise HTTPException(
            status_code=409, detail="Le reçu s'établit quand le règlement est constaté."
        )
    en_ligne = next(
        (
            p
            for p in comptoir().paiements.par_souscription(proforma.numero)
            if p.nature is NaturePaiement.PROFORMA and p.statut is StatutPaiement.VALIDE
        ),
        None,
    )
    regle = (
        f"par mobile money, référence {en_ligne.reference_externe or en_ligne.identifiant}"
        if en_ligne
        else "au guichet du cabinet"
    )
    octets = rendre_un_recu(
        titre="REÇU DE RÈGLEMENT · ACQUITTÉ",
        # Le numéro de la proforma soldée : lisible par la cliente, unique (un dossier ne
        # solde qu'une proforma), et sans seconde numérotation à tenir.
        numero=f"R-{proforma.numero}",
        le=commercial.payee_le,
        avant=[
            ("Client", commercial.demande.nom),
            ("Objet", f"Proforma {proforma.numero}, version {proforma.version}"),
        ],
        montant=proforma.total,
        apres=[("Réglé", regle)],
    )
    return Response(
        content=octets,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="recu-{proforma.numero}.pdf"'},
    )
