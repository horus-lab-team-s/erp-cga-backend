"""Les mandats déclaratifs et de paiement donnés au cabinet.

Extrait de `routes_http.py` le 16 octobre 2026. Les routes gardent leurs chemins ; le
routeur est monté à côté du sien.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
)
from app.contextes.transverse.adaptateurs.entrant.routes_http import (
    nouvel_identifiant,
)
from app.contextes.transverse.api import (
    Permission,
    Role,
)
from app.contextes.transverse.domaine.mandats import Mandat, MotifMandat
from app.partage import horloge
from app.partage.erreurs import message_lisible
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/transverse", tags=["Identité et habilitations"])


# ── Mandats ──────────────────────────────────────────────────────────────────
#
# ⚠️ **CES ROUTES SONT CELLES DU MANDANT, ET DE LUI SEUL.**
#
# Un mandat s'accorde sur ses propres données, et se retire de même. Le mandataire n'a
# aucune route ici : lui en donner une reviendrait à laisser un locataire s'octroyer un
# accès chez un autre, ce qui est exactement ce que le mandat empêche.


class FicheMandat(BaseModel):
    """Un mandat, tel que l'écran du mandant le montre."""

    identifiant: str
    mandataire: str
    roles: list[Role]
    comptes: list[str] | None
    debut: date
    fin: date | None
    motif: MotifMandat
    accorde_par: str
    revoque_le: date | None
    revoque_par: str | None
    precision: str | None
    #: Calculé à la date du jour : c'est la seule question que l'écran pose vraiment.
    en_vigueur: bool


def _fiche(mandat: Mandat, aujourd_hui: date) -> FicheMandat:
    return FicheMandat(
        identifiant=mandat.identifiant,
        mandataire=mandat.mandataire,
        roles=sorted(mandat.roles, key=lambda r: r.value),
        comptes=None if mandat.comptes is None else sorted(mandat.comptes),
        debut=mandat.debut,
        fin=mandat.fin,
        motif=mandat.motif,
        accorde_par=mandat.accorde_par,
        revoque_le=mandat.revoque_le,
        revoque_par=mandat.revoque_par,
        precision=mandat.precision,
        en_vigueur=mandat.en_vigueur(aujourd_hui),
    )


@routeur.get(
    "/mandats",
    summary="Les mandats que ce locataire a accordés",
    description=(
        "Révoqués et expirés compris : un mandat retiré reste une trace, et c'est la "
        "première chose qu'un litige demande."
    ),
)
def lister_les_mandats(acces: AccesRequis) -> list[FicheMandat]:
    exiger(acces, Permission.GERER_COMPTES)
    aujourd_hui = horloge.aujourd_hui()
    return [_fiche(m, aujourd_hui) for m in atelier().mandats.tous()]


class DemandeDeMandat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandataire: str = Field(min_length=1, max_length=64)
    roles: list[Role] = Field(min_length=1)
    #: Absent ou nul = tous les comptes du mandataire.
    comptes: list[str] | None = None
    debut: date
    fin: date | None = None
    motif: MotifMandat
    precision: str | None = Field(default=None, max_length=500)


@routeur.post(
    "/mandats",
    status_code=201,
    summary="Accorder un mandat à un autre locataire",
    description=(
        "Le mandat autorise l'**exercice** de rôles déjà tenus dans le périmètre de ce "
        "locataire. Il n'accorde aucun rôle nouveau."
    ),
    responses={422: {"description": "Mandat incohérent — le motif est rendu"}},
)
def accorder_un_mandat(demande: DemandeDeMandat, acces: AccesRequis) -> FicheMandat:
    exiger(acces, Permission.GERER_COMPTES)
    instant = maintenant()
    try:
        mandat = Mandat(
            identifiant=nouvel_identifiant("MDT"),
            # ⚠️ Le mandant est le locataire servi, jamais un champ du corps. Le laisser
            # choisir permettrait d'accorder un mandat sur les données d'un autre.
            mandant=courant(),
            mandataire=demande.mandataire,
            roles=frozenset(demande.roles),
            comptes=None if demande.comptes is None else frozenset(demande.comptes),
            debut=demande.debut,
            fin=demande.fin,
            motif=demande.motif,
            accorde_par=acces.nom_complet,
            precision=demande.precision,
        )
    except ValidationError as refus:
        # ⚠️ `message_lisible` et non `str()` : la trace brute d'un validateur rendue à
        # l'appelant expose la forme interne du modèle et ne se lit pas.
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus

    boutique = atelier()
    boutique.mandats.enregistrer(mandat)
    boutique.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="mandat.accorde",
        objet_type="mandat",
        objet_id=mandat.identifiant,
        apres={
            "mandataire": mandat.mandataire,
            "roles": sorted(r.value for r in mandat.roles),
            "debut": mandat.debut.isoformat(),
            "motif": mandat.motif.value,
        },
    )
    return _fiche(mandat, jour_a_douala(instant))


class DemandeDeRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ⚠️ Exigé, et long : retirer un accès accordé à un tiers se motive, et c'est ce
    #: motif que l'on relit deux ans plus tard.
    motif: str = Field(min_length=10, max_length=500)


@routeur.post(
    "/mandats/{identifiant}/revocation",
    summary="Retirer un mandat avant son terme",
    description=(
        "La révocation porte sa propre date et son auteur. Elle n'écrase pas la fin "
        "prévue : un mandat retiré n'est pas un mandat arrivé à échéance."
    ),
    responses={404: {"description": "Aucun mandat de ce locataire porte cet identifiant"}},
)
def revoquer_un_mandat(
    identifiant: str, demande: DemandeDeRevocation, acces: AccesRequis
) -> FicheMandat:
    exiger(acces, Permission.GERER_COMPTES)
    boutique = atelier()
    mandat = boutique.mandats.lire(identifiant)
    if mandat is None:
        raise HTTPException(status_code=404, detail="Ce mandat n'existe pas.")
    if mandat.revoque_le is not None:
        raise HTTPException(status_code=409, detail="Ce mandat est déjà révoqué.")

    instant = maintenant()
    retire = mandat.model_copy(
        update={"revoque_le": jour_a_douala(instant), "revoque_par": acces.nom_complet}
    )
    boutique.mandats.enregistrer(retire)
    boutique.journal.ajouter(
        horodatage=instant,
        acteur=acces.compte,
        action="mandat.revoque",
        objet_type="mandat",
        objet_id=retire.identifiant,
        avant={"revoque_le": None},
        apres={"revoque_le": retire.revoque_le.isoformat()},
        motif=demande.motif,
    )
    return _fiche(retire, jour_a_douala(instant))

