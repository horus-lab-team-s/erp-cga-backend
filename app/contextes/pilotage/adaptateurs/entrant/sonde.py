"""La sonde du contexte J : les réglages du cabinet se chargent-ils ?

─────────────────────────────────────────────────────────────────────────────────
CE QUE CETTE SONDE ATTRAPE, ET QUE RIEN D'AUTRE N'ATTRAPE

Chaque réglage du Pilotage suit la même règle : **fichier absent, valeurs du domaine ;
fichier présent, il fait foi et un fichier mal formé lève.** C'est la bonne règle, et
elle a un angle mort à l'exploitation : le fichier mal formé ne se manifeste qu'à la
première requête de l'écran concerné, en 500, chez un collaborateur qui n'a rien fait de
mal.

Sept réglages sont chargés ici d'un coup. Ils ne coûtent que sept lectures de petits
fichiers, et ils déplacent la découverte du défaut de « le jour où quelqu'un ouvre
l'écran » à « la minute où la configuration est posée ».

⚠️ **Suspect, jamais panne.** Le Pilotage lit tous les contextes et n'est lu par aucun :
son arrêt n'entraîne personne. Une direction qui ne voit pas son tableau de bord est
gênée ; un adhérent dont la pièce est refusée est lésé. Les deux ne méritent pas le même
signal, et confondre les deux finit par faire ignorer les deux.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.contextes.pilotage.adaptateurs.sortant.charge_yaml import (
    charger_les_reglages_de_la_charge,
)
from app.contextes.pilotage.adaptateurs.sortant.espace_adherent_yaml import (
    charger_les_reglages_de_l_espace_adherent,
)
from app.contextes.pilotage.adaptateurs.sortant.file_d_anomalies_yaml import (
    charger_les_reglages_de_la_file,
)
from app.contextes.pilotage.adaptateurs.sortant.mesures_yaml import (
    charger_le_catalogue_des_mesures,
)
from app.contextes.pilotage.adaptateurs.sortant.pieces_manquantes_yaml import (
    charger_les_reglages_des_pieces_manquantes,
)
from app.contextes.pilotage.adaptateurs.sortant.plan_de_travail_yaml import (
    charger_les_reglages_du_plan,
)
from app.contextes.pilotage.adaptateurs.sortant.rapport_mensuel_yaml import (
    charger_les_reglages_du_rapport,
)
from app.infrastructure.config import configuration
from app.registre.etat import Verdict

__all__ = ["REGLAGES", "sonde_du_pilotage"]

#: Ce que chaque réglage commande, dit du point de vue de celui qui lira le motif. Le
#: libellé nomme **l'écran qui s'arrête**, et non le fichier : « charge.yaml ne se charge
#: pas » n'apprend rien à qui ne connaît pas le dossier.
REGLAGES: tuple[tuple[str, Callable[[Path], object]], ...] = (
    ("la charge des collaborateurs", charger_les_reglages_de_la_charge),
    ("l'accueil de l'adhérent", charger_les_reglages_de_l_espace_adherent),
    ("la file d'anomalies", charger_les_reglages_de_la_file),
    ("les mesures de la direction", charger_le_catalogue_des_mesures),
    ("les pièces manquantes", charger_les_reglages_des_pieces_manquantes),
    ("le plan de travail", charger_les_reglages_du_plan),
    ("le rapport mensuel", charger_les_reglages_du_rapport),
)


def sonde_du_pilotage() -> Verdict | None:
    """Les sept réglages se chargent-ils ?

    ⚠️ **Le premier qui tombe fait foi, et il est nommé.** Rendre « trois réglages sur
    sept sont fautifs » obligerait à tous les charger même après le premier échec, pour
    un gain nul : on corrige le premier, on relit la sonde, elle nomme le suivant.
    """
    referentiel = configuration().dossier_referentiel
    for ce_qu_il_commande, charger in REGLAGES:
        try:
            charger(referentiel)
        except Exception as panne:  # noqa: BLE001 — le motif importe, quel qu'il soit
            return Verdict.suspect(
                f"le réglage de {ce_qu_il_commande} ne se charge pas "
                f"({type(panne).__name__}) : l'écran rendra une erreur au premier "
                f"collaborateur qui l'ouvrira. Vérifier {referentiel / 'pilotage'}"
            )
    return None
