"""L'attestation d'adhésion, délivrée au cabinet et à l'adhérent (11 novembre 2026).

Voir `adaptateurs/sortant/document_attestation.py` pour ce qu'elle porte et ce qu'elle ne
remplace pas. Chaque délivrance passe au journal d'audit avec sa référence : une
attestation présentée à un tiers se retrouve, et son empreinte se recalcule.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Response

from app.contextes.portefeuille.api import EntrepriseIntrouvable, RegimeFiscal, depot_entreprises
from app.contextes.souscription.adaptateurs.sortant.document_attestation import (
    AttestationDAdhesion,
    empreinte_de_l_attestation,
    rendre_l_attestation,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger_dossier
from app.partage.horloge import jour_a_douala, maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/souscription", tags=["Adhésion"])

_REGIMES = {
    RegimeFiscal.IGS: "Impôt général synthétique (IGS)",
    RegimeFiscal.REEL: "Régime du réel",
}


@routeur.get(
    "/dossiers/{niu}/attestation-d-adhesion",
    summary="L'attestation d'adhésion au Centre, en PDF",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}},
        409: {"description": "Le dossier n'est pas adhérent à ce jour"},
    },
)
def attestation_d_adhesion(acces: AccesRequis, niu: str) -> Response:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    try:
        entreprise = depot_entreprises().lire(niu)
    except EntrepriseIntrouvable as absence:
        raise HTTPException(status_code=404, detail=str(absence)) from absence
    instant = maintenant()
    jour = jour_a_douala(instant)
    adhesion = entreprise.adhesion_au(jour)
    if adhesion is None:
        raise HTTPException(
            status_code=409,
            detail=f"{entreprise.denomination} n'est pas adhérente du Centre au "
            f"{jour:%d/%m/%Y} : aucune attestation ne se délivre.",
        )
    if not adhesion.numero:
        raise HTTPException(
            status_code=409,
            detail="l'adhésion en cours n'a pas de numéro : l'attribuer au portefeuille avant "
            "de délivrer une attestation.",
        )
    attestation = AttestationDAdhesion(
        denomination=entreprise.denomination,
        niu=entreprise.niu,
        rccm=entreprise.rccm,
        regime=_REGIMES.get(entreprise.regime_au(jour), str(entreprise.regime_au(jour))),
        numero_d_adhesion=adhesion.numero,
        adherent_depuis=adhesion.debut,
        delivree_le=instant,
        reference=f"ATT-{jour:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
    )
    atelier().journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="adhesion.attestation_delivree",
        objet_type="entreprise",
        objet_id=niu,
        apres={
            "reference": attestation.reference,
            "numero_d_adhesion": attestation.numero_d_adhesion,
            "empreinte": empreinte_de_l_attestation(attestation),
        },
    )
    return Response(
        content=rendre_l_attestation(attestation),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="attestation-adhesion-{niu}.pdf"'},
    )
