"""Les documents à échéance d'un adhérent (lot 4, 9 novembre 2026), sous `/portefeuille`.

* `GET  /portefeuille/documents-a-echeance/catalogue` : les sortes, leur durée, leur source ;
* `GET  /portefeuille/entreprises/{niu}/documents-a-echeance` : en vigueur, et l'histoire ;
* `POST /portefeuille/entreprises/{niu}/documents-a-echeance` : enregistrer une attestation.

Qui enregistre : le cabinet qui tient le dossier (`SAISIR_ECRITURE`), ou l'adhérent dans son
espace (`RENSEIGNER_FICHE_DSF`, la permission de sa fiche). L'expiration se calcule par la
durée du catalogue ; sans durée établie, elle se saisit — et une date portée sur l'attestation
elle-même l'emporte toujours, c'est le document qui fait foi.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.portefeuille.adaptateurs.entrant.routes_http import depot, lire_le_dossier
from app.contextes.portefeuille.adaptateurs.sortant.documents_a_echeance_yaml import (
    charger_le_catalogue_des_documents,
)
from app.contextes.portefeuille.domaine.documents_a_echeance import (
    CatalogueDesDocuments,
    DocumentAEcheance,
    documents_en_vigueur,
    expiration_calculee,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    exiger,
    exiger_dossier,
    exiger_dossier_pour_l_un_de,
)
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.horloge import aujourd_hui, maintenant

__all__ = ["catalogue_des_documents", "routeur"]

routeur = APIRouter(prefix="/portefeuille", tags=["Portefeuille · documents à échéance"])

ENREGISTREMENT = (Permission.SAISIR_ECRITURE, Permission.RENSEIGNER_FICHE_DSF)


def catalogue_des_documents() -> CatalogueDesDocuments:
    return charger_le_catalogue_des_documents(configuration().dossier_referentiel)


@routeur.get(
    "/documents-a-echeance/catalogue",
    summary="Les sortes de documents à échéance, leur durée et leur source",
)
def lire_le_catalogue(acces: AccesRequis) -> CatalogueDesDocuments:
    exiger(acces, Permission.LIRE_DOSSIER)
    return catalogue_des_documents()


class DocumentLu(BaseModel):
    document: DocumentAEcheance
    jours_restants: int
    expire: bool


class DocumentsDuDossier(BaseModel):
    en_vigueur: list[DocumentLu]
    historique: list[DocumentAEcheance]


def _lus(documents: list[DocumentAEcheance]) -> DocumentsDuDossier:
    jour = aujourd_hui()
    vigueur = documents_en_vigueur(documents)
    return DocumentsDuDossier(
        en_vigueur=[
            DocumentLu(document=d, jours_restants=d.jours_restants(jour), expire=d.expire_le < jour)
            for d in vigueur
        ],
        historique=sorted(
            (d for d in documents if d not in vigueur), key=lambda d: d.delivre_le, reverse=True
        ),
    )


@routeur.get(
    "/entreprises/{niu}/documents-a-echeance",
    summary="Les documents à échéance d'un dossier : en vigueur, et l'histoire",
)
def lire_les_documents(acces: AccesRequis, niu: str) -> DocumentsDuDossier:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    return _lus(list(lire_le_dossier(niu).documents_a_echeance))


class DocumentAEnregistrer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    delivre_le: date
    #: Absente : calculée par la durée du catalogue. Présente : celle de l'attestation.
    expire_le: date | None = None
    reference: str | None = Field(default=None, max_length=120)


@routeur.post(
    "/entreprises/{niu}/documents-a-echeance",
    summary="Enregistrer un document à échéance (une attestation, sa date)",
    status_code=201,
)
def enregistrer_un_document(
    acces: AccesRequis, niu: str, corps: DocumentAEnregistrer
) -> DocumentsDuDossier:
    exiger_dossier_pour_l_un_de(acces, ENREGISTREMENT, niu)
    sorte = catalogue_des_documents().sorte(corps.code)
    if sorte is None:
        raise HTTPException(
            status_code=422,
            detail=f"« {corps.code} » n'est pas au catalogue des documents à échéance.",
        )
    expire_le = corps.expire_le or expiration_calculee(sorte, corps.delivre_le)
    if expire_le is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"La durée de validité de « {sorte.libelle} » n'est pas établie : indiquez la "
                "date d'expiration portée sur le document."
            ),
        )
    try:
        document = DocumentAEcheance(
            identifiant=f"doc-{uuid.uuid4().hex[:12]}",
            code=sorte.code,
            libelle=sorte.libelle,
            delivre_le=corps.delivre_le,
            expire_le=expire_le,
            reference=corps.reference,
            enregistre_par=acces.compte,
            enregistre_le=maintenant(),
        )
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    entreprise = lire_le_dossier(niu)
    documents = [*entreprise.documents_a_echeance, document]
    depot().enregistrer(entreprise.model_copy(update={"documents_a_echeance": documents}))
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="document_a_echeance.enregistre",
        objet_type="entreprise",
        objet_id=niu,
        apres={"dossier": niu, "code": sorte.code, "expire_le": expire_le.isoformat()},
    )
    return _lus(documents)
