"""Compléter le jeu de démonstration, pour une séance de présentation (20 octobre 2026).

    python -m app.jeu_de_presentation /chemin/jeu.yaml

─────────────────────────────────────────────────────────────────────────────────
⚠️ POURQUOI CETTE COMMANDE

La recette en présentiel, chez la cliente, joue chaque parcours avec un compte de chaque
rôle et des clients fictifs ; d'autres clients s'ajouteront d'une séance à l'autre. Le jeu
de démonstration (`amorcage.py`) est figé dans le code : celle-ci le complète depuis un
fichier, sans toucher au code.

⚠️ CE QU'ELLE FAIT

* l'ÉQUIPE : un compte actif par personne, au mot de passe de la démonstration, et son
  habilitation (rôle, portée : des NIU, une référence de création, ou tout le cabinet) ;
* les CLIENTS : l'entreprise au portefeuille (forme, régime, centre, adhésion, exercices,
  dirigeant), son adhérent, et les collaborateurs qui la suivent (une habilitation de plus,
  portée sur ce NIU) ;
* RIEN DE CE QUI EXISTE N'EST RÉÉCRIT : un courriel ou un NIU déjà présent est passé, et
  la commande se relance sans risque avec un fichier complété ;
* ELLE REFUSE UN ENVIRONNEMENT EXPOSÉ, comme l'amorçage : des comptes au mot de passe publié
  n'ont rien à faire en production.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from app.contextes.portefeuille.api import (
    Adhesion,
    CentreRattachement,
    DepotEntreprisesSql,
    Dirigeant,
    Entreprise,
    Exercice,
    FormeJuridique,
    MandatDeclaratif,
    MotifChangement,
    NatureActivite,
    RegimeFiscal,
    StatutRattachement,
    StatutRegime,
)
from app.contextes.transverse.api import (
    LOCATAIRE_PAR_DEFAUT,
    MOT_DE_PASSE_DEMO,
    Compte,
    EtatCompte,
    Habilitation,
    MotifHabilitation,
    Role,
    ServiceEmpreinteArgon2,
    unite_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import jour_a_douala, maintenant

__all__ = ["JeuRefuse", "verser_le_jeu"]


class JeuRefuse(RuntimeError):
    """Un environnement exposé, ou un fichier qui ne se lit pas."""


def _exercices(depuis: date) -> list[Exercice]:
    """Exercices civils jusqu'à l'année en cours, clos sauf le dernier."""
    courant = maintenant().year
    return [
        Exercice(
            libelle=str(annee),
            ouverture=date(annee, 1, 1),
            cloture=date(annee, 12, 31),
            clos=annee < courant,
        )
        for annee in range(depuis.year, courant + 1)
    ]


def _entreprise(c: dict) -> Entreprise:
    creee = date.fromisoformat(str(c["date_creation"]))
    adhesion = date.fromisoformat(str(c.get("adhesion_depuis", f"{maintenant().year}-01-01")))
    dirigeant = c.get("dirigeant") or f"{c['adherent']['nom']} {c['adherent']['prenom']}"
    return Entreprise(
        niu=c["niu"],
        denomination=c["denomination"],
        forme_juridique=FormeJuridique(c.get("forme", "SARL")),
        date_creation=creee,
        rccm=c.get("rccm"),
        capital=Decimal(str(c["capital"])) if c.get("capital") is not None else None,
        activite=c.get("activite"),
        nature_activite=NatureActivite(c.get("nature", "COMMERCIALE")),
        siege=c.get("siege"),
        regimes=[
            StatutRegime(
                debut=creee,
                regime=RegimeFiscal(c.get("regime", "REEL")),
                motif=MotifChangement.CREATION,
            )
        ],
        rattachements=[
            StatutRattachement(
                debut=creee,
                centre=CentreRattachement(c.get("centre", "CDI")),
                motif=MotifChangement.CREATION,
            )
        ],
        adhesions=[
            Adhesion(
                debut=adhesion,
                numero=c.get("numero_adhesion", f"ADH-{adhesion.year}-{c['niu'][-4:]}"),
                motif=MotifChangement.ADHESION,
            )
        ],
        mandats=[
            MandatDeclaratif(
                debut=adhesion,
                motif=MotifChangement.ADHESION,
                obligations=list(c.get("mandat", ["TVA", "IRPP_ACOMPTE", "DSF"])),
                signe_le=adhesion,
                signe_par=dirigeant,
            )
        ],
        exercices=_exercices(creee),
        dirigeants=[Dirigeant(nom=dirigeant, qualite=c.get("qualite", "Gérant"), depuis=creee)],
    )


def verser_le_jeu(chemin: Path, locataire: str = LOCATAIRE_PAR_DEFAUT) -> dict[str, list[str]]:
    """Verse le fichier ; rend ce qui a été créé et ce qui existait déjà."""
    if configuration().en_production:
        raise JeuRefuse(
            f"environnement {configuration().environnement} : un jeu de présentation n'a "
            "rien à faire ici."
        )
    try:
        jeu = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as panne:
        raise JeuRefuse(f"{chemin} ne se lit pas : {panne}") from panne

    empreinte = ServiceEmpreinteArgon2().deriver(str(jeu.get("mot_de_passe", MOT_DE_PASSE_DEMO)))
    instant = maintenant()
    bilan: dict[str, list[str]] = {"crees": [], "deja_la": []}

    with unite_de_travail(locataire) as boutique:
        comptes = {c.courriel: c for c in boutique.comptes.lister()}

        def personne(p: dict, role: Role, portee: set[str] | None, precision: str) -> None:
            courriel = p["courriel"].strip().lower()
            compte = comptes.get(courriel)
            if compte is None:
                compte = Compte(
                    identifiant=f"P-{uuid.uuid4().hex[:10]}",
                    courriel=courriel,
                    nom=p["nom"],
                    prenom=p["prenom"],
                    locataire=locataire,
                    telephone=p.get("telephone"),
                    empreinte_mot_de_passe=empreinte,
                    etat=EtatCompte.ACTIF,
                    cree_le=instant,
                )
                boutique.comptes.enregistrer(compte)
                comptes[courriel] = compte
                bilan["crees"].append(f"compte {courriel}")
            deja = any(
                h.role is role and h.fin is None and (h.portee or None) == (portee or None)
                for h in boutique.habilitations.pour_compte(compte.identifiant)
            )
            if deja:
                bilan["deja_la"].append(f"{courriel} : {role.value}")
                return
            boutique.habilitations.enregistrer(
                Habilitation(
                    identifiant=f"H-{uuid.uuid4().hex[:10]}",
                    compte=compte.identifiant,
                    role=role,
                    portee=None if portee is None else frozenset(portee),
                    debut=jour_a_douala(instant),
                    motif=MotifHabilitation.RECRUTEMENT,
                    accordee_par="système:jeu-de-presentation",
                    precision=precision,
                )
            )
            bilan["crees"].append(f"{courriel} : {role.value}")

        for p in jeu.get("equipe", []):
            portee = p.get("portee", "tout")
            personne(
                p,
                Role(p["role"]),
                None if portee == "tout" else set(portee if isinstance(portee, list) else [portee]),
                p.get("precision", "Jeu de présentation"),
            )

        dossiers = DepotEntreprisesSql(boutique.session, locataire)
        connus = {e.niu for e in dossiers.lister()}
        for c in jeu.get("clients", []):
            if c["niu"] not in connus:
                dossiers.enregistrer(_entreprise(c))
                bilan["crees"].append(f"entreprise {c['niu']} {c['denomination']}")
            else:
                bilan["deja_la"].append(f"entreprise {c['niu']}")
            personne(c["adherent"], Role.ADHERENT, {c["niu"]}, f"Adhérent de {c['denomination']}")
            for suivi in c.get("suivi_par", []):
                personne(
                    # Un collaborateur déjà présent : seul son courriel compte ici.
                    dict(courriel=suivi["courriel"], nom="", prenom=""),
                    Role(suivi["role"]),
                    {c["niu"]},
                    f"Suit {c['denomination']}",
                )
    return bilan


def principal(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1:
        print("usage : python -m app.jeu_de_presentation <jeu.yaml>", file=sys.stderr)
        return 2
    try:
        bilan = verser_le_jeu(Path(arguments[0]))
    except JeuRefuse as refus:
        print(f"Refusé : {refus}", file=sys.stderr)
        return 1
    for ligne in bilan["crees"]:
        print(f"  + {ligne}")
    print(f"{len(bilan['crees'])} créé(s), {len(bilan['deja_la'])} déjà présent(s).")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
