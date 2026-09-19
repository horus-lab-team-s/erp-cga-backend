"""Mes rappels d'échéance : le réglage de l'adhérent (pas 115), sous `/obligations`.

─────────────────────────────────────────────────────────────────────────────────
DEUX ROUTES

* `GET /obligations/dossiers/{entreprise}/mes-rappels` (`REGLER_SES_RAPPELS`) : le réglage du compte
  (ou le défaut du référentiel, dit comme tel), les jalons possibles, et pourquoi WhatsApp est grisé.
* `POST /obligations/dossiers/{entreprise}/mes-rappels` : règle. Le réglage est **au compte**, pas au
  dossier : un adhérent qui suit deux entreprises choisit une fois comment il veut être prévenu.

LE RÉGLAGE EST UNE ENTRÉE DATÉE DU JOURNAL

La dernière entrée `obligations.rappels_regles` du compte fait foi. Pas de table : un réglage à deux
champs, relu une fois par heure, n'en demande pas, et le journal répond en plus à « qui a coupé les
rappels, et quand », la première question le jour où un adhérent dit n'avoir jamais été prévenu.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contextes.obligations.adaptateurs.sortant.rappels_adherent_yaml import (
    charger_les_rappels_de_l_adherent,
)
from app.contextes.obligations.domaine.rappels_adherent import (
    PreferenceDeRappels,
    ReglagesDesRappels,
)
from app.contextes.transverse.api import AccesRequis, Permission, atelier, exiger_dossier
from app.infrastructure.config import configuration
from app.partage.erreurs import message_lisible
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/obligations", tags=["Obligations · rappels de l'adhérent"])

#: Le type d'objet des réglages au journal, par compte.
OBJET_REGLAGE = "reglage_des_rappels"


def reglages_des_rappels() -> ReglagesDesRappels:
    return charger_les_rappels_de_l_adherent(configuration().dossier_referentiel)


def preference_du_compte(compte: str, reglages: ReglagesDesRappels) -> PreferenceDeRappels:
    """La dernière préférence réglée par le compte, sinon le défaut du référentiel (`defini` faux)."""
    entrees = [
        e
        for e in atelier().journal.lister(objet_type=OBJET_REGLAGE, objet_id=compte)
        if e.action == "obligations.rappels_regles"
    ]
    if not entrees:
        return PreferenceDeRappels(
            actifs=reglages.actifs_par_defaut, jalons=reglages.jalons_par_defaut
        )
    derniere = entrees[-1].apres
    return PreferenceDeRappels(
        actifs=derniere["actifs"], jalons=tuple(derniere["jalons"]), defini=True
    )


class VueDesRappels(BaseModel):
    preference: PreferenceDeRappels
    jalons_possibles: list[int]
    whatsapp_disponible: bool
    motif_whatsapp: str


def _vue(compte: str) -> VueDesRappels:
    reglages = reglages_des_rappels()
    return VueDesRappels(
        preference=preference_du_compte(compte, reglages),
        jalons_possibles=sorted(reglages.jalons_possibles, reverse=True),
        # ⚠️ Écrit en dur **à faux**, et c'est une donnée, pas un oubli : aucun envoi WhatsApp n'existe.
        # Le jour où il existera, cette valeur viendra du registre des canaux.
        whatsapp_disponible=False,
        motif_whatsapp=reglages.motif_whatsapp,
    )


@routeur.get(
    "/dossiers/{entreprise}/mes-rappels",
    summary="Le réglage des rappels d'échéance du compte connecté",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_mes_rappels(acces: AccesRequis, entreprise: str) -> VueDesRappels:
    exiger_dossier(acces, Permission.REGLER_SES_RAPPELS, entreprise)
    return _vue(acces.compte)


class ReglageDemande(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actifs: bool
    jalons: list[int] = Field(default_factory=list, max_length=10)


@routeur.post(
    "/dossiers/{entreprise}/mes-rappels",
    summary="Régler ses rappels d'échéance",
    # POST et non PUT : la convention du projet (aucune route PUT, ni côté client ni dans l'outil de
    # contrat). Le geste reste idempotent : régler deux fois la même chose rend la même vue.
    responses={
        404: {"description": "Dossier inconnu ou hors périmètre"},
        422: {"description": "Aucun moment choisi, ou un moment non proposé"},
    },
)
def regler_mes_rappels(acces: AccesRequis, entreprise: str, demande: ReglageDemande) -> VueDesRappels:
    exiger_dossier(acces, Permission.REGLER_SES_RAPPELS, entreprise)
    reglages = reglages_des_rappels()
    try:
        preference = PreferenceDeRappels(
            actifs=demande.actifs, jalons=tuple(sorted(demande.jalons, reverse=True)), defini=True
        )
        preference.verifier(reglages)
    except ValueError as refus:
        raise HTTPException(status_code=422, detail=message_lisible(refus)) from refus
    atelier().journal.ajouter(
        horodatage=maintenant(),
        acteur=acces.compte,
        action="obligations.rappels_regles",
        objet_type=OBJET_REGLAGE,
        objet_id=acces.compte,
        apres={
            "dossier": entreprise,
            "actifs": preference.actifs,
            "jalons": list(preference.jalons),
        },
    )
    return _vue(acces.compte)


__all__ = ["routeur"]
