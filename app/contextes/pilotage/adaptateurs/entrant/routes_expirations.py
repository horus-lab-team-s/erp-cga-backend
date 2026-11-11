"""Ce qui expire au portefeuille, et les alertes (lot 4, 9 novembre 2026).

─────────────────────────────────────────────────────────────────────────────────
* `GET /pilotage/expirations` : le panneau « Expirations à venir » — la direction voit tout
  le cabinet, qui tient des dossiers voit son périmètre ;
* le travail de fond `alertes-d-expiration` : à chaque palier atteint (réglages), une entrée
  au journal — la cloche du gestionnaire et celle de l'adhérent la lisent, par abonnement —
  et un courriel aux adhérents du dossier.

⚠️ REJOUABLE. Le journal sert de mémoire : un palier déjà signalé pour cette chose à cette
date (`objet_id` = clé + palier) ne l'est pas deux fois.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.pilotage.adaptateurs.entrant.sources import depot_dossiers
from app.contextes.pilotage.adaptateurs.sortant.expirations_yaml import (
    charger_les_reglages_des_expirations,
)
from app.contextes.pilotage.domaine.expirations import (
    Expiration,
    ReglagesDesExpirations,
    expirations_de,
    palier_a_signaler,
)
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    Role,
    atelier,
    exiger,
    habilitations_actives,
    restreindre,
    service_de_notification,
)
from app.infrastructure.config import configuration
from app.orchestration.ordonnanceur import Travail
from app.partage.horloge import aujourd_hui, maintenant

__all__ = ["alerter_des_expirations", "routeur", "travail_des_alertes_d_expiration"]

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · expirations"])
_journal = logging.getLogger(__name__)

NOM_TRAVAIL = "alertes-d-expiration"
ACTION = "expiration.signalee"


def _reglages() -> ReglagesDesExpirations:
    return charger_les_reglages_des_expirations(configuration().dossier_referentiel)


class ExpirationsDuCabinet(BaseModel):
    a_la_date: date
    tout_le_cabinet: bool
    expirations: list[Expiration]
    reglages: ReglagesDesExpirations


@routeur.get("/expirations", summary="Ce qui expire au portefeuille, ou vient d'expirer")
def lire_les_expirations(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> ExpirationsDuCabinet:
    tout = acces.detient(Permission.LIRE_PILOTAGE)
    if not tout:
        exiger(acces, Permission.SAISIR_ECRITURE)
    jour = a_la_date or aujourd_hui()
    reglages = _reglages()
    dossiers = depot_dossiers().lister()
    if not tout:
        dossiers = restreindre(acces, dossiers, lambda d: d.niu)
    retenues = [
        e
        for d in dossiers
        for e in expirations_de(d, jour)
        if -reglages.expires_affiches_jours <= e.jours_restants <= reglages.horizon_jours
    ]
    retenues.sort(key=lambda e: (e.jours_restants, e.denomination))
    return ExpirationsDuCabinet(
        a_la_date=jour, tout_le_cabinet=tout, expirations=retenues, reglages=reglages
    )


# ── Le travail de fond ────────────────────────────────────────────────────────────


def travail_des_alertes_d_expiration() -> Travail:
    return Travail(
        nom=NOM_TRAVAIL,
        cadence=timedelta(hours=1),
        objet=(
            "prévient le gestionnaire et l'adhérent de ce qui expire au portefeuille, "
            "à chaque palier réglé par le cabinet"
        ),
    )


def _adherents_du_dossier(niu: str, jour: date):
    """Les comptes adhérents habilités sur ce dossier : ceux qu'on prévient par courriel."""
    boutique = atelier()
    comptes = []
    for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
        if h.role != Role.ADHERENT:
            continue
        try:
            comptes.append(boutique.comptes.lire(h.compte))
        except Exception:  # noqa: BLE001 — un compte illisible ne retient pas les autres
            continue
    return comptes


def _deja_signales(cle: str) -> set[int]:
    journal = atelier().journal
    return {
        int(str(e.objet_id).rsplit(":", 1)[1])
        for e in journal.lister(objet_type="expiration")
        if str(e.objet_id).startswith(f"{cle}:")
    }


def alerter_des_expirations(_boite=None, a_la_date: date | None = None) -> str:
    jour = a_la_date or aujourd_hui()
    reglages = _reglages()
    signalees = 0
    for entreprise in depot_dossiers().lister():
        for e in expirations_de(entreprise, jour):
            palier = palier_a_signaler(
                e.jours_restants, reglages.paliers_jours, _deja_signales(e.cle)
            )
            if palier is None:
                continue
            atelier().journal.ajouter(
                horodatage=maintenant(),
                acteur="systeme",
                action=ACTION,
                objet_type="expiration",
                objet_id=f"{e.cle}:{palier}",
                apres={
                    "dossier": e.dossier,
                    "denomination": e.denomination,
                    "libelle": e.libelle,
                    "expire_le": e.expire_le.strftime("%d/%m/%Y"),
                    "jours": e.jours_restants,
                },
            )
            for compte in _adherents_du_dossier(e.dossier, jour):
                try:
                    service_de_notification().envoyer(
                        "expiration.adherent",
                        destinataire=compte.courriel,
                        contexte={
                            "prenom": compte.appellation,
                            "denomination": e.denomination,
                            "libelle": e.libelle,
                            "echeance": (
                                f"a expiré le {e.expire_le:%d/%m/%Y}"
                                if e.jours_restants < 0
                                else f"expire le {e.expire_le:%d/%m/%Y}"
                            ),
                        },
                    )
                except Exception:  # noqa: BLE001 — la cloche a l'alerte ; le courriel n'en est que l'écho
                    _journal.warning("dossier %s : courriel d'expiration non envoyé.", e.dossier)
            signalees += 1
    return f"{signalees} expiration(s) signalée(s)"
