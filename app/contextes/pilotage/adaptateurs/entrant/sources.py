"""Les sources du pilotage : les dépôts des autres contextes, lus pour la requête en cours.

Extrait de `routes_http.py` le 16 octobre 2026 : quatre modules de routes importaient ces
aides, privées, depuis un autre module de routes. Elles sont publiques ici, en un seul
endroit, et chaque route les prend à la même source.

Chaque dépôt suit la même bascule : la base dans une requête (session de travail),
la mémoire sinon (essais, démonstration sans base).
"""

from __future__ import annotations

from datetime import date

from app.contextes.collecte.api import (
    DepotDemandesSql,
    DepotPiecesSql,
    demandes_en_memoire,
    pieces_en_memoire,
)
from app.contextes.comptabilite.api import DepotEcrituresSql, ecritures_en_memoire
from app.contextes.obligations.api import (
    DepotTypesObligationMemoire,
    accuses_du_portail,
    effectif_du_dossier,
    generer_echeancier,
)
from app.contextes.portefeuille.api import DepotEntreprisesSql, entreprises_en_memoire
from app.contextes.transverse.api import session_de_travail
from app.partage.locataire import courant

__all__ = [
    "depot_demandes",
    "depot_dossiers",
    "depot_ecritures",
    "depot_pieces",
    "echeancier_du_dossier",
    "sans_echouer",
]

_CATALOGUE = DepotTypesObligationMemoire()


def depot_dossiers():
    session = session_de_travail()
    if session is None:
        return entreprises_en_memoire()
    return DepotEntreprisesSql(session, courant())


def depot_pieces():
    session = session_de_travail()
    if session is None:
        return pieces_en_memoire()
    return DepotPiecesSql(session, courant())


def depot_demandes():
    session = session_de_travail()
    if session is None:
        return demandes_en_memoire()
    return DepotDemandesSql(session, courant())


def depot_ecritures(entreprise: str):
    session = session_de_travail()
    if session is None:
        return ecritures_en_memoire(entreprise)
    return DepotEcrituresSql(session, courant(), entreprise)


def echeancier_du_dossier(dossier, exercice: str, jour: date):
    periode = next((e for e in dossier.exercices if e.libelle == exercice), None)
    if periode is None:
        return []
    # ⚠️ La même question que l'échéancier des obligations (pas 56) : sans elle, le
    # pilotage ne comptait jamais une CNPS en retard.
    return generer_echeancier(
        dossier,
        _CATALOGUE.charger(jour),
        periode,
        emploie_sur=effectif_du_dossier(dossier.niu),
        # ⚠️ Sans elle, une déclaration déposée pesait sur le score comme un retard
        # (pas 58).
        accuse_de=accuses_du_portail(),
    )


def sans_echouer(lecture, repli):
    """Exécute une lecture, ou rend le repli.

    Le tableau de bord agrège cinq contextes : si l'un d'eux refuse, la direction
    doit quand même voir les quatre autres. L'exception est avalée **ici et
    seulement ici** — dans un agrégateur, jamais dans un calcul.

    ⚠️ **Sauf une erreur de programmation** (pas 104). Une méthode qui n'existe pas, un
    mauvais nombre d'arguments ne sont pas un contexte indisponible : ce sont des défauts du
    code, et les avaler les rend invisibles pour toujours. C'est exactement ce qui est arrivé
    aux demandes sans réponse, dont la composante a valu zéro sans que personne ne le voie.
    """
    try:
        return lecture()
    except (AttributeError, TypeError, NameError):
        raise
    except Exception:  # noqa: BLE001 — voir la docstring : c'est un agrégateur.
        return repli
