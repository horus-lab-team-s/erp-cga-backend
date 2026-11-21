"""Les retards du cabinet, par responsable (lot 4, 9 novembre 2026), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
CE QUE CETTE ROUTE RASSEMBLE

* les tâches comptables et déclaratives EN RETARD de chaque dossier du portefeuille — le
  plan de travail existant, relu sur tous les dossiers, avec leurs porteurs ;
* les dossiers de création restés au guichet unique au-delà du délai annoncé (le paramètre
  du référentiel ; sans lui, rien n'est signalé : un délai imaginaire ne relance personne).

Le commerce (demandes en souffrance, proformas impayées, rendez-vous, honoraires) vit dans la
souscription, que le pilotage ne lit pas : il a sa propre route, `/acquisition/retards`, et
la console réunit les deux.

QUI VOIT QUOI

* la direction (`LIRE_PILOTAGE`) voit tout le cabinet, regroupé par responsable ;
* qui tient des dossiers (`SAISIR_ECRITURE`) voit les retards de SON périmètre ;
* le chargé des formalités (`SUIVRE_FORMALITE`) voit les dossiers de création.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.creation_entreprise.api import (
    EtapeCreation,
    delais_depasses,
    depasse_le_delai_annonce,
    regle_des_delais_en_vigueur,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_plan_de_travail import (
    etat_du_dossier_pour_le_plan,
)
from app.contextes.pilotage.adaptateurs.entrant.sources import (
    depot_creations,
    depot_dossiers,
    sans_echouer,
)
from app.contextes.pilotage.adaptateurs.sortant.plan_de_travail_yaml import (
    charger_les_reglages_du_plan,
)
from app.contextes.pilotage.domaine.plan_de_travail import NatureDeTache, planifier
from app.contextes.pilotage.domaine.retards import CategorieDeRetard, Responsable, Retard
from app.contextes.referentiel.api import service_parametres
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    atelier,
    est_interne,
    exiger,
    habilitations_actives,
    restreindre,
)
from app.infrastructure.config import configuration
from app.partage.horloge import aujourd_hui

__all__ = ["routeur"]

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · retards"])


class RetardsDuCabinet(BaseModel):
    a_la_date: date
    #: Vrai pour la direction : tout le cabinet ; faux : le périmètre de la session.
    tout_le_cabinet: bool
    retards: list[Retard]


def _porteurs_par_dossier(nius: list[str], jour: date) -> dict[str, tuple[Responsable, ...]]:
    """Qui porte chaque dossier : les habilitations internes à portée explicite, actives.

    La même règle que la charge (`routes_charge._porteurs`) : une habilitation transverse a
    accès à tout et ne « porte » rien ; un adhérent n'est pas un responsable.
    """
    boutique = atelier()
    noms: dict[str, str] = {}
    porteurs: dict[str, tuple[Responsable, ...]] = {}
    for niu in nius:
        siens: dict[str, Responsable] = {}
        for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
            if h.portee is None or not est_interne(h.role):
                continue
            if h.compte not in noms:
                fiche = boutique.comptes.lire(h.compte)
                noms[h.compte] = f"{fiche.prenom} {fiche.nom}".strip() or h.compte
            siens[h.compte] = Responsable(compte=h.compte, nom=noms[h.compte])
        porteurs[niu] = tuple(sorted(siens.values(), key=lambda r: r.nom))
    return porteurs


def _retards_du_portefeuille(acces, jour: date, tout: bool) -> list[Retard]:
    dossiers = depot_dossiers().lister()
    if not tout:
        dossiers = restreindre(acces, dossiers, lambda d: d.niu)
    reglages = charger_les_reglages_du_plan(configuration().dossier_referentiel)
    etats = [
        e
        for e in (
            sans_echouer(lambda d=d: etat_du_dossier_pour_le_plan(d, jour)[0], None)
            for d in dossiers
        )
        if e is not None
    ]
    taches = [t for t in planifier(etats, reglages, jour) if t.en_retard and t.echeance]
    porteurs = _porteurs_par_dossier(sorted({t.dossier for t in taches}), jour)
    retards = []
    for t in taches:
        jours = (jour - t.echeance).days
        if jours < 1:
            continue
        retards.append(
            Retard(
                categorie=(
                    CategorieDeRetard.DECLARATION
                    if t.nature is NatureDeTache.PREPARER_DECLARATION
                    else CategorieDeRetard.COMPTABILITE
                ),
                dossier=t.dossier,
                denomination=t.denomination,
                libelle=f"{t.libelle} ({t.volume})" if t.volume else t.libelle,
                echeance=t.echeance,
                jours_de_retard=jours,
                lien=t.lien,
                responsables=porteurs.get(t.dossier, ()),
            )
        )
    return retards


def _retards_des_creations(jour: date) -> list[Retard]:
    parametres = service_parametres()
    try:
        delai = int(parametres.valeur_numerique("CFCE_DELAI_ANNONCE_JOURS", jour))
    except Exception:  # noqa: BLE001 — sans délai annoncé, aucun retard ne se déduit
        return []
    retards = _retards_des_delais_de_creation(jour)
    for dossier in depot_creations().lister(etape=EtapeCreation.DEPOT_CFCE):
        if not depasse_le_delai_annonce(dossier, parametres, jour):
            continue
        depose_le = dossier.depuis_le(EtapeCreation.DEPOT_CFCE)
        echeance = depose_le + timedelta(days=delai)
        retards.append(
            Retard(
                categorie=CategorieDeRetard.CREATION,
                dossier=dossier.reference,
                denomination=dossier.denomination_souhaitee,
                libelle=(
                    f"Au guichet unique depuis le {depose_le:%d/%m/%Y}, au-delà du délai "
                    f"annoncé de {delai} jours : relancer le CFCE"
                ),
                echeance=echeance,
                jours_de_retard=max((jour - echeance).days, 1),
                lien=f"/creation-entreprise/{dossier.reference}",
            )
        )
    return retards


def _retards_des_delais_de_creation(jour: date) -> list[Retard]:
    """Lot 4 : statuts à rédiger ou à valider, originaux à retirer, au-delà des délais fixés
    par la direction. Sans délai fixé, rien (voir `creation_entreprise/domaine/delais.py`)."""
    regle = regle_des_delais_en_vigueur()
    retards = []
    for dossier in depot_creations().lister():
        for depasse in delais_depasses(dossier, regle, jour):
            retards.append(
                Retard(
                    categorie=CategorieDeRetard.CREATION,
                    dossier=dossier.reference,
                    denomination=dossier.denomination_souhaitee,
                    libelle=(
                        f"{depasse.libelle} (délai du client)"
                        if depasse.du_fait_du_client
                        else f"{depasse.libelle} (délai du cabinet)"
                    ),
                    echeance=depasse.echeance,
                    jours_de_retard=depasse.jours_de_retard,
                    lien=f"/creation-entreprise/{dossier.reference}",
                )
            )
    return retards


@routeur.get("/retards", summary="Les retards du cabinet, en rouge, par responsable")
def lire_les_retards(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> RetardsDuCabinet:
    tout = acces.detient(Permission.LIRE_PILOTAGE)
    tient = acces.detient(Permission.SAISIR_ECRITURE)
    formalites = acces.detient(Permission.SUIVRE_FORMALITE)
    if not (tient or formalites):
        # Ni porteur de dossiers ni chargé des formalités : la direction seule, et le refus
        # nomme sa permission.
        exiger(acces, Permission.LIRE_PILOTAGE)
    jour = a_la_date or aujourd_hui()
    retards: list[Retard] = []
    if tout or tient:
        retards += _retards_du_portefeuille(acces, jour, tout)
    if tout or formalites:
        retards += sans_echouer(lambda: _retards_des_creations(jour), [])
    retards.sort(key=lambda r: (-r.jours_de_retard, r.denomination))
    return RetardsDuCabinet(a_la_date=jour, tout_le_cabinet=tout, retards=retards)
