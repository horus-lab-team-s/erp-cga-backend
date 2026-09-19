"""Charge et production (pas 105), sous `/pilotage`.

`LIRE_PILOTAGE`, la direction seule, comme le tableau de bord : la charge porte un jugement
d'affectation. La validation d'une proposition n'est pas ici : c'est la réaffectation du
contexte K (`POST /transverse/dossiers/{niu}/reaffectation`, `AFFECTER_DOSSIER`). Le pilotage
propose, il ne modifie aucun accès.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.comptabilite.api import depot_des_revues
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    _aujourd_hui,
    _depot_dossiers,
    _depot_pieces,
    _echeancier,
    _ecritures,
    _sans_echouer,
)
from app.contextes.pilotage.adaptateurs.sortant.charge_yaml import (
    charger_les_reglages_de_la_charge,
)
from app.contextes.pilotage.domaine.charge_et_production import (
    DossierPourLaCharge,
    LigneDeCharge,
    PorteurDeDossiers,
    PropositionDeReaffectation,
    ReglagesDeLaCharge,
    mesurer_la_charge,
    proposer_des_reaffectations,
)
from app.contextes.transverse.api import (
    ROLES_INTERNES,
    AccesRequis,
    Permission,
    atelier,
    exiger,
    habilitations_actives,
)
from app.infrastructure.config import configuration

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · charge et production"])


class ChargeEtProduction(BaseModel):
    a_la_date: date
    #: Le mois mesuré : du premier au dernier jour du mois de la lecture.
    mois_du: date
    mois_au: date
    dossiers: int
    echeances_du_mois: int
    collaborateurs: list[LigneDeCharge]
    propositions: list[PropositionDeReaffectation]
    reglages: ReglagesDeLaCharge


def _dossier(entreprise, jour: date, debut: date, fin: date) -> DossierPourLaCharge:
    niu = entreprise.niu
    pieces = _sans_echouer(lambda: _depot_pieces().du_dossier(niu), [])
    echeancier = _sans_echouer(lambda: _echeancier(entreprise, str(jour.year), jour), [])
    ecritures = _sans_echouer(lambda: _ecritures(niu).toutes(str(jour.year)), [])
    revues = _sans_echouer(lambda: depot_des_revues().du_dossier(niu), [])
    return DossierPourLaCharge(
        niu=niu,
        denomination=entreprise.denomination,
        pieces_en_attente=sum(1 for p in pieces if p.en_attente_de_traitement),
        echeances_du_mois=sum(
            1 for i in echeancier if not i.deposee and debut <= i.echeance <= fin
        ),
        retards=sum(1 for i in echeancier if i.en_retard(jour)),
        # ⚠️ Reçues **et** traitées ce mois : la pièce ne retient pas la date de son traitement.
        pieces_traitees_du_mois=sum(
            1 for p in pieces if p.traitee and debut <= p.recue_le.date() <= fin
        ),
        ecritures_du_mois=sum(
            1 for e in ecritures if e.validee_le is not None and debut <= e.validee_le.date() <= fin
        ),
        reprises_du_mois=sum(1 for r in revues for m in r.remarques if debut <= m.le.date() <= fin),
    )


def _porteurs(nius: list[str], jour: date) -> list[PorteurDeDossiers]:
    """Les habilitations **à portée explicite** actives au jour, avec les dossiers portés.

    Même règle que `_qui_suit_quoi` : une habilitation transverse a accès à tout, elle ne
    « porte » rien. Un porteur par habilitation : un compte qui cumule deux rôles à portée
    porte deux charges distinctes, chacune rapportée à la capacité de son rôle.
    """
    boutique = atelier()
    par_habilitation: dict[str, tuple] = {}
    for niu in nius:
        for h in habilitations_actives(boutique.habilitations.pour_dossier(niu), jour):
            # Ni transverse (accès à tout, ne porte rien), ni adhérent ou inspecteur (portée
            # explicite, mais aucun travail du cabinet) : le défaut trouvé au pas 105.
            if h.portee is None or h.role not in ROLES_INTERNES:
                continue
            compte, role, portes = par_habilitation.get(h.identifiant, (h.compte, h.role.value, []))
            portes.append(niu)
            par_habilitation[h.identifiant] = (compte, role, portes)
    porteurs = []
    for identifiant, (compte, role, portes) in par_habilitation.items():
        fiche = boutique.comptes.lire(compte)
        porteurs.append(
            PorteurDeDossiers(
                compte=compte,
                nom=f"{fiche.prenom} {fiche.nom}".strip() or compte,
                role=role,
                habilitation=identifiant,
                dossiers=tuple(sorted(portes)),
            )
        )
    return sorted(porteurs, key=lambda p: (p.role, p.nom))


@routeur.get(
    "/charge-et-production",
    summary="Qui est saturé, ce qui avance, ce qu'il faudrait réaffecter",
)
def lire_la_charge_et_la_production(
    acces: AccesRequis,
    a_la_date: date | None = Query(None, description="Défaut : aujourd'hui"),
) -> ChargeEtProduction:
    exiger(acces, Permission.LIRE_PILOTAGE)
    jour = a_la_date or _aujourd_hui()
    debut = jour.replace(day=1)
    fin = (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    reglages = charger_les_reglages_de_la_charge(configuration().dossier_referentiel)
    entreprises = _depot_dossiers().lister()
    dossiers = {e.niu: _dossier(e, jour, debut, fin) for e in entreprises}
    porteurs = _porteurs(list(dossiers), jour)
    return ChargeEtProduction(
        a_la_date=jour,
        mois_du=debut,
        mois_au=fin,
        dossiers=len(dossiers),
        echeances_du_mois=sum(d.echeances_du_mois for d in dossiers.values()),
        collaborateurs=mesurer_la_charge(porteurs, dossiers, reglages),
        propositions=proposer_des_reaffectations(porteurs, dossiers, reglages),
        reglages=reglages,
    )


__all__ = ["routeur"]
