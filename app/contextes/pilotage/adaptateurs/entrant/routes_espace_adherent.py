"""L'accueil de l'adhérent : le mois, ce qui manque, ce qui est arrivé (pas 112), sous `/pilotage`.

─────────────────────────────────────────────────────────────────────────────────
UNE ROUTE, ET POURQUOI AU PILOTAGE

* `GET /pilotage/dossiers/{niu}/mon-mois` : le bandeau (ce qui manque, ou que tout est complet),
  les pièces demandées dans les mots de l'adhérent, le nombre de justificatifs du mois, et les
  achats du mois **quand le mois est revu**.

Le bandeau croise quatre contextes : les demandes et les pièces (collecte), les attentes déduites
(pas 111, qui lisent la comptabilité et les obligations), le verrou des mois revus (comptabilité).
Le pilotage lit tous les contextes et n'est lu par aucun : c'est sa place. Une collecte qui lirait
la comptabilité pour son accueil créerait une arête que le registre refuse.

⚠️ CE QUE L'ADHÉRENT NE LIT PAS

* **Les attentes déduites.** Elles décident de l'état (« le cabinet vérifie » plutôt que
  « complet ») mais ne sont pas listées : tant que le cabinet ne les a pas demandées, ce sont des
  présomptions. Le libellé d'une série interrompue (« Facture de la QUINCAILLERIE DU WOURI,
  n° 13 manquant ») serait une question que le comptable n'a pas choisi de poser.
* **Les achats d'un mois en cours.** « Il ne voit pas la comptabilité tant qu'elle est en cours
  d'établissement » (rôle ADHERENT) : le montant n'est rendu que pour un mois verrouillé par la
  revue (pas 107), avec la date du verrou. Sinon `achats` est vide, et l'écran dit pourquoi.

`LIRE_DOSSIER`, sur le dossier : l'adhérent, et tout collaborateur du dossier (le chargé de
clientèle au téléphone voit ce que l'adhérent voit).
─────────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contextes.collecte.api import mois_de_la_piece, reglages_des_reponses, texte_de_la_reponse
from app.contextes.comptabilite.api import EtatEcriture, periodes_verrouillees_du_dossier
from app.contextes.pilotage.adaptateurs.entrant.routes_http import (
    _depot_demandes,
    _depot_pieces,
    _ecritures,
    _sans_echouer,
)
from app.contextes.pilotage.adaptateurs.entrant.routes_relance import (
    _bornes,
    _dossier,
    _relever_les_attentes,
    _reglages as _reglages_des_pieces_manquantes,
)
from app.contextes.pilotage.adaptateurs.sortant.espace_adherent_yaml import (
    charger_les_reglages_de_l_espace_adherent,
)
from app.contextes.pilotage.domaine.mois_de_l_adherent import (
    AttendueDeLAdherent,
    Bandeau,
    ReglagesDeLEspaceAdherent,
    achats_du_mois,
    etat_du_mois,
    rendre_le_bandeau,
)
from app.contextes.pilotage.domaine.pieces_manquantes import nom_du_mois
from app.contextes.transverse.api import AccesRequis, Permission, exiger_dossier
from app.infrastructure.config import configuration
from app.partage.horloge import maintenant

routeur = APIRouter(prefix="/pilotage", tags=["Pilotage · accueil de l'adhérent"])


def _reglages() -> ReglagesDeLEspaceAdherent:
    return charger_les_reglages_de_l_espace_adherent(configuration().dossier_referentiel)


class VueDuMois(BaseModel):
    dossier: str
    denomination: str
    #: `AAAA-MM`, et en toutes lettres (« juillet 2026 »).
    mois: str
    nom_du_mois: str
    bandeau: Bandeau
    #: La plus proche des dates demandées, s'il y en a une.
    date_limite: date | None
    #: Les pièces demandées, encore ouvertes : ce qui demande un geste d'abord.
    attendues: list[AttendueDeLAdherent]
    justificatifs_du_mois: int
    #: Rendus seulement pour un mois verrouillé par la revue ; sinon vides.
    achats: Decimal | None
    achats_arretes_le: datetime | None
    #: Le mois qui suit celui-ci, pour « voir le mois suivant » ; absent pour le mois observé.
    mois_suivant: str | None
    mois_precedent: str


def _mois_voisin(mois: str, sens: int) -> str:
    du, au = _bornes(mois)
    jour = au + timedelta(days=1) if sens > 0 else du - timedelta(days=1)
    return f"{jour:%Y-%m}"


@routeur.get(
    "/dossiers/{niu}/mon-mois",
    summary="Le mois de l'adhérent : ce qui manque, ou que tout est complet",
    responses={404: {"description": "Dossier inconnu ou hors périmètre"}},
)
def lire_mon_mois(
    acces: AccesRequis,
    niu: str,
    mois: str | None = Query(
        None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Sans valeur : le mois précédent, dont la déclaration se prépare",
    ),
) -> VueDuMois:
    exiger_dossier(acces, Permission.LIRE_DOSSIER, niu)
    reglages = _reglages()
    dossier = _dossier(niu)
    jour = maintenant().date()
    observe = f"{(jour.replace(day=1) - timedelta(days=1)):%Y-%m}"
    mois = mois or observe
    du, au = _bornes(mois)

    reponses = reglages_des_reponses()
    # ⚠️ Toutes les demandes ouvertes, pas celles du seul mois : une rectificative de juin encore
    # ouverte en août manque toujours, et l'adhérent doit la voir sur son accueil.
    ouvertes = _sans_echouer(lambda: _depot_demandes().ouvertes(niu), [])
    attendues = sorted(
        (
            AttendueDeLAdherent(
                demande=d.identifiant,
                libelle=d.motif,
                type_attendu=d.type_attendu.value,
                demandee_le=d.demandee_le,
                attendue_pour=d.attendue_pour,
                en_retard=d.en_retard(jour),
                bloquante=d.bloquante,
                a_corriger=d.piece_a_rectifier is not None,
                piece_a_rectifier=d.piece_a_rectifier,
                reponse=(
                    texte_de_la_reponse(d.derniere_reponse, reponses)
                    if d.derniere_reponse
                    else None
                ),
                reponse_le=d.derniere_reponse.le if d.derniere_reponse else None,
            )
            for d in ouvertes
        ),
        key=lambda a: (
            not a.en_retard,
            not a.bloquante,
            a.attendue_pour or date.max,
            a.demande,
        ),
    )
    # Les attentes que le cabinet n'a pas encore demandées. ⚠️ Aujourd'hui, le filtre ne change pas
    # l'état : une attente rattachée à une demande suppose la demande ouverte, et l'état est alors
    # déjà « à envoyer » (la batterie de mutations du pas 112 l'a montré). Il est gardé parce qu'il
    # dit ce que l'on compte, et qu'un état futur (« demandé, et répondu ») le rendrait décisif.
    deduites = [
        a
        for a in _relever_les_attentes(dossier, du, au, _reglages_des_pieces_manquantes())
        if a.demande is None
    ]
    pieces = _sans_echouer(lambda: _depot_pieces().du_dossier(niu), [])
    du_mois = sum(1 for p in pieces if mois_de_la_piece(p) == mois)

    etat = etat_du_mois(
        attendues=attendues,
        attentes_deduites_sans_demande=len(deduites),
        pieces_du_mois=du_mois,
    )
    bandeau, limite = rendre_le_bandeau(
        etat, attendues=attendues, nom_du_mois=nom_du_mois(du), jour=jour, reglages=reglages
    )

    achats, arretes_le = None, None
    verrou = next(
        (
            p
            for p in _sans_echouer(lambda: periodes_verrouillees_du_dossier(niu), [])
            if p.du <= du and au <= p.au
        ),
        None,
    )
    if verrou is not None:
        ecritures = _sans_echouer(lambda: _ecritures(niu).toutes(str(du.year)), [])
        achats = achats_du_mois(
            [
                (ligne.compte, ligne.au_debit, ligne.montant)
                for e in ecritures
                if e.etat is EtatEcriture.VALIDEE and du <= e.date_operation <= au
                for ligne in e.lignes
            ],
            reglages.racines_d_achats,
        )
        arretes_le = verrou.depuis

    return VueDuMois(
        dossier=niu,
        denomination=dossier.denomination,
        mois=mois,
        nom_du_mois=nom_du_mois(du),
        bandeau=bandeau,
        date_limite=limite,
        attendues=attendues,
        justificatifs_du_mois=du_mois,
        achats=achats,
        achats_arretes_le=arretes_le,
        mois_suivant=None if mois >= observe else _mois_voisin(mois, +1),
        mois_precedent=_mois_voisin(mois, -1),
    )


__all__ = ["routeur"]
