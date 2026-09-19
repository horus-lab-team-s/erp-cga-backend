"""La sonde du contexte M : le parcours d'acquisition peut-il fonctionner ?

⚠️ **C'est la sonde la plus étendue du système, et c'est justifié.** Le parcours
d'acquisition lit cinq paquets de configuration distincts, et chacun bloque une étape
différente : sans catalogue, aucun devis ; sans questionnaire, aucune qualification ;
sans barème, aucun chiffrage ; sans plan de contact, aucun message ; sans plan de
relance, aucune relance.

Une installation à laquelle il manque un seul de ces fichiers **répond
normalement jusqu'à l'étape concernée**, puis échoue sur un client réel.
"""

from __future__ import annotations

from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["sonde_de_la_souscription"]


def sonde_de_la_souscription() -> Verdict | None:
    """Les cinq paquets du parcours se chargent-ils ?

    ─────────────────────────────────────────────────────────────────────────────
    ⚠️ **Une panne franche.** Aucun de ces manques n'a d'explication innocente : ce
    sont des fichiers du dépôt, présents ou absents. Un fichier absent est une
    installation incomplète, jamais un état transitoire.

    LE MOTIF NOMME LE PAQUET ET LE DOSSIER

    « la souscription ne fonctionne pas » enverrait chercher. Le motif dit lequel
    des cinq manque et où il devrait être, ce qui suffit à agir.

    ⚠️ **Le premier manquant arrête la vérification.** Les cinq chargements sont
    indépendants, et l'on pourrait tous les tenter pour rendre la liste complète.
    C'est délibérément évité : un exploitant corrige un fichier à la fois, et une
    liste de cinq lignes lui ferait croire à cinq problèmes distincts là où le
    premier est souvent la cause des autres — un dossier de référentiel mal monté
    les fait tous manquer ensemble.
    ─────────────────────────────────────────────────────────────────────────────
    """
    from app.contextes.souscription.adaptateurs.sortant.catalogue_offre import CATALOGUE
    from app.contextes.souscription.adaptateurs.sortant.grille_tarifaire import (
        charger_les_baremes,
        charger_les_regles_de_tarification,
    )
    from app.contextes.souscription.adaptateurs.sortant.plan_de_contact import (
        charger_le_plan_de_contact,
    )
    from app.contextes.souscription.adaptateurs.sortant.plan_de_relance import (
        charger_le_plan_de_relance,
    )
    from app.contextes.souscription.adaptateurs.sortant.questionnaires import (
        RepertoireDeQuestionnaires,
    )

    referentiel = configuration().dossier_referentiel

    if not CATALOGUE:
        return Verdict.panne(
            "le catalogue de l'offre est vide : aucun devis ne pourrait être établi"
        )

    verifications = (
        (
            "les questionnaires de qualification",
            referentiel / "qualification",
            lambda d: len(RepertoireDeQuestionnaires.depuis(d)),
        ),
        (
            "les barèmes de tarification",
            referentiel / "tarification",
            lambda d: len(charger_les_baremes(d)),
        ),
        (
            "les règles de tarification",
            referentiel / "tarification",
            lambda d: len(charger_les_regles_de_tarification(d)),
        ),
        (
            "le plan de contact",
            referentiel / "messagerie",
            lambda d: len(charger_le_plan_de_contact(d).canaux),
        ),
        (
            "le plan de relance",
            referentiel / "relance",
            lambda d: len(charger_le_plan_de_relance(d).paliers),
        ),
    )

    for quoi, dossier, charger in verifications:
        try:
            combien = charger(dossier)
        except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
            return Verdict.panne(
                f"{quoi} ne se charge{'nt' if quoi.startswith('les') else ''} pas "
                f"({type(panne).__name__}) : vérifier {dossier}"
            )
        if not combien:
            return Verdict.panne(f"{quoi} : rien de chargé depuis {dossier}")
    return None
