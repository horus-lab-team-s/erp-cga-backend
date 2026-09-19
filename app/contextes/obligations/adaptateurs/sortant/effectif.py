"""Ce que l'échéancier demande au social : le dossier a-t-il employé quelqu'un ?

─────────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE EXISTE (pas 56)

Les cotisations CNPS et les retenues sur salaires sont dues chaque mois où le
dossier a employé quelqu'un, quel que soit son régime. L'échéancier ne lisait pas
le social, et les recevait d'un booléen faux par défaut : elles n'apparaissaient
nulle part. Voir `app/contextes/social/contrats.py`.

⚠️ UNE LECTURE, PUIS DES RÉPONSES

Les contrats du dossier sont lus **une fois**, à la construction de la réponse, et
non à chaque période : un échéancier compte vingt-quatre mois d'obligations sociales,
et vingt-quatre lectures du dépôt pour la même liste seraient vingt-trois de trop.

⚠️ LE SOCIAL QUI NE RÉPOND PAS NE BLOQUE PAS LES OBLIGATIONS

Si la lecture échoue, chaque question reçoit `None`, et l'échéancier affiche les
obligations sociales marquées « effectif à confirmer ». Le calendrier fiscal reste
entier et utilisable : un contexte en panne ne doit pas emporter ceux qui ne lui
posent qu'une question. Et le sens de la prudence est choisi : une obligation montrée
à tort se vérifie, une obligation omise se paie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from app.contextes.social.api import a_employe_sur, depots_du_social

__all__ = ["effectif_du_dossier"]

_journal = logging.getLogger(__name__)


def effectif_du_dossier(entreprise: str) -> Callable[[date, date], bool | None]:
    """La question `emploie_sur(du, au_inclus)` pour ce dossier, prête à être posée."""
    try:
        _, contrats = depots_du_social()
        du_dossier = contrats.du_dossier(entreprise)
    except Exception:  # noqa: BLE001 — voir l'en-tête : aucune panne du social ne remonte ici.
        _journal.exception("fichier du personnel illisible pour %s", entreprise)
        return lambda _du, _au: None
    return lambda du, au_inclus: a_employe_sur(du_dossier, du, au_inclus)
