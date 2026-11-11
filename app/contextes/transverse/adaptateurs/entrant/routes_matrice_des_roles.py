"""La matrice des rôles du cabinet, lue et réglée (18 octobre 2026).

Voir `application/matrice_des_roles.py` pour les gestes et `domaine/roles.py` pour les
bornes. Ici, la traduction HTTP : la grille complète (rôles × droits, par famille), et
les trois gestes du super administrateur.

⚠️ LA LECTURE EST OUVERTE À QUI GÈRE LES COMPTES

L'administrateur qui invite un collaborateur doit voir ce que le rôle choisi permet,
sans pouvoir le changer. Régler reste au super administrateur.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    AccesRequis,
    atelier,
    exiger,
)
from app.contextes.transverse.application.autorisation import AccesRefuse
from app.contextes.transverse.application.matrice_des_roles import (
    RoleIntrouvable,
    RoleRefuse,
    creer_un_role,
    regler_un_role,
    retirer_un_role,
)
from app.contextes.transverse.domaine.roles import (
    FAMILLES_DE_PERMISSIONS,
    PERMISSIONS_DU_CLIENT,
    PERMISSIONS_EXTERNES,
    PERMISSIONS_RESERVEES,
    ROLES_A_PORTEE_OBLIGATOIRE,
    FicheDeRole,
    Permission,
    Role,
    matrice_en_vigueur,
    matrice_par_defaut,
)
from app.partage.horloge import jour_a_douala, maintenant

__all__ = ["routeur"]

routeur = APIRouter(prefix="/transverse", tags=["Identité et habilitations"])


class DroitDeLaGrille(BaseModel):
    code: Permission
    libelle: str
    #: Les bornes, pour que l'écran grise ce que le serveur refuserait.
    reserve_au_super_admin: bool
    geste_du_client: bool
    externe_possible: bool


class FamilleDeDroits(BaseModel):
    famille: str
    droits: list[DroitDeLaGrille]


class RoleDeLaGrille(BaseModel):
    code: str
    libelle: str
    permissions: list[Permission]
    systeme: bool
    externe: bool
    portee_obligatoire: bool
    #: Ses droits diffèrent de la matrice livrée (ou c'est un rôle du cabinet).
    regle: bool
    reglee_par: str | None
    reglee_le: datetime | None


class MatriceDesRoles(BaseModel):
    familles: list[FamilleDeDroits]
    roles: list[RoleDeLaGrille]
    #: Le lecteur peut-il régler ? L'écran ne le déduit pas des rôles.
    reglable: bool


def _grille(fiche: FicheDeRole, livree: dict[str, FicheDeRole]) -> RoleDeLaGrille:
    origine = livree.get(fiche.code)
    return RoleDeLaGrille(
        code=fiche.code,
        libelle=fiche.libelle,
        permissions=sorted(fiche.permissions, key=lambda p: p.value),
        systeme=fiche.systeme,
        externe=fiche.externe,
        portee_obligatoire=fiche.code in {r.value for r in ROLES_A_PORTEE_OBLIGATOIRE},
        regle=origine is None or origine.permissions != fiche.permissions,
        reglee_par=fiche.reglee_par,
        reglee_le=fiche.reglee_le,
    )


@routeur.get("/matrice-des-roles", summary="La matrice des rôles du cabinet, rôles × droits")
def lire_la_matrice(acces: AccesRequis) -> MatriceDesRoles:
    if not acces.detient(Permission.GERER_ROLES):
        exiger(acces, Permission.GERER_COMPTES)
    livree = matrice_par_defaut()
    en_vigueur = matrice_en_vigueur()
    # Les rôles livrés dans l'ordre de l'énumération (le cabinet, puis les externes),
    # les rôles du cabinet ensuite, par nom.
    ordre = [r.value for r in Role] + sorted(c for c in en_vigueur if c not in livree)
    return MatriceDesRoles(
        familles=[
            FamilleDeDroits(
                famille=famille,
                droits=[
                    DroitDeLaGrille(
                        code=permission,
                        libelle=libelle,
                        reserve_au_super_admin=permission in PERMISSIONS_RESERVEES,
                        geste_du_client=permission in PERMISSIONS_DU_CLIENT,
                        externe_possible=permission in PERMISSIONS_EXTERNES,
                    )
                    for permission, libelle in droits
                ],
            )
            for famille, droits in FAMILLES_DE_PERMISSIONS
        ],
        roles=[_grille(en_vigueur[code], livree) for code in ordre if code in en_vigueur],
        reglable=acces.detient(Permission.GERER_ROLES),
    )


class Reglage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: list[Permission]
    motif: str = Field(min_length=10, max_length=500)
    #: Seul un rôle du cabinet se renomme.
    libelle: str | None = Field(default=None, min_length=2, max_length=80)


class Creation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=40)
    libelle: str = Field(min_length=2, max_length=80)
    permissions: list[Permission]
    motif: str = Field(min_length=10, max_length=500)


class Retrait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: str = Field(min_length=10, max_length=500)


def _traduire(geste):
    try:
        return geste()
    except RoleIntrouvable as absent:
        raise HTTPException(status_code=404, detail=str(absent)) from absent
    except RoleRefuse as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    except AccesRefuse as refus:
        raise HTTPException(
            status_code=403,
            detail=f"Action non permise : GERER_ROLES est requise. ({type(refus).__name__})",
        ) from refus


@routeur.put("/matrice-des-roles/{code}", summary="Régler les droits d'un rôle")
def regler(acces: AccesRequis, code: str, demande: Reglage) -> RoleDeLaGrille:
    exiger(acces, Permission.GERER_ROLES, motif=demande.motif)
    boutique = atelier()
    fiche = _traduire(
        lambda: regler_un_role(
            par=acces,
            code=code,
            permissions=demande.permissions,
            libelle=demande.libelle,
            motif=demande.motif,
            roles=boutique.roles,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    )
    return _grille(fiche, matrice_par_defaut())


@routeur.post("/matrice-des-roles", summary="Créer un rôle du cabinet", status_code=201)
def creer(acces: AccesRequis, demande: Creation) -> RoleDeLaGrille:
    exiger(acces, Permission.GERER_ROLES, motif=demande.motif)
    boutique = atelier()
    fiche = _traduire(
        lambda: creer_un_role(
            par=acces,
            code=demande.code.strip().upper(),
            libelle=demande.libelle,
            permissions=demande.permissions,
            motif=demande.motif,
            roles=boutique.roles,
            journal=boutique.journal,
            a_l_instant=maintenant(),
        )
    )
    return _grille(fiche, matrice_par_defaut())


@routeur.post(
    "/matrice-des-roles/{code}/retrait", summary="Retirer un rôle du cabinet", status_code=204
)
def retirer(acces: AccesRequis, code: str, demande: Retrait) -> None:
    exiger(acces, Permission.GERER_ROLES, motif=demande.motif)
    boutique = atelier()
    instant = maintenant()
    _traduire(
        lambda: retirer_un_role(
            par=acces,
            code=code,
            motif=demande.motif,
            roles=boutique.roles,
            comptes=boutique.comptes,
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=instant,
            a_la_date=jour_a_douala(instant),
        )
    )
