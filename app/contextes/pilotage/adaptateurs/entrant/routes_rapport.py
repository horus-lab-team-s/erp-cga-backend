"""Le rapport mensuel de la direction (pas 106), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
LES SECTIONS SONT LES LECTURES DES ÉCRANS, APPELÉES TELLES QUELLES

    RISQUE              lire_tableau_de_bord             (écran /pilotage)
    CHARGE              lire_la_charge_et_la_production  (écran /pilotage/charge)
    DEROGATIONS         lire_le_journal_des_derogations  (écran /conformite, filtré sur le mois)
    QUALITE_DES_REGLES  mesurer_la_qualite_des_regles    (écran /conformite/qualite, sur le mois)
    DECISIONS           les décisions de direction prises dans le mois

Les deux lectures de la conformité passent par son `api.py`, où elles ont été déplacées au
pas 106 pour cette raison : l'écran et le rapport appellent la même fonction.

PERMISSIONS

`LIRE_PILOTAGE` **et** `LIRE_AUDIT` : le rapport contient le journal des dérogations, que le
pilotage seul ne permet pas de lire. La direction détient les deux.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contextes.conformite.api import (
    lire_le_journal_des_derogations,
    mesurer_la_qualite_des_regles,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_charge import (
    lire_la_charge_et_la_production,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    _aujourd_hui,
    _depot_dossiers,
    lire_tableau_de_bord,
)
from app.contextes.pilotage.adaptateurs.sortant.depots_decisions import (
    depot_des_decisions_de_direction,
)
from app.contextes.pilotage.adaptateurs.sortant.depots_rapports import depot_des_rapports
from app.contextes.pilotage.adaptateurs.sortant.rapport_mensuel_yaml import (
    charger_les_reglages_du_rapport,
)
from app.contextes.pilotage.application.rapport_mensuel import (
    RapportRefuse,
    generer_le_rapport,
)
from app.contextes.pilotage.domaine.decisions import DecisionDeDirection
from app.contextes.pilotage.domaine.rapport_mensuel import RapportMensuel, SectionDuRapport
from app.contextes.transverse.api import Acces, AccesRequis, Permission, atelier, exiger
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · rapport mensuel"])


class DecisionsDuMois(BaseModel):
    decisions: list[DecisionDeDirection]


class ResumeDeRapport(BaseModel):
    identifiant: str
    mois: str
    version: int
    a_la_date: date
    genere_le: str
    genere_par_nom: str
    empreinte: str
    integre: bool


class DemandeDeRapport(BaseModel):
    mois: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


def _lectures(acces: Acces):
    def risque(du, au, a_la_date):
        return lire_tableau_de_bord(acces, a_la_date=a_la_date, exercice=str(a_la_date.year))

    def charge(du, au, a_la_date):
        return lire_la_charge_et_la_production(acces, a_la_date=a_la_date)

    def derogations(du, au, a_la_date):
        return lire_le_journal_des_derogations(acces, du=du, au=au)

    def qualite(du, au, a_la_date):
        return mesurer_la_qualite_des_regles(acces, du, au)

    def decisions(du, au, a_la_date):
        depot = depot_des_decisions_de_direction()
        prises = [
            d
            for dossier in _depot_dossiers().lister()
            for d in depot.du_dossier(dossier.niu)
            if du <= d.prise_le.date() <= au
        ]
        return DecisionsDuMois(decisions=sorted(prises, key=lambda d: (d.prise_le, d.identifiant)))

    return {
        SectionDuRapport.RISQUE: risque,
        SectionDuRapport.CHARGE: charge,
        SectionDuRapport.DEROGATIONS: derogations,
        SectionDuRapport.QUALITE_DES_REGLES: qualite,
        SectionDuRapport.DECISIONS: decisions,
    }


@routeur.post(
    "/rapports-mensuels",
    summary="Générer et archiver le rapport mensuel d'un mois",
    status_code=201,
)
def generer(acces: AccesRequis, demande: DemandeDeRapport) -> RapportMensuel:
    exiger(acces, Permission.LIRE_PILOTAGE)
    # Aucun rôle ne détient aujourd'hui LIRE_PILOTAGE sans LIRE_AUDIT : ce second contrôle ne
    # refuse personne de plus. Il reste, pour le jour où un rôle de lecture du pilotage naîtra
    # sans l'audit (une mutation qui le retire survit, et c'est noté au journal du pas 106).
    exiger(acces, Permission.LIRE_AUDIT)
    try:
        rapport = generer_le_rapport(
            mois=demande.mois,
            jour=_aujourd_hui(),
            lectures=_lectures(acces),
            reglages=charger_les_reglages_du_rapport(configuration().dossier_referentiel),
            par=acces.compte,
            par_nom=acces.nom_complet,
            le=maintenant(),
            depot=depot_des_rapports(),
        )
    except RapportRefuse as refus:
        raise HTTPException(status_code=422, detail=str(refus)) from refus
    # ⚠️ L'empreinte part au journal d'audit, chaîné : un rapport modifié en base ne pourrait
    # plus s'y accorder, même si l'on recalculait l'empreinte du document.
    #
    # ⚠️ Sous la clé `sha256_du_contenu`, **pas** `empreinte` : le journal expurge toute clé
    # nommée `empreinte` (c'est ainsi qu'est rangée l'empreinte d'un mot de passe), et
    # l'expurgation a lieu **avant** l'écriture. La valeur n'aurait jamais été enregistrée.
    # Trouvé par le test ; la liste d'expurgation, garde-fou de sécurité, n'est pas assouplie.
    atelier().journal.ajouter(
        horodatage=rapport.genere_le,
        acteur=acces.compte,
        action="pilotage.rapport_genere",
        objet_type="rapport_mensuel",
        objet_id=rapport.identifiant,
        apres={
            "mois": rapport.mois,
            "version": rapport.version,
            "sha256_du_contenu": rapport.empreinte,
            "par": acces.nom_complet,
        },
    )
    return rapport


@routeur.get("/rapports-mensuels", summary="Les rapports mensuels archivés")
def lister(acces: AccesRequis) -> list[ResumeDeRapport]:
    exiger(acces, Permission.LIRE_PILOTAGE)
    exiger(acces, Permission.LIRE_AUDIT)
    return [
        ResumeDeRapport(
            identifiant=r.identifiant,
            mois=r.mois,
            version=r.version,
            a_la_date=r.a_la_date,
            genere_le=r.genere_le.isoformat(),
            genere_par_nom=r.genere_par_nom,
            empreinte=r.empreinte,
            integre=r.integre,
        )
        for r in depot_des_rapports().tous()
    ]


@routeur.get(
    "/rapports-mensuels/{identifiant}",
    summary="Un rapport mensuel archivé, tel qu'il a été généré",
)
def lire(acces: AccesRequis, identifiant: str) -> RapportMensuel:
    """Le rapport **figé** : rien n'est recalculé à la lecture, sauf l'empreinte, pour dire si
    le contenu relu est toujours celui qui a été archivé."""
    exiger(acces, Permission.LIRE_PILOTAGE)
    exiger(acces, Permission.LIRE_AUDIT)
    rapport = depot_des_rapports().trouver(identifiant)
    if rapport is None:
        raise HTTPException(status_code=404, detail=f"rapport « {identifiant} » inconnu.")
    return rapport


__all__ = ["routeur"]
