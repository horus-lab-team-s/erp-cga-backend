"""L'identité fiscale du cabinet, saisie quand la direction l'a (18 octobre 2026).

* `GET /souscription/facturation/identite-fiscale` : ce qui est saisi, et ce qui manque
  encore pour émettre une facture définitive ;
* `PUT /souscription/facturation/identite-fiscale` : la saisir ou la corriger, un champ
  à la fois s'il le faut.

⚠️ `FIXER_LES_TARIFS`, la direction seule : ces mentions engagent le cabinet sur chaque
facture, comme un prix. Corriger l'identité ne réécrit aucune facture déjà émise.
Voir `domaine/facturation.py`.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contextes.souscription.adaptateurs.sortant.depots_facturation import (
    depot_de_l_identite_fiscale,
)
from app.contextes.souscription.domaine.facturation import (
    IdentiteFiscaleDuCabinet,
    MontantsConvenus,
    RegimeTvaDuCabinet,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/souscription/facturation", tags=["Facturation du cabinet"])


class IdentiteLue(BaseModel):
    identite: IdentiteFiscaleDuCabinet
    #: Ce qui empêche encore d'émettre une facture, en clair ; vide, elle s'émet.
    manques: list[str]
    complete: bool


class IdentiteSaisie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    niu: str | None = Field(default=None, max_length=14)
    rccm: str | None = Field(default=None, max_length=60)
    regime_tva: RegimeTvaDuCabinet | None = None
    taux_tva: Decimal | None = None
    montants_convenus: MontantsConvenus | None = None
    fondement_exoneration: str | None = Field(default=None, max_length=300)


def _lue(identite: IdentiteFiscaleDuCabinet) -> IdentiteLue:
    return IdentiteLue(identite=identite, manques=identite.manques, complete=identite.complete)


@routeur.get("/identite-fiscale", summary="L'identité fiscale du cabinet, et ce qui y manque")
def lire_l_identite_fiscale(acces: AccesRequis) -> IdentiteLue:
    exiger(acces, Permission.FIXER_LES_TARIFS)
    return _lue(depot_de_l_identite_fiscale().lire())


@routeur.put("/identite-fiscale", summary="Saisir ou corriger l'identité fiscale du cabinet")
def saisir_l_identite_fiscale(acces: AccesRequis, saisie: IdentiteSaisie) -> IdentiteLue:
    exiger(acces, Permission.FIXER_LES_TARIFS)
    valeurs = {
        k: (v.strip().upper() if k == "niu" and isinstance(v, str) else v)
        for k, v in saisie.model_dump().items()
    }
    valeurs = {k: (None if isinstance(v, str) and not v.strip() else v) for k, v in valeurs.items()}
    try:
        identite = IdentiteFiscaleDuCabinet(
            **valeurs, modifiee_par=acces.compte, modifiee_le=maintenant()
        )
    except ValidationError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    depot = depot_de_l_identite_fiscale()
    avant = depot.lire()
    depot.enregistrer(identite)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="souscription.identite_fiscale",
        objet_type="identite_fiscale",
        objet_id="cabinet",
        avant=avant.model_dump(mode="json", exclude={"modifiee_par", "modifiee_le"}),
        apres=identite.model_dump(mode="json", exclude={"modifiee_par", "modifiee_le"}),
    )
    return _lue(identite)
