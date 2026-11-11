"""Les délais de la création (lot 4, 9 novembre 2026), sous `/creations`.

* `GET/PUT /creations/regle-des-delais` : la règle — lue par qui suit les formalités et par
  la direction, fixée par le super administrateur (`ACCORDER_FAVEURS`), avec un motif ;
* `POST /creations/{reference}/statuts-transmis` : le cabinet a transmis les statuts ;
* `POST /creations/{reference}/originaux-remis` : le client a retiré ses originaux.

Les dépassements se lisent dans la vue « Retards » (pilotage) ; la pénalité éventuelle se
décide par un responsable, côté souscription.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.creation_entreprise.adaptateurs.entrant.routes_http import (
    depot,
    lire_un_dossier_de_creation,
)
from app.contextes.creation_entreprise.adaptateurs.sortant.regle_des_delais import (
    depot_de_la_regle_des_delais,
)
from app.contextes.creation_entreprise.domaine.delais import (
    REGLE_SANS_DELAIS,
    RegleDesDelaisDeCreation,
)
from app.contextes.creation_entreprise.domaine.entites import DossierCreation, EtapeCreation
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.horloge import aujourd_hui, maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/creations", tags=["Création d'entreprise · délais"])


class RegleEnVigueur(BaseModel):
    regle: RegleDesDelaisDeCreation
    #: Rien de fixé : aucun délai n'est suivi.
    par_defaut: bool


@routeur.get("/regle-des-delais", summary="Les délais de la création fixés par la direction")
def lire_la_regle_des_delais(acces: AccesRequis) -> RegleEnVigueur:
    if not acces.detient(Permission.ACCORDER_FAVEURS) and not acces.detient(
        Permission.LIRE_PILOTAGE
    ):
        exiger(acces, Permission.SUIVRE_FORMALITE)
    fixee = depot_de_la_regle_des_delais().lire()
    return RegleEnVigueur(regle=fixee or REGLE_SANS_DELAIS, par_defaut=fixee is None)


class RegleAFixer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Vide : ce délai n'est pas suivi.
    delai_redaction_statuts_jours: int | None = Field(default=None, ge=1, le=365)
    delai_validation_statuts_jours: int | None = Field(default=None, ge=1, le=365)
    delai_retrait_originaux_jours: int | None = Field(default=None, ge=1, le=730)
    motif: str = Field(min_length=10, max_length=500)


@routeur.put("/regle-des-delais", summary="Fixer les délais de la création")
def fixer_la_regle_des_delais(acces: AccesRequis, demande: RegleAFixer) -> RegleEnVigueur:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=demande.motif)
    regle = RegleDesDelaisDeCreation(
        delai_redaction_statuts_jours=demande.delai_redaction_statuts_jours,
        delai_validation_statuts_jours=demande.delai_validation_statuts_jours,
        delai_retrait_originaux_jours=demande.delai_retrait_originaux_jours,
        motif=demande.motif,
        fixee_par=acces.compte,
        fixee_le=maintenant(),
    )
    depot_de_la_regle_des_delais().enregistrer(regle)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="creation.regle_des_delais",
        objet_type="regle_des_delais_de_creation",
        objet_id="regle",
        apres=demande.model_dump(exclude={"motif"}),
        motif=demande.motif,
    )
    return RegleEnVigueur(regle=regle, par_defaut=False)


def _jalon(acces, reference: str, champ: str, *, etapes: tuple[EtapeCreation, ...], action: str):
    magasin = depot()
    dossier = lire_un_dossier_de_creation(reference, magasin)
    if dossier.etape not in etapes:
        raise HTTPException(
            status_code=409,
            detail=f"Le dossier est à l'étape {dossier.etape.value} : ce geste n'y a pas de sens.",
        )
    if getattr(dossier, champ) is not None:
        return dossier
    date_du_jour = aujourd_hui()
    marque = dossier.model_copy(update={champ: date_du_jour})
    magasin.enregistrer(marque)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action=action,
        objet_type="dossier_creation",
        objet_id=reference,
        apres={champ: date_du_jour.isoformat()},
    )
    return marque


@routeur.post(
    "/{reference}/statuts-transmis",
    summary="Le cabinet a transmis les statuts au client (début du délai de validation)",
)
def marquer_les_statuts_transmis(acces: AccesRequis, reference: str) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    return _jalon(
        acces,
        reference,
        "statuts_transmis_le",
        etapes=(EtapeCreation.CONSTITUTION,),
        action="creation.statuts_transmis",
    )


@routeur.post(
    "/{reference}/originaux-remis",
    summary="Le client a retiré ses originaux (fin du délai de retrait)",
)
def marquer_les_originaux_remis(acces: AccesRequis, reference: str) -> DossierCreation:
    exiger(acces, Permission.SUIVRE_FORMALITE)
    return _jalon(
        acces,
        reference,
        "originaux_remis_le",
        etapes=(EtapeCreation.LIVRAISON, EtapeCreation.CONVERTI),
        action="creation.originaux_remis",
    )
