"""La clôture mensuelle d'un dossier (pas 107), sous `/comptabilite`.

─────────────────────────────────────────────────────────────────────────────────
UNE LECTURE, ET AUCUN GESTE NOUVEAU

L'écran de clôture (maquette « Parcours comptable », vue G) montre les points de contrôle du
mois, le mois en chiffres, le verrou, et le réviseur qui recevra le dossier. Le seul geste,
« Transmettre au réviseur », **existe déjà** (pas 102) : il appelle désormais
`points_de_cloture_du_mois`, et refuse tant qu'un point bloque. Il n'y a donc pas deux
chemins de transmission, dont un sans contrôle.

« Enregistrer et continuer plus tard » n'a rien à enregistrer côté serveur : chaque point se
calcule sur les faits, et redevient juste dès qu'on revient. Seul le commentaire au réviseur
est une saisie ; l'écran le garde en brouillon dans le navigateur.

QUI LIT

`LIRE_COMPTABILITE`, restreint au périmètre : qui lit la comptabilité du dossier voit où en est
sa clôture. Transmettre reste réservé à `SAISIR_ECRITURE`.
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel

from app.contextes.collecte.api import (
    DepotDemandesSql,
    DepotPiecesSql,
    demandes_en_memoire,
    pieces_en_memoire,
)
from app.contextes.comptabilite.adaptateurs.entrant.routes_http import (
    _dossier_du_portefeuille,
    _journaux,
    depot,
)
from app.contextes.comptabilite.adaptateurs.sortant.cloture_mensuelle_yaml import (
    charger_les_reglages_de_la_cloture_mensuelle,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_rapprochements import (
    depot_des_rapprochements,
)
from app.contextes.comptabilite.adaptateurs.sortant.depots_revues import depot_des_revues
from app.contextes.comptabilite.application.consequences_fiscales import montant_tva_rejetee
from app.contextes.comptabilite.application.revue import points_de_la_periode
from app.contextes.comptabilite.domaine.cloture_mensuelle import (
    ChiffresDuMois,
    DemandeOuverte,
    EcartEnSuspens,
    PeriodeVerrouillee,
    PieceDuMois,
    PointDeCloture,
    ReglagesDeLaClotureMensuelle,
    chiffrer_le_mois,
    periodes_verrouillees,
    points_de_cloture,
)
from app.contextes.comptabilite.domaine.entites import NatureJournal
from app.contextes.comptabilite.domaine.revue import StatutRevue
from app.contextes.transverse.api import (
    AccesRequis,
    Permission,
    Role,
    atelier,
    exiger_dossier,
    habilitations_actives,
    session_de_travail,
)
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant
from app.partage.locataire import courant

routeur = APIRouter(prefix="/comptabilite", tags=["Comptabilité · clôture mensuelle"])


def _reglages() -> ReglagesDeLaClotureMensuelle:
    return charger_les_reglages_de_la_cloture_mensuelle(configuration().dossier_referentiel)


def bornes_du_mois(mois: str) -> tuple[date, date]:
    debut = date(int(mois[:4]), int(mois[5:7]), 1)
    fin = (debut + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return debut, fin


def _pieces(entreprise: str) -> list[PieceDuMois]:
    session = session_de_travail()
    magasin = pieces_en_memoire() if session is None else DepotPiecesSql(session, courant())
    return [
        PieceDuMois(
            identifiant=p.identifiant,
            reference=p.reference_document,
            etat=p.etat.value,
            date=p.date_document or p.recue_le.date(),
            recue_le=p.recue_le.date(),
        )
        for p in magasin.du_dossier(entreprise)
    ]


def _demandes(entreprise: str) -> list[DemandeOuverte]:
    session = session_de_travail()
    magasin = demandes_en_memoire() if session is None else DepotDemandesSql(session, courant())
    return [
        DemandeOuverte(
            identifiant=d.identifiant,
            motif=d.motif,
            demandee_le=d.demandee_le,
            derniere_relance=max((jour for jour, _canal in d.relances), default=None),
        )
        for d in magasin.ouvertes(entreprise)
    ]


def _ecarts_en_suspens(entreprise: str) -> list[EcartEnSuspens]:
    # Par la surface publique de la conformité : l'arête comptabilite → conformite est déclarée.
    from app.contextes.conformite.api import StatutEcart, depot_des_ecarts

    return [
        EcartEnSuspens(
            identifiant=e.identifiant,
            reference_document=e.reference_document,
            code_regle=e.code_regle,
            severite=e.severite.value,
        )
        for e in depot_des_ecarts().toutes()
        if e.dossier == entreprise and e.statut is StatutEcart.EN_ATTENTE
    ]


def points_de_cloture_du_mois(
    entreprise: str, du: date, au: date, exercice: str
) -> tuple[list[PointDeCloture], ChiffresDuMois, ReglagesDeLaClotureMensuelle]:
    """Les points et les chiffres d'une période. **La** fonction que l'écran de clôture et la
    transmission appellent : ils ne peuvent pas diverger."""
    reglages = _reglages()
    ecritures = depot(entreprise).lister(exercice)
    banques = {
        j.code: j.compte_contrepartie
        for j in _journaux.charger()
        if j.nature is NatureJournal.BANQUE and j.compte_contrepartie
    }
    pieces = _pieces(entreprise)
    points = points_de_cloture(
        du=du,
        au=au,
        points_de_la_revue=points_de_la_periode(
            du,
            au,
            ecritures,
            banques,
            depot_des_rapprochements().du_dossier(entreprise),
            transmise=False,
        ),
        ecritures_de_l_exercice=ecritures,
        pieces=pieces,
        demandes=_demandes(entreprise),
        ecarts_en_suspens=_ecarts_en_suspens(entreprise),
        reglages=reglages,
    )
    chiffres = chiffrer_le_mois(
        du=du,
        au=au,
        ecritures_de_l_exercice=ecritures,
        pieces=pieces,
        tva_rejetee_par_ecriture=montant_tva_rejetee,
        reglages=reglages,
    )
    return points, chiffres, reglages


class RevueDuMois(BaseModel):
    identifiant: str
    statut: StatutRevue
    du: date
    au: date


class VueDeCloture(BaseModel):
    dossier: str
    denomination: str
    mois: str
    du: date
    au: date
    exercice: str
    #: Un mois qui n'est pas fini ne se transmet pas : transmettre le verrouillerait.
    mois_termine: bool
    points: list[PointDeCloture]
    traites: int
    #: Le compte affiché sur le bouton : « Transmettre au réviseur, 2 points restants ».
    bloquants_restants: int
    chiffres: ChiffresDuMois
    #: La revue qui couvre ce mois, s'il a déjà été transmis.
    revue: RevueDuMois | None
    verrou: PeriodeVerrouillee | None
    #: Les réviseurs habilités sur le dossier à ce jour : qui recevra le mois.
    reviseurs: list[str]
    transmissible: bool
    source_des_reglages: str


def _reviseurs(entreprise: str, jour: date) -> list[str]:
    boutique = atelier()
    noms = set()
    for h in habilitations_actives(boutique.habilitations.pour_dossier(entreprise), jour):
        if h.role is Role.REVISEUR:
            fiche = boutique.comptes.lire(h.compte)
            noms.add(f"{fiche.prenom} {fiche.nom}".strip() or h.compte)
    return sorted(noms)


@routeur.get(
    "/dossiers/{entreprise}/clotures/{mois}",
    summary="La clôture d'un mois : points de contrôle, chiffres, verrou",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_la_cloture_du_mois(
    acces: AccesRequis,
    entreprise: str,
    mois: str = Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="AAAA-MM"),
) -> VueDeCloture:
    exiger_dossier(acces, Permission.LIRE_COMPTABILITE, entreprise)
    dossier = _dossier_du_portefeuille(entreprise)
    if dossier is None:
        raise HTTPException(status_code=404, detail=f"dossier {entreprise} inconnu.")
    du, au = bornes_du_mois(mois)
    # L'exercice est celui de l'année du mois, comme à la transmission (pas 102).
    exercice = str(du.year)
    jour = maintenant().date()
    points, chiffres, reglages = points_de_cloture_du_mois(entreprise, du, au, exercice)
    revue = next(
        (r for r in depot_des_revues().du_dossier(entreprise) if r.du <= au and du <= r.au),
        None,
    )
    verrous = periodes_verrouillees(depot_des_revues().du_dossier(entreprise), reglages)
    verrou = next((p for p in verrous if p.du <= au and du <= p.au), None)
    bloquants = sum(1 for p in points if p.bloque)
    return VueDeCloture(
        dossier=entreprise,
        denomination=dossier.denomination,
        mois=mois,
        du=du,
        au=au,
        exercice=exercice,
        mois_termine=au < jour,
        points=points,
        traites=sum(1 for p in points if p.traite),
        bloquants_restants=bloquants,
        chiffres=chiffres,
        revue=RevueDuMois(
            identifiant=revue.identifiant, statut=revue.statut, du=revue.du, au=revue.au
        )
        if revue
        else None,
        verrou=verrou,
        reviseurs=_reviseurs(entreprise, jour),
        transmissible=revue is None and bloquants == 0 and au < jour,
        source_des_reglages=reglages.source,
    )


__all__ = ["bornes_du_mois", "points_de_cloture_du_mois", "routeur"]
