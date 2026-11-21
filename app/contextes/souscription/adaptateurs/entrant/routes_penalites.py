"""Les pénalités de retard d'un dossier (lot 4, 9 novembre 2026), sous `/acquisition`.

* `POST /acquisition/dossiers/{reference}/penalites` : un responsable inflige une pénalité,
  avec une explication ; le client en est prévenu par courriel ;
* `POST /acquisition/dossiers/{reference}/penalites/{numero}/levee` : le super administrateur
  la lève, avec un motif — jamais une pénalité déjà versée ;
* `POST /acquisition/dossiers/{reference}/penalite-de-retard/encaissement` : la caisse
  encaisse ce qui reste dû.

Voir `domaine/penalites.py` : le système signale (vue « Retards »), un responsable décide.
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
)
from app.contextes.souscription.domaine.dossier_commercial import DossierCommercial
from app.contextes.souscription.domaine.honoraires import CanalDeReglement
from app.contextes.souscription.domaine.penalites import (
    LIBELLES_DES_MOTIFS,
    MotifDePenalite,
    PenaliteInfligee,
    PenaliteRefusee,
    lever,
    total_des_penalites_actives,
)
from app.contextes.souscription.domaine.versements_du_parcours import (
    NatureVersement,
    SituationDesVersements,
    VersementDejaRecu,
    VersementRefuse,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

__all__ = ["penalite_de_retard_due", "routeur"]

routeur = APIRouter(prefix="/acquisition", tags=["Pénalités de retard"])


def _comptoir():
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir

    return comptoir()


def _dossier(reference: str) -> DossierCommercial:
    try:
        return depot_dossiers().lire(reference)
    except DossierIntrouvable as echec:
        raise HTTPException(status_code=404, detail=str(echec)) from echec


def penalite_de_retard_due(
    dossier: DossierCommercial, situation: SituationDesVersements | None = None
) -> Decimal:
    """Ce que les pénalités infligées et non levées de ce dossier coûtent encore."""
    infligees = total_des_penalites_actives(dossier.penalites)
    if infligees == 0:
        return Decimal(0)
    if situation is None:
        from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
            situation_des_versements_de,
        )

        situation = situation_des_versements_de(dossier)
    return max(infligees - situation.penalites_de_retard_versees, Decimal(0))


def _prevenir_le_client(dossier: DossierCommercial, penalite: PenaliteInfligee) -> bool:
    """Le client lit la pénalité, son montant et son explication. Un échec d'envoi ne défait
    rien : le responsable voit la pénalité au dossier et peut l'annoncer de vive voix."""
    from app.contextes.souscription.adaptateurs.entrant.routes_rendez_vous import (
        courriel_du_dossier,
    )
    from app.contextes.transverse.api import service_de_notification

    courriel = courriel_du_dossier(dossier)
    if not courriel:
        return False
    demande = dossier.demande
    try:
        service_de_notification().envoyer(
            "penalite.infligee",
            destinataire=courriel,
            contexte={
                "prenom": demande.nom,
                "motif": LIBELLES_DES_MOTIFS[penalite.motif],
                "montant": f"{penalite.montant:,.0f}".replace(",", " "),
                "explication": penalite.explication,
            },
        )
    except Exception:  # noqa: BLE001 — voir la docstring
        return False
    return True


class PenaliteAInfliger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: MotifDePenalite
    #: Vide : le montant par défaut du motif, s'il est fixé dans la règle des versements.
    montant: Decimal | None = Field(default=None, gt=0)
    explication: str = Field(min_length=10, max_length=500)


class PenalitesDuDossier(BaseModel):
    dossier: str
    penalites: tuple[PenaliteInfligee, ...]
    reste_du: Decimal
    client_prevenu: bool | None = None


@routeur.post(
    "/dossiers/{reference}/penalites",
    summary="Infliger une pénalité de retard (décision d'un responsable)",
    status_code=201,
)
def infliger_une_penalite(
    acces: AccesRequis, reference: str, corps: PenaliteAInfliger
) -> PenalitesDuDossier:
    # Le responsable de la création, ou la direction.
    if not acces.detient(Permission.LIRE_PILOTAGE):
        exiger(acces, Permission.SUIVRE_FORMALITE)
    from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
        regle_des_versements,
    )

    dossier = _dossier(reference)
    montant = corps.montant or regle_des_versements().penalites_de_retard.get(corps.motif.value)
    if montant is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Aucun montant par défaut n'est fixé pour ce motif : saisissez le montant de "
                "la pénalité."
            ),
        )
    penalite = PenaliteInfligee(
        numero=len(dossier.penalites) + 1,
        motif=corps.motif,
        montant=montant,
        explication=corps.explication.strip(),
        infligee_par=acces.compte,
        infligee_le=maintenant(),
    )
    mis_a_jour = dossier.model_copy(update={"penalites": (*dossier.penalites, penalite)})
    depot_dossiers().enregistrer(mis_a_jour)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="penalite.infligee",
        objet_type="dossier_commercial",
        objet_id=reference,
        apres={"numero": penalite.numero, "motif": penalite.motif.value, "montant": str(montant)},
        motif=penalite.explication,
    )
    return PenalitesDuDossier(
        dossier=reference,
        penalites=mis_a_jour.penalites,
        reste_du=penalite_de_retard_due(mis_a_jour),
        client_prevenu=_prevenir_le_client(mis_a_jour, penalite),
    )


class LeveeDemandee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/dossiers/{reference}/penalites/{numero}/levee",
    summary="Lever une pénalité de retard, avec un motif",
)
def lever_une_penalite(
    acces: AccesRequis, reference: str, numero: int, corps: LeveeDemandee
) -> PenalitesDuDossier:
    exiger(acces, Permission.ACCORDER_FAVEURS, motif=corps.motif)
    from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
        situation_des_versements_de,
    )

    dossier = _dossier(reference)
    try:
        penalites = lever(
            dossier.penalites,
            numero,
            motif=corps.motif,
            par=acces.compte,
            le=maintenant(),
            deja_versees=situation_des_versements_de(dossier).penalites_de_retard_versees,
        )
    except PenaliteRefusee as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus
    mis_a_jour = dossier.model_copy(update={"penalites": penalites})
    depot_dossiers().enregistrer(mis_a_jour)
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="penalite.levee",
        objet_type="dossier_commercial",
        objet_id=reference,
        apres={"numero": numero},
        motif=corps.motif,
    )
    return PenalitesDuDossier(
        dossier=reference, penalites=penalites, reste_du=penalite_de_retard_due(mis_a_jour)
    )


class PenaliteDeRetardAuGuichet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_externe: str = Field(min_length=1, max_length=120)


@routeur.post(
    "/dossiers/{reference}/penalite-de-retard/encaissement",
    summary="Constater le règlement des pénalités de retard dues, au guichet",
    status_code=201,
)
def encaisser_la_penalite_de_retard(
    acces: AccesRequis, reference: str, corps: PenaliteDeRetardAuGuichet
) -> SituationDesVersements:
    exiger(acces, Permission.ENCAISSER)
    from app.contextes.souscription.adaptateurs.entrant.routes_versements import (
        regle_des_versements,
    )

    dossier = _dossier(reference)
    du = penalite_de_retard_due(dossier)
    if du <= 0:
        raise HTTPException(
            status_code=409, detail="Aucune pénalité de retard n'est due sur ce dossier."
        )
    try:
        resultat = recevoir_un_versement(
            dossier=dossier,
            proforma=proforma_a_regler(depot_proformas().par_dossier(reference)),
            versements=_comptoir().versements_du_parcours,
            regle=regle_des_versements(),
            nature=NatureVersement.PENALITE_DE_RETARD,
            montant=du,
            canal=CanalDeReglement.GUICHET,
            reference_externe=f"guichet:{corps.reference_externe.strip()}",
            constate_par=acces.compte,
            a_l_instant=maintenant(),
            boite=boite_du_parcours(),
        )
    except (VersementRefuse, VersementDejaRecu) as refus:
        raise HTTPException(status_code=409, detail=message_lisible(refus)) from refus
    return resultat.situation
