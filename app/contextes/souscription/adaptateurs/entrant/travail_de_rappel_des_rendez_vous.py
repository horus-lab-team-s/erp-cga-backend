"""Le rappel de la veille d'un rendez-vous de création (9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
POURQUOI

Un rendez-vous pris sur le site une semaine plus tôt s'oublie, et un rendez-vous manqué
coûte une pénalité au client et une heure au cabinet. La veille, le client reçoit le
rappel : l'heure, le lieu (ou le lien, s'il est posé), et qu'il peut prévenir.

⚠️ REJOUABLE. `rappel_envoye_le` porte la garde : un tour rejoué n'envoie pas deux fois.
⚠️ IL NE LÈVE PAS SUR UN ENVOI IMPOSSIBLE : sans adresse, rien à faire ; un poste
indisponible laisse le rendez-vous sans marque, et le tour suivant réessaie.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import a_douala, maintenant

__all__ = ["NOM_TRAVAIL", "rappeler", "travail_de_rappel_des_rendez_vous"]

NOM_TRAVAIL = "rappel-des-rendez-vous"
_journal = logging.getLogger(__name__)

#: Combien d'heures avant le rendez-vous le rappel part.
AVANCE = timedelta(hours=24)


def travail_de_rappel_des_rendez_vous() -> Travail:
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(minutes=30),
        objet="rappelle la veille au client chacun de ses rendez-vous de création",
    )


def rappeler(_boite=None) -> str:
    from app.contextes.souscription.adaptateurs.entrant.routes_acquisition import (
        phrase_du_rendez_vous,
    )
    from app.contextes.souscription.adaptateurs.entrant.routes_http import comptoir
    from app.contextes.souscription.adaptateurs.entrant.routes_rendez_vous import (
        courriel_du_dossier,
    )
    from app.contextes.souscription.adaptateurs.entrant.sources import depot_dossiers
    from app.contextes.souscription.adaptateurs.sortant.depots_memoire import DossierIntrouvable
    from app.contextes.souscription.domaine.rendez_vous import EtatRendezVous, ModeRendezVous
    from app.contextes.transverse.api import service_de_notification

    boutique = comptoir()
    instant = a_douala(maintenant())
    envoyes = 0
    for rdv in boutique.rendez_vous.depuis(instant):
        if rdv.etat is not EtatRendezVous.PREVU or rdv.rappel_envoye_le is not None:
            continue
        if rdv.debut > instant + AVANCE:
            continue
        try:
            dossier = depot_dossiers().lire(rdv.dossier)
        except DossierIntrouvable:
            continue
        courriel = courriel_du_dossier(dossier)
        if not courriel:
            continue
        if rdv.mode is ModeRendezVous.EN_LIGNE:
            acces = (
                f"Votre lien de rendez-vous : {rdv.lien}"
                if rdv.lien
                else "Le cabinet vous envoie le lien de visioconférence avant l'heure."
            )
        else:
            acces = "Le cabinet vous reçoit à son adresse, indiquée sur son site."
        demande = dossier.demande
        try:
            service_de_notification().envoyer(
                "rendez_vous.rappel",
                destinataire=courriel,
                contexte={
                    "prenom": demande.nom,
                    "rendez_vous": phrase_du_rendez_vous(rdv),
                    "acces": acces,
                },
            )
        except Exception:  # noqa: BLE001 — voir l'en-tête : le tour suivant réessaie
            _journal.warning("rendez-vous %s : rappel non envoyé.", rdv.identifiant)
            continue
        boutique.rendez_vous.enregistrer(rdv.model_copy(update={"rappel_envoye_le": maintenant()}))
        envoyes += 1
    return f"{envoyes} rappel(s) de rendez-vous envoyé(s)"
