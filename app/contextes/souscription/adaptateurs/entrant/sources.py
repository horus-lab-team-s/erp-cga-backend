"""Les sources de l'acquisition : dépôts, boîte d'envoi et référentiel, pour la requête.

Extrait de `routes_acquisition.py` le 16 octobre 2026 : sept modules du contexte (routes
et abonnés) importaient ces aides, privées, depuis ce module de routes de 4 000 lignes.
Elles sont publiques ici, en un seul endroit.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.contextes.souscription.adaptateurs.sortant.catalogue_modeles import CatalogueDeModeles
from app.contextes.souscription.adaptateurs.sortant.depots_sql import (
    DepotDossiersSql,
    DepotProformasSql,
)
from app.contextes.souscription.adaptateurs.sortant.grille_tarifaire import (
    charger_les_baremes,
    charger_les_regles_de_tarification,
)
from app.contextes.souscription.adaptateurs.sortant.magasins_memoire import (
    dossiers_memoire,
    proformas_memoire,
)
from app.contextes.souscription.adaptateurs.sortant.plan_de_contact import (
    charger_le_plan_de_contact,
)
from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
    RepertoireDeQuestionnaires,
)
from app.contextes.souscription.adaptateurs.sortant.regles_affectation import (
    charger_la_grille_d_affectation,
)
from app.contextes.transverse.api import session_de_travail
from app.infrastructure.config import configuration
from app.partage.locataire import courant

__all__ = [
    "boite_du_parcours",
    "depot_dossiers",
    "depot_proformas",
    "referentiel_d_acquisition",
]


def depot_dossiers():
    """Le dépôt en vigueur — de la requête en SQL, du processus en mémoire.

    Reconstruit à chaque appel en SQL, parce qu'il porte la session de la requête.
    Le mémoïser conserverait une session déjà fermée, panne qui n'apparaîtrait
    qu'à la seconde requête.
    """
    session = session_de_travail()
    if session is None:
        return dossiers_memoire()
    return DepotDossiersSql(session, courant())


def depot_proformas():
    """Le dépôt des proformas, durable si la base est là."""
    session = session_de_travail()
    if session is None:
        return proformas_memoire()
    return DepotProformasSql(session, courant())


def boite_du_parcours():
    """La boîte d'envoi, par la **surface publique** du Transverse.

    ⚠️ La première rédaction importait l'atelier d'orchestration directement, et le
    test d'architecture l'a refusée : on n'entre chez un autre contexte que par sa
    surface déclarée. Il avait raison, et le remède a été d'exposer la boîte plutôt
    que de la reconstruire — deux fabriques seraient deux boîtes en mémoire, l'une
    déposerait et l'autre publierait.
    """
    from app.contextes.transverse.api import boite_d_envoi

    return boite_d_envoi()


@lru_cache
def referentiel_d_acquisition() -> dict[str, Any]:
    """Les quatre paquets de configuration, lus une fois par processus.

    Mémoïsés parce qu'ils sont sur disque et ne changent qu'au déploiement ou par
    un geste d'exploitation. Les relire à chaque requête ferait quatre lectures de
    fichiers sur le chemin critique d'un formulaire public.

    ⚠️ La contrepartie est qu'un fichier modifié à chaud n'est pas repris. C'est
    assumé : le référentiel est versionné avec le code, et une reprise à chaud
    demanderait un rechargement explicite, qui n'existe pas encore.
    """
    # ⚠️ LE DOSSIER DU RÉFÉRENTIEL VIENT DE LA CONFIGURATION, JAMAIS DU DÉPÔT.
    #
    # Il était écrit en dur, `RACINE_DEPOT / "Docs" / "referentiel"`. Les tests
    # tournent depuis le dépôt, où ce chemin existe : ils passaient. L'image, elle,
    # range le référentiel dans `/application/donnees/referentiel` et le dit par
    # `CGA_DOSSIER_REFERENTIEL`. Le chemin en dur n'y menait à rien, et TOUTE la
    # surface d'acquisition répondait 500 — la demande de contact publique
    # comprise, c'est-à-dire la seule porte par laquelle un prospect demande à être
    # rappelé. Constaté sur la pile déployée.
    REFERENTIEL = configuration().dossier_referentiel
    return {
        "affectation": charger_la_grille_d_affectation(REFERENTIEL / "affectation"),
        "questionnaires": RepertoireDeQuestionnaires.depuis(
            REFERENTIEL / "qualification"
        ),
        "plan": charger_le_plan_de_contact(REFERENTIEL / "messagerie"),
        "modeles": CatalogueDeModeles.depuis(REFERENTIEL / "messagerie" / "modeles"),
        "baremes": charger_les_baremes(REFERENTIEL / "tarification"),
        "regles_tarifaires": charger_les_regles_de_tarification(
            REFERENTIEL / "tarification"
        ),
    }
