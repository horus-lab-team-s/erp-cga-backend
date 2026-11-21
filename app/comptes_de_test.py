"""Un compte d'essai par rôle, pour éprouver une installation (8 novembre 2026).

    python -m app.comptes_de_test --base tchambaedwin@gmail.com
    python -m app.comptes_de_test --base tchambaedwin@gmail.com --niu M081234567890P
    python -m app.comptes_de_test --base tchambaedwin@gmail.com --retirer

─────────────────────────────────────────────────────────────────────────────────
CE QU'IL FAIT

Pour chaque rôle, un compte `<nom>+<role>@<domaine>` tiré de l'adresse `--base` : avec
une adresse Gmail, tous les courriels de ces comptes arrivent dans la même boîte. Le compte
est ACTIF d'emblée, avec un mot de passe tiré au hasard (16 caractères) affiché UNE fois.
L'habilitation porte « Compte d'essai » et passe au journal d'audit, comme toute autre.

* Les rôles du cabinet portent sur tout le portefeuille.
* ADHERENT et INSPECTEUR exigent un dossier (`--niu`), CLIENT_CREATION un dossier de
  création (`--creation CRE-…`) : sans eux, ces rôles sont sautés, et le script le dit.
* SUPER_ADMIN n'est pas créé par défaut : c'est le premier administrateur qui le porte
  (`app.premier_administrateur`). `--roles SUPER_ADMIN` le force.
* Rejoué, il saute les comptes qui existent déjà (il ne change pas leur mot de passe).

⚠️ CE SONT DES COMPTES RÉELS DE LA BASE. `--retirer` les SUSPEND (un compte ne se supprime
jamais : le journal d'audit le référence). Les retirer avant l'ouverture au public.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import secrets
import sys
import uuid

from app.contextes.transverse.adaptateurs.entrant.dependances import unite_de_travail
from app.contextes.transverse.adaptateurs.sortant.empreinte_argon2 import (
    ServiceEmpreinteArgon2,
)
from app.contextes.transverse.api import LOCATAIRE_PAR_DEFAUT
from app.contextes.transverse.application.administration import (
    ACTEUR_OUVERTURE,
    accorder_a_l_ouverture,
)
from app.contextes.transverse.domaine.habilitations import Habilitation, MotifHabilitation
from app.contextes.transverse.domaine.identites import Compte, EtatCompte
from app.contextes.transverse.domaine.roles import Role, libelle_role
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import etabli

__all__ = ["ROLES_PAR_DEFAUT", "creer_les_comptes_de_test", "retirer_les_comptes_de_test"]

ACTEUR = f"{ACTEUR_OUVERTURE}:comptes-de-test"

#: Tous les rôles sauf SUPER_ADMIN, dans l'ordre de la matrice.
ROLES_PAR_DEFAUT: tuple[Role, ...] = tuple(r for r in Role if r is not Role.SUPER_ADMIN)


def courriel_de_test(base: str, role: Role) -> str:
    local, _, domaine = base.strip().lower().partition("@")
    if not local or not domaine:
        raise ValueError(f"adresse de base invalide : {base!r}")
    local = local.split("+", 1)[0]
    return f"{local}+{role.value.lower().replace('_', '-')}@{domaine}"


def _portee(role: Role, *, niu: str | None, creation: str | None) -> frozenset[str] | None | str:
    """La portée du rôle, ou un motif (str) de le sauter."""
    if role in (Role.ADHERENT, Role.INSPECTEUR):
        return frozenset({niu}) if niu else "il faut un dossier : --niu <NIU>"
    if role is Role.CLIENT_CREATION:
        if creation:
            return frozenset({creation})
        return "il faut un dossier de création : --creation CRE-…"
    return None


def creer_les_comptes_de_test(
    *,
    base: str,
    roles: tuple[Role, ...] = ROLES_PAR_DEFAUT,
    niu: str | None = None,
    creation: str | None = None,
    locataire: str = LOCATAIRE_PAR_DEFAUT,
) -> list[tuple[Role, str, str]]:
    """Crée les comptes ; rend `(rôle, courriel, mot de passe | motif)` pour chacun."""
    instant = maintenant()
    jour = jour_a_douala(instant)
    empreinte = ServiceEmpreinteArgon2()
    bilan: list[tuple[Role, str, str]] = []
    with etabli(locataire), unite_de_travail(locataire) as boutique:
        for role in roles:
            courriel = courriel_de_test(base, role)
            portee = _portee(role, niu=niu, creation=creation)
            if isinstance(portee, str):
                bilan.append((role, courriel, f"sauté : {portee}"))
                continue
            if boutique.comptes.par_courriel(courriel) is not None:
                bilan.append((role, courriel, "existe déjà (mot de passe inchangé)"))
                continue
            mot_de_passe = secrets.token_urlsafe(12)
            compte = Compte(
                identifiant=f"C-{uuid.uuid4().hex[:12]}",
                courriel=courriel,
                nom="ESSAI",
                prenom=libelle_role(role),
                locataire=locataire,
                empreinte_mot_de_passe=empreinte.deriver(mot_de_passe),
                etat=EtatCompte.ACTIF,
                cree_le=instant,
            )
            boutique.comptes.enregistrer(compte)
            boutique.journal.ajouter(
                horodatage=instant,
                acteur=ACTEUR,
                action="compte.cree",
                objet_type="compte",
                objet_id=compte.identifiant,
                apres={"courriel": courriel, "role_initial": role, "essai": True},
            )
            accorder_a_l_ouverture(
                Habilitation(
                    identifiant=f"H-{uuid.uuid4().hex[:12]}",
                    compte=compte.identifiant,
                    role=role,
                    portee=portee,
                    debut=jour,
                    motif=MotifHabilitation.RECRUTEMENT,
                    accordee_par=ACTEUR,
                    precision="Compte d'essai (app.comptes_de_test) : à retirer avant l'ouverture.",
                ),
                habilitations=boutique.habilitations,
                journal=boutique.journal,
                a_l_instant=instant,
            )
            bilan.append((role, courriel, mot_de_passe))
    return bilan


def retirer_les_comptes_de_test(
    *, base: str, roles: tuple[Role, ...] = tuple(Role), locataire: str = LOCATAIRE_PAR_DEFAUT
) -> list[str]:
    """Suspend les comptes d'essai tirés de `base` ; rend leurs courriels."""
    instant = maintenant()
    suspendus: list[str] = []
    with etabli(locataire), unite_de_travail(locataire) as boutique:
        for role in roles:
            compte = boutique.comptes.par_courriel(courriel_de_test(base, role))
            if compte is None or compte.etat is EtatCompte.SUSPENDU:
                continue
            boutique.comptes.enregistrer(compte.suspendre())
            boutique.journal.ajouter(
                horodatage=instant,
                acteur=ACTEUR,
                action="compte.suspendu",
                objet_type="compte",
                objet_id=compte.identifiant,
                apres={"courriel": compte.courriel, "essai": True},
            )
            suspendus.append(compte.courriel)
    return suspendus


def principal(arguments: list[str] | None = None) -> int:
    lecteur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lecteur.add_argument("--base", required=True, help="adresse dont dériver les comptes")
    lecteur.add_argument(
        "--roles", nargs="*", help="rôles à créer (défaut : tous sauf SUPER_ADMIN)"
    )
    lecteur.add_argument("--niu", help="dossier des rôles ADHERENT et INSPECTEUR")
    lecteur.add_argument("--creation", help="dossier de création du rôle CLIENT_CREATION")
    lecteur.add_argument("--retirer", action="store_true", help="suspendre les comptes d'essai")
    lecteur.add_argument("--locataire", default=LOCATAIRE_PAR_DEFAUT)
    options = lecteur.parse_args(arguments)
    try:
        roles = tuple(Role(r.upper()) for r in options.roles) if options.roles else None
        if options.retirer:
            suspendus = retirer_les_comptes_de_test(
                base=options.base, roles=roles or tuple(Role), locataire=options.locataire
            )
            print(f"{len(suspendus)} compte(s) d'essai suspendu(s).")
            for courriel in suspendus:
                print(f"  {courriel}")
            return 0
        bilan = creer_les_comptes_de_test(
            base=options.base,
            roles=roles or ROLES_PAR_DEFAUT,
            niu=options.niu,
            creation=options.creation,
            locataire=options.locataire,
        )
    except ValueError as faute:
        print(f"Refusé : {faute}", file=sys.stderr)
        return 2
    print("Comptes d'essai (les mots de passe ne s'affichent qu'ici) :")
    for role, courriel, mot_de_passe in bilan:
        print(f"  {role.value:<18} {courriel:<48} {mot_de_passe}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
