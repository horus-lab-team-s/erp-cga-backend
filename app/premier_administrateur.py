"""Le premier administrateur d'une base de production neuve (20 octobre 2026).

    python -m app.premier_administrateur --courriel a.b@cga-brcg.cm --nom MBALLA --prenom Bernadette

─────────────────────────────────────────────────────────────────────────────────
⚠️ POURQUOI CETTE COMMANDE

Sur une base neuve, personne ne porte `GERER_COMPTES` : le compte qui la porterait est
celui qu'on crée. Seul `amorcage.py` écrivait des comptes, et il écrit ceux de la
démonstration, avec leur mot de passe publié dans le dépôt. Constaté à l'audit avant la
mise en production.

⚠️ CE QU'ELLE FAIT, ET RIEN DE PLUS

* elle refuse une base qui contient déjà un compte : le premier administrateur ne se
  crée qu'une fois, ensuite c'est lui qui invite ;
* elle passe par `creer_l_administrateur_d_un_tenant`, le cas d'usage de l'ouverture
  d'un tenant : compte sans mot de passe, habilitation ADMINISTRATEUR sur tout le
  portefeuille, jeton d'invitation, journal d'audit ;
* elle y ajoute le rôle SUPER_ADMIN : sans lui, personne ne réglerait jamais la matrice
  des rôles ni n'accorderait une grâce ;
* le lien d'activation part par courriel (le gabarit `compte.invitation`) ET s'affiche
  ici : si le relais refuse, l'exploitant le transmet lui-même. Aucun mot de passe ne
  passe par cette commande ; le titulaire choisit le sien en suivant le lien.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import sys
import uuid

from app.contextes.transverse.adaptateurs.entrant.dependances import (
    service_de_notification,
    unite_de_travail,
)
from app.contextes.transverse.api import LOCATAIRE_PAR_DEFAUT
from app.contextes.transverse.application.administration import (
    ACTEUR_OUVERTURE,
    accorder_a_l_ouverture,
    creer_l_administrateur_d_un_tenant,
)
from app.contextes.transverse.domaine.habilitations import Habilitation, MotifHabilitation
from app.contextes.transverse.domaine.roles import Role
from app.infrastructure.config import configuration
from app.partage.horloge import jour_a_douala, maintenant
from app.partage.locataire import etabli

__all__ = ["BaseDejaHabitee", "creer_le_premier_administrateur"]


class BaseDejaHabitee(RuntimeError):
    """La base a déjà des comptes : le premier administrateur existe."""


def creer_le_premier_administrateur(
    *, courriel: str, nom: str, prenom: str, locataire: str = LOCATAIRE_PAR_DEFAUT
) -> str:
    """Crée le compte et ses deux habilitations ; rend le lien d'activation."""
    instant = maintenant()
    with etabli(locataire), unite_de_travail(locataire) as boutique:
        if boutique.comptes.lister():
            raise BaseDejaHabitee(
                "cette base contient déjà des comptes : le premier administrateur existe. "
                "Les suivants s'invitent depuis la console (Comptes et habilitations)."
            )
        resultat = creer_l_administrateur_d_un_tenant(
            locataire=locataire,
            identifiant_compte=f"C-{uuid.uuid4().hex[:12]}",
            identifiant_habilitation=f"H-{uuid.uuid4().hex[:12]}",
            identifiant_jeton=f"J-{uuid.uuid4().hex[:12]}",
            courriel=courriel.strip().lower(),
            nom=nom.strip(),
            prenom=prenom.strip(),
            depuis=jour_a_douala(instant),
            comptes=boutique.comptes,
            habilitations=boutique.habilitations,
            jetons=boutique.jetons,
            journal=boutique.journal,
            a_l_instant=instant,
        )
        assert resultat is not None  # la base était vide : le compte vient d'être créé
        compte, jeton, secret = resultat
        accorder_a_l_ouverture(
            Habilitation(
                identifiant=f"H-{uuid.uuid4().hex[:12]}",
                compte=compte.identifiant,
                role=Role.SUPER_ADMIN,
                portee=None,
                debut=jour_a_douala(instant),
                motif=MotifHabilitation.RECRUTEMENT,
                accordee_par=ACTEUR_OUVERTURE,
                precision="Premier administrateur de la base de production.",
            ),
            habilitations=boutique.habilitations,
            journal=boutique.journal,
            a_l_instant=instant,
        )

    lien = f"{configuration().adresse_publique_site.rstrip('/')}/activation?jeton={secret}"
    try:
        service_de_notification().envoyer(
            "compte.invitation",
            destinataire=compte.courriel,
            contexte={
                "prenom": compte.appellation,
                "role": "Administrateur",
                "lien": lien,
                "expire_le": f"{jeton.expire_le:%d/%m/%Y à %Hh%M}",
            },
        )
    except Exception as panne:  # noqa: BLE001 — le lien s'affiche de toute façon
        print(f"⚠️ Courriel non envoyé ({panne}) : transmettez le lien ci-dessous.")
    return lien


def principal(arguments: list[str] | None = None) -> int:
    lecteur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lecteur.add_argument("--courriel", required=True)
    lecteur.add_argument("--nom", required=True)
    lecteur.add_argument("--prenom", required=True)
    lecteur.add_argument("--locataire", default=LOCATAIRE_PAR_DEFAUT)
    options = lecteur.parse_args(arguments)
    try:
        lien = creer_le_premier_administrateur(
            courriel=options.courriel,
            nom=options.nom,
            prenom=options.prenom,
            locataire=options.locataire,
        )
    except BaseDejaHabitee as refus:
        print(f"Refusé : {refus}", file=sys.stderr)
        return 1
    print(f"Compte créé pour {options.courriel}. Lien d'activation (valable une fois) :")
    print(lien)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
