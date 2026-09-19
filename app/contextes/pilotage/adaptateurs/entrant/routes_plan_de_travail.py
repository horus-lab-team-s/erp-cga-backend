"""Le plan de travail d'un collaborateur (pas 104), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
POURQUOI DANS LE PILOTAGE

Le plan agrège cinq contextes (portefeuille, collecte, obligations, comptabilité et ses
revues), exactement comme le tableau de bord de la direction, et il réutilise ses relevés :
les pièces en souffrance, l'échéancier. Le pilotage est le seul contexte qui lit tout le
monde et que personne ne lit ; y ranger le plan évite d'ouvrir de nouvelles arêtes.

⚠️ LA PERMISSION N'EST PAS CELLE DU PILOTAGE

Le tableau de bord de la direction exige `LIRE_PILOTAGE`. Le plan de travail est celui de
qui **tient** des dossiers : `SAISIR_ECRITURE` (comptable, réviseur). Et il ne porte que sur
les dossiers du périmètre de la session : « le comptable ne voit que ses dossiers ».
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.comptabilite.api import (
    JOURNAUX_CABINET,
    NatureJournal,
    StatutRapprochement,
    StatutRemarque,
    StatutRevue,
    depot_des_rapprochements,
    depot_des_revues,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    _aujourd_hui,
    _depot_demandes,
    _depot_dossiers,
    _depot_pieces,
    _echeancier,
    _ecritures,
    _sans_echouer,
)
from app.contextes.pilotage.adaptateurs.sortant.plan_de_travail_yaml import (
    charger_les_reglages_du_plan,
)
from app.contextes.pilotage.domaine.plan_de_travail import (
    DeclarationAPreparer,
    EtatDuDossierPourLePlan,
    ReglagesDuPlan,
    Tache,
    planifier,
)
from app.contextes.transverse.api import AccesRequis, Permission, exiger, restreindre
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · plan de travail"])


class LigneDeDossier(BaseModel):
    """« Mes dossiers » : où en est chacun, d'un coup d'œil."""

    niu: str
    denomination: str
    taches: int
    prochaine_echeance: date | None
    #: L'état de la revue du mois précédent : NON_TRANSMIS, TRANSMISE, RENVOYEE, VALIDEE, ou
    #: SANS_ECRITURE quand le mois n'a rien à réviser.
    revue_du_mois_precedent: str


class PlanDeTravail(BaseModel):
    a_la_date: date
    taches: list[Tache]
    mes_dossiers: list[LigneDeDossier]
    #: Les passages de relais : mois qui attendent le réviseur, mois qui reviennent.
    chez_le_reviseur: int
    renvoyes: int
    reglages: ReglagesDuPlan


def _etat_du_dossier(dossier, jour: date) -> tuple[EtatDuDossierPourLePlan, str]:
    niu = dossier.niu
    fin_precedent = jour.replace(day=1) - timedelta(days=1)
    debut_precedent = fin_precedent.replace(day=1)

    pieces = tuple(
        (p.identifiant, p.recue_le.date())
        for p in _sans_echouer(lambda: _depot_pieces().du_dossier(niu), [])
        if p.en_attente_de_traitement
    )
    demandes = tuple(
        d.identifiant
        for d in _sans_echouer(lambda: _depot_demandes().ouvertes(niu), [])
        if d.en_retard(jour) or d.a_escalader(jour)
    )
    declarations = tuple(
        DeclarationAPreparer(
            code=i.code_obligation,
            libelle=i.libelle,
            periode_debut=i.periode_debut,
            echeance=i.echeance,
        )
        for i in _sans_echouer(lambda: _echeancier(dossier, str(jour.year), jour), [])
        if not i.deposee
    )

    ecritures = _sans_echouer(lambda: _ecritures(niu).toutes(str(fin_precedent.year)), [])
    du_mois = [e for e in ecritures if debut_precedent <= e.date_operation <= fin_precedent]
    rapprochements = _sans_echouer(lambda: depot_des_rapprochements().du_dossier(niu), [])
    releves = tuple(
        j.code
        for j in JOURNAUX_CABINET
        if j.nature is NatureJournal.BANQUE
        and j.compte_contrepartie
        and any(l_.compte.startswith(j.compte_contrepartie) for e in du_mois for l_ in e.lignes)
        and not any(
            r.journal == j.code
            and r.statut is StatutRapprochement.VALIDE
            and r.du <= fin_precedent <= r.au
            for r in rapprochements
        )
    )

    revues = _sans_echouer(lambda: depot_des_revues().du_dossier(niu), [])
    renvoyee = next((r for r in revues if r.statut is StatutRevue.RENVOYEE), None)
    du_precedent = next(
        (r for r in revues if r.du <= fin_precedent and debut_precedent <= r.au), None
    )
    etat_revue = (
        "SANS_ECRITURE"
        if not du_mois
        else du_precedent.statut.value
        if du_precedent
        else "NON_TRANSMIS"
    )
    return (
        EtatDuDossierPourLePlan(
            niu=niu,
            denomination=dossier.denomination,
            pieces_a_traiter=pieces,
            demandes_echues=demandes,
            declarations=declarations,
            releves_a_rapprocher=releves,
            revue_renvoyee=None
            if renvoyee is None
            else (renvoyee.identifiant, renvoyee.compter(StatutRemarque.OUVERTE)),
            mois_precedent_a_transmettre=etat_revue == "NON_TRANSMIS",
        ),
        etat_revue,
    )


@routeur.get("/plan-de-travail", summary="Par quoi commencer : les tâches de mes dossiers")
def lire_le_plan_de_travail(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> PlanDeTravail:
    exiger(acces, Permission.SAISIR_ECRITURE)
    jour = a_la_date or _aujourd_hui()
    reglages = charger_les_reglages_du_plan(configuration().dossier_referentiel)
    dossiers = restreindre(acces, _depot_dossiers().lister(), lambda d: d.niu)
    etats = [_etat_du_dossier(d, jour) for d in dossiers]
    taches = planifier([e for e, _ in etats], reglages, jour)
    lignes = []
    for etat, revue in etats:
        siennes = [t for t in taches if t.dossier == etat.niu]
        echeances = [t.echeance for t in siennes if t.echeance is not None]
        lignes.append(
            LigneDeDossier(
                niu=etat.niu,
                denomination=etat.denomination,
                taches=len(siennes),
                prochaine_echeance=min(echeances) if echeances else None,
                revue_du_mois_precedent=revue,
            )
        )
    lignes.sort(key=lambda l_: (-l_.taches, l_.denomination))
    return PlanDeTravail(
        a_la_date=jour,
        taches=taches,
        mes_dossiers=lignes,
        chez_le_reviseur=sum(1 for l_ in lignes if l_.revue_du_mois_precedent == "TRANSMISE"),
        renvoyes=sum(1 for e, _ in etats if e.revue_renvoyee is not None),
        reglages=reglages,
    )


__all__ = ["routeur"]
