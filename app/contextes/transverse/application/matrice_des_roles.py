"""Régler la matrice des rôles du cabinet (18 octobre 2026).

─────────────────────────────────────────────────────────────────────────────────
CE QUE LE GÉRANT A DEMANDÉ

« Un RBAC dynamique, qui ressorte aisément la matrice des rôles et cache les vues dont
un rôle n'a pas le droit. » Trois gestes, tous au super administrateur, tous avec un
motif et le second facteur, tous au journal d'audit avec l'avant et l'après :

* **régler** un rôle : cocher ou décocher ses droits ;
* **créer** un rôle du cabinet (« ressources humaines »), interne par construction ;
* **retirer** un rôle du cabinet que plus personne ne tient.

Les bornes qui ne se règlent pas sont dans `FicheDeRole` (voir l'en-tête de
`roles.py`) : ce module ne les répète pas, il les fait jouer en construisant la fiche.

⚠️ RÉGLER UN RÔLE LIVRÉ SUR SA MATRICE LIVRÉE EFFACE LE RÉGLAGE

La ligne disparaît plutôt que de figer la valeur livrée : le cabinet qui revient au
défaut suivra les droits que la plateforme ajoutera à ce rôle, comme les cabinets qui
n'y ont jamais touché.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Protocol

from app.contextes.transverse.application.autorisation import Acces
from app.contextes.transverse.domaine.habilitations import habilitations_actives
from app.contextes.transverse.domaine.ports import DepotComptes, DepotHabilitations, JournalAudit
from app.contextes.transverse.domaine.roles import (
    FicheDeRole,
    Permission,
    Role,
    matrice_en_vigueur,
    matrice_par_defaut,
)

__all__ = [
    "DepotRolesDuCabinet",
    "RoleIntrouvable",
    "RoleRefuse",
    "creer_un_role",
    "regler_un_role",
    "retirer_un_role",
]


class DepotRolesDuCabinet(Protocol):
    def tous(self) -> list[FicheDeRole]: ...

    def enregistrer(self, fiche: FicheDeRole) -> None: ...

    def retirer(self, code: str) -> None: ...


class RoleIntrouvable(LookupError):
    """Le cabinet n'a pas ce rôle."""


class RoleRefuse(ValueError):
    """Le geste contredit une borne de la matrice, ou l'état des habilitations."""


def _trace(fiche: FicheDeRole | None) -> dict | None:
    if fiche is None:
        return None
    return {"libelle": fiche.libelle, "permissions": sorted(p.value for p in fiche.permissions)}


def _fiche(**champs) -> FicheDeRole:
    try:
        return FicheDeRole(**champs)
    except ValueError as invalide:
        # Le message de la borne est déjà écrit pour le gérant ; on ne garde que lui.
        erreurs = getattr(invalide, "errors", None)
        message = erreurs()[0]["msg"].removeprefix("Value error, ") if erreurs else str(invalide)
        raise RoleRefuse(message) from invalide


def regler_un_role(
    *,
    par: Acces,
    code: str,
    permissions: Iterable[Permission],
    motif: str,
    roles: DepotRolesDuCabinet,
    journal: JournalAudit,
    a_l_instant: datetime,
    libelle: str | None = None,
) -> FicheDeRole:
    """Fixe les droits d'un rôle ; renomme un rôle du cabinet si `libelle` est donné."""
    par.exiger(Permission.GERER_ROLES, motif=motif)
    avant = matrice_en_vigueur().get(code)
    if avant is None:
        raise RoleIntrouvable(f"le cabinet n'a pas de rôle {code}.")
    if libelle is not None and avant.systeme and libelle != avant.libelle:
        raise RoleRefuse("un rôle livré garde son nom : c'est celui que les textes emploient.")
    apres = _fiche(
        code=code,
        libelle=libelle or avant.libelle,
        permissions=frozenset(permissions),
        systeme=avant.systeme,
        externe=avant.externe,
        reglee_par=par.compte,
        reglee_le=a_l_instant,
    )
    livree = matrice_par_defaut().get(code)
    if livree is not None and livree.permissions == apres.permissions:
        roles.retirer(code)
    else:
        roles.enregistrer(apres)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="roles.reglage",
        objet_type="role",
        objet_id=code,
        avant=_trace(avant),
        apres=_trace(apres),
        motif=motif,
    )
    return apres


def creer_un_role(
    *,
    par: Acces,
    code: str,
    libelle: str,
    permissions: Iterable[Permission],
    motif: str,
    roles: DepotRolesDuCabinet,
    journal: JournalAudit,
    a_l_instant: datetime,
) -> FicheDeRole:
    par.exiger(Permission.GERER_ROLES, motif=motif)
    if code in matrice_en_vigueur() or code in Role.__members__:
        raise RoleRefuse(f"le rôle {code} existe déjà.")
    fiche = _fiche(
        code=code,
        libelle=libelle.strip(),
        permissions=frozenset(permissions),
        systeme=False,
        externe=False,
        reglee_par=par.compte,
        reglee_le=a_l_instant,
    )
    roles.enregistrer(fiche)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="roles.creation",
        objet_type="role",
        objet_id=code,
        apres=_trace(fiche),
        motif=motif,
    )
    return fiche


def retirer_un_role(
    *,
    par: Acces,
    code: str,
    motif: str,
    roles: DepotRolesDuCabinet,
    comptes: DepotComptes,
    habilitations: DepotHabilitations,
    journal: JournalAudit,
    a_l_instant: datetime,
    a_la_date: date,
) -> None:
    """Retire un rôle du cabinet. ⚠️ Refusé tant qu'un compte le tient : ses droits
    disparaîtraient sans que personne n'ait fermé son habilitation, et l'écran des comptes
    montrerait un rôle qui ne donne plus rien."""
    par.exiger(Permission.GERER_ROLES, motif=motif)
    fiche = matrice_en_vigueur().get(code)
    if fiche is None:
        raise RoleIntrouvable(f"le cabinet n'a pas de rôle {code}.")
    if fiche.systeme:
        raise RoleRefuse("un rôle livré ne se retire pas : il se règle.")
    tenu_par = sorted(
        h.compte
        for c in comptes.lister()
        for h in habilitations_actives(habilitations.pour_compte(c.identifiant), a_la_date)
        if str(h.role) == code
    )
    if tenu_par:
        raise RoleRefuse(
            f"le rôle {code} est encore tenu par {', '.join(tenu_par)} : fermer d'abord "
            "leurs habilitations."
        )
    roles.retirer(code)
    journal.ajouter(
        horodatage=a_l_instant,
        acteur=par.compte,
        action="roles.retrait",
        objet_type="role",
        objet_id=code,
        avant=_trace(fiche),
        motif=motif,
    )
